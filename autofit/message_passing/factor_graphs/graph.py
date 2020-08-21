from collections import Counter, defaultdict
from typing import Tuple, Dict, Collection, List

import numpy as np

from autofit.message_passing.factor_graphs import FactorNode, FactorValue, AbstractNode
from autofit.message_passing.factor_graphs.factor import Factor
from autofit.message_passing.factor_graphs.variable import Variable, Plate
from autofit.message_passing.utils import add_arrays


class DeterministicFactorNode(FactorNode):
    def __init__(
            self,
            factor: Factor,
            variable: Variable,
            *args: Variable,
            **kwargs: Variable
    ):
        """
        A deterministic factor is used to convert a function f(g(x)) to f(y)g(x) (integrating over y wit
        a delta function) so that it can be represented in a factor graph.

        Parameters
        ----------
        factor
            The original factor to which the deterministic factor is associated
        variable
            The deterministic factor used
        args
            Variables for the original factor
        kwargs
            Variables for the original factor
        """
        super().__init__(
            factor,
            *args,
            **kwargs
        )
        self._deterministic_variables = {
            variable.name: variable
        }

    def __call__(
            self,
            *args: np.ndarray,
            **kwargs: np.ndarray
    ) -> FactorValue:
        """
        Call this factor with a set of arguments

        Parameters
        ----------
        args
            Positional arguments for the underlying factor
        kwargs
            Keyword arguments for the underlying factor

        Returns
        -------
        An object encapsulating the value for the factor
        """
        res = self._call_factor(*args, **kwargs)
        shape = self._function_shape(*args, **kwargs)
        shift = len(shape) - self.ndim
        plate_dim = dict(zip(self.plates, shape[shift:]))

        det_shapes = {
            v: shape[:shift] + tuple(
                plate_dim[p] for v in self.deterministic_variables.values()
                for p in v.plates)
            for v in self.deterministic_variables
        }

        if not (isinstance(res, tuple) or self.n_deterministic > 1):
            res = res,

        log_val = 0. if shape == () else np.zeros(np.ones_like(shape))
        det_vals = {
            k: np.reshape(val, det_shapes[k])
            if det_shapes[k]
            else val
            for k, val
            in zip(self._deterministic_variables, res)
        }
        return FactorValue(log_val, det_vals)

    def __repr__(self) -> str:
        factor_str = super().__repr__()
        var_str = ", ".join(self._deterministic_variables)
        return f"({factor_str} == ({var_str}))"


class FactorGraph(AbstractNode):
    def __init__(
            self,
            factors: Collection[FactorNode],
    ):
        """
        A graph relating factors

        Parameters
        ----------
        factors
            Nodes wrapping individual factors in a model
        """
        self._name = ".".join(f.name for f in factors)

        self._factors = tuple(factors)

        self._variables = dict()
        self._deterministic_variables = dict()

        for f in self._factors:
            self._deterministic_variables.update(
                f.deterministic_variables
            )
            self._variables.update(
                f.variables
            )

        self._factor_all_variables = {
            f: f.all_variables for f in self._factors}

        self._call_sequence = self._get_call_sequence()

        self._validate()

        factor_args = [
            factor._args
            for factor
            in self.factors
        ]

        max_len = min(map(len, factor_args))
        _args = tuple(
            factor_args[0][i] for i in range(max_len)
            if len(set(arg[i] for arg in factor_args)) == 1
        )
        _kwargs = {
            k: variable
            for k, variable
            in self.variables.items()
            if variable not in _args
        }

        super().__init__(
            *_args,
            **_kwargs
        )

    @property
    def deterministic_variables(self):
        return self._deterministic_variables

    def broadcast_plates(
            self,
            plates: Collection[Plate],
            value: np.ndarray
    ) -> np.ndarray:
        """
        Extract the indices of a collection of plates then match
        the shape of the data to that shape.

        Parameters
        ----------
        plates
            Plates representing the dimensions of some factor
        value
            A value to broadcast

        Returns
        -------
        The value reshaped to match the plates
        """
        return self._broadcast(self._match_plates(plates), value)

    @property
    def name(self):
        return self._name

    def _validate(self):
        """
        Raises
        ------
        If there is an inconsistency with this graph
        """
        det_var_counts = ", ".join(
            v for v, c in Counter(
                v for f in self.factors
                for v in f.deterministic_variables).items()
            if c > 1)
        if det_var_counts:
            raise ValueError(
                "Improper FactorGraph, "
                f"Deterministic variables {det_var_counts} appear in "
                "multiple factors"
            )

    @property
    def variables(self):
        return {
            v: self._variables[v] for v in
            (self._variables.keys() - self._deterministic_variables.keys())
        }

    def _get_call_sequence(self) -> List[List[FactorNode]]:
        """
        Compute the order in which the factors must be evaluated. This is done by checking whether
        all variables required to call a factor are present in the set of variables encapsulated
        by all factors, not including deterministic variables.

        Deterministic variables must be computed before the dependent factors can be computed.
        """
        call_sets = defaultdict(list)
        for factor in self.factors:
            missing_vars = frozenset(factor.variables_difference(**self.variables))
            call_sets[missing_vars].append(factor)

        call_sequence = []
        while call_sets:
            # the factors that can be evaluated have no missing variables
            factors = call_sets.pop(frozenset(()))
            # if there's a KeyError then the FactorGraph is improper
            calls = []
            new_variables = {}
            for factor in factors:
                if isinstance(factor, DeterministicFactorNode):
                    det_vars = factor.deterministic_variables
                else:
                    det_vars = {}

                calls.append(factor)
                new_variables.update(det_vars)

            call_sequence.append(calls)

            # update to include newly calculated factors
            for missing in list(call_sets.keys()):
                if missing.intersection(new_variables):
                    factors = call_sets.pop(missing)
                    call_sets[missing.difference(new_variables)].extend(factors)

        return call_sequence

    def __call__(
            self,
            *args: np.ndarray,
            **kwargs: np.ndarray
    ) -> FactorValue:
        """
        Call each function in the graph in the correct order, adding the logarithmic results.

        Deterministic values computed in initial factor calls are added to a dictionary and
        passed to subsequent factor calls.

        Parameters
        ----------
        args
            Positional arguments
        kwargs
            Keyword arguments

        Returns
        -------
        Object comprising the log value of the computation and a dictionary containing
        the values of deterministic variables.
        """

        # generate set of factors to call, these are indexed by the
        # missing deterministic variables that need to be calculated
        log_value = 0.
        det_values = {}
        variables = kwargs

        n_args = len(args)
        if n_args > len(self._args):
            raise ValueError(
                f"too many arguments passed, must pass {len(self._args)} arguments, "
                f"factor graph call signature: {self.call_signature}"
            )

        missing = set(self.kwarg_names) - variables.keys() - set(self.arg_names[:n_args])
        if missing:
            n_miss = len(missing)
            missing_str = ", ".join(missing)
            raise ValueError(
                f"{self} missing {n_miss} arguments: {missing_str}"
                f"factor graph call signature: {self.call_signature}"
            )

        for calls in self._call_sequence:
            # TODO parallelise this part?
            for factor in calls:
                ret = factor(*args, **variables)
                ret_value = self.broadcast_plates(factor.plates, ret.log_value)
                log_value = add_arrays(log_value, ret_value)
                det_values.update(ret.deterministic_values)
                variables.update(ret.deterministic_values)

        return FactorValue(log_value, det_values)

    def __mul__(self, other: AbstractNode) -> "FactorGraph":
        """
        Combine this object with another factor node or graph, creating
        a new graph that comprises all of the factors of the two objects.
        """
        factors = self.factors

        if isinstance(other, FactorGraph):
            factors += other.factors
        elif isinstance(other, FactorNode):
            factors += (other,)
        else:
            raise TypeError(
                f"type of passed element {(type(other))} "
                "does not match required types, (`FactorGraph`, `FactorNode`)")

        return type(self)(factors)

    def __repr__(self) -> str:
        factors_str = " * ".join(map(repr, self.factors))
        return f"({factors_str})"

    @property
    def factors(self) -> Tuple[FactorNode, ...]:
        return self._factors

    @property
    def factor_all_variables(self) -> Dict[FactorNode, str]:
        return self._factor_all_variables