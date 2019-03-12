import os

import numpy as np

from autofit import conf
from autofit import exc
from autofit.mapper import link
from autofit.mapper import model_mapper as mm
from autofit.mapper import prior as p
from autofit.optimize import non_linear
from autofit.optimize import optimizer
from autofit.tools import path_util


class GridSearchResult(object):

    def __init__(self, results, lists):
        """
        The result of a grid search.

        Parameters
        ----------
        results: [non_linear.Result]
            The results of the non linear optimizations performed at each grid step
        lists: [[float]]
            A list of lists of values representing the lower bounds of the grid searched values at each step
        """
        self.lists = lists
        self.results = results
        self.no_dimensions = len(self.lists[0])
        self.no_steps = len(self.lists)
        self.side_length = int(self.no_steps ** (1 / self.no_dimensions))

    @property
    def best_result(self):
        """
        The best result of the grid search. That is, the result output by the non linear search that had the highest
        maximum figure of merit.

        Returns
        -------
        best_result: Result
        """
        best_result = None
        for result in self.results:
            if best_result is None or result.figure_of_merit > best_result.figure_of_merit:
                best_result = result
        return best_result

    @property
    def best_model(self):
        """
        Returns
        -------
        best_model: mm.ModelMapper
            The model mapper instance associated with the highest figure of merit from the grid search
        """
        return self.best_result.variable

    @property
    def all_models(self):
        """
        Returns
        -------
        all_models: [mm.ModelMapper]
            All model mapper instances used in the grid search
        """
        return [result.variable for result in self.results]

    @property
    def figure_of_merit_array(self):
        """
        Returns
        -------
        figure_of_merit_array: np.ndarray
            An array of figures of merit. This array has the same dimensionality as the grid search, with the value in
            each entry being the figure of merit taken from the optimization performed at that point.
        """
        return np.reshape(np.array([result.figure_of_merit for result in self.results]),
                          tuple(self.side_length for _ in range(self.no_dimensions)))


class GridSearch(object):

    def __init__(self, phase_name, phase_folders=None, number_of_steps=10, optimizer_class=non_linear.DownhillSimplex,
                 model_mapper=None, constant=None):
        """
        Performs a non linear optimiser search for each square in a grid. The dimensionality of the search depends on
        the number of distinct priors passed to the fit function. (1 / step_size) ^ no_dimension steps are performed
        per an optimisation.

        Parameters
        ----------
        number_of_steps: int
            The number of steps to go in each direction
        optimizer_class: class
            The class of the optimizer that is run at each step
        model_mapper: mm.ModelMapper | None
            The model mapper that maps between the optimizer and class model
        phase_name: str
            The name of this grid search
        """
        self.variable = model_mapper or mm.ModelMapper()
        self.constant = constant or mm.ModelInstance()

        self.phase_folders = phase_folders
        if phase_folders is None:
            self.phase_path = ''
        else:
            self.phase_path = path_util.path_from_folder_names(folder_names=phase_folders)

        self.phase_name = phase_name
        self.number_of_steps = number_of_steps
        self.optimizer_class = optimizer_class

        self.phase_output_path = "{}/{}/{}".format(conf.instance.output_path, self.phase_path, phase_name)

        sym_path = "{}/optimizer".format(self.phase_output_path)
        self.backup_path = "{}/optimizer_backup".format(self.phase_output_path)

        try:
            os.makedirs("/".join(sym_path.split("/")[:-1]))
        except FileExistsError:
            pass

        self.path = link.make_linked_folder(sym_path)

    @property
    def hyper_step_size(self):
        """
        Returns
        -------
        hyper_step_size: float
            The size of a step in any given dimension in hyper space.
        """
        return 1 / self.number_of_steps

    def make_lists(self, grid_priors):
        """
        Produces a list of lists of floats, where each list of floats represents the values in each dimension for one
        step of the grid search.

        Parameters
        ----------
        grid_priors: [p.Prior]
            A list of priors that are to be searched using the grid search.

        Returns
        -------
        lists: [[float]]
        """
        return optimizer.make_lists(len(grid_priors), step_size=self.hyper_step_size, centre_steps=False)

    def make_arguments(self, values, grid_priors):
        arguments = {}
        for value, grid_prior in zip(values, grid_priors):
            if float("-inf") == grid_prior.lower_limit or float('inf') == grid_prior.upper_limit:
                raise exc.PriorException("Priors passed to the grid search must have definite limits")
            lower_limit = grid_prior.lower_limit + value * grid_prior.width
            upper_limit = grid_prior.lower_limit + (value + self.hyper_step_size) * grid_prior.width
            prior = p.UniformPrior(lower_limit=lower_limit, upper_limit=upper_limit)
            arguments[grid_prior] = prior
        return arguments

    def model_mappers(self, grid_priors):
        grid_priors = list(set(grid_priors))
        lists = self.make_lists(grid_priors)
        for values in lists:
            arguments = self.make_arguments(values, grid_priors)
            yield self.variable.mapper_from_partial_prior_arguments(arguments)

    def fit(self, analysis, grid_priors):
        """
        Fit an analysis with a set of grid priors. The grid priors are priors associated with the model mapper
        of this instance that are replaced by uniform priors for each step of the grid search.

        Parameters
        ----------
        analysis: non_linear.Analysis
            An analysis used to determine the fitness of a given model instance
        grid_priors: [p.Prior]
            A list of priors to be substituted for uniform priors across the grid.

        Returns
        -------
        result: GridSearchResult
            An object that comprises the results from each individual fit
        """
        grid_priors = list(set(grid_priors))
        results = []
        lists = self.make_lists(grid_priors)

        results_list = [list(map(self.variable.name_for_prior, grid_priors)) + ["figure_of_merit"]]

        def write_results():
            with open("{}/results".format(self.phase_output_path), "w+") as f:
                f.write("\n".join(map(lambda ls: ", ".join(
                    map(lambda value: "{:.2f}".format(value) if isinstance(value, float) else str(value), ls)),
                                      results_list)))

        for values in lists:
            arguments = self.make_arguments(values, grid_priors)
            model_mapper = self.variable.mapper_from_partial_prior_arguments(arguments)

            labels = []
            for prior in arguments.values():
                labels.append(
                    "{}_{:.2f}_{:.2f}".format(model_mapper.name_for_prior(prior), prior.lower_limit, prior.upper_limit))

            name_path = "{}/{}".format(self.phase_name, "_".join(labels))
            optimizer_instance = self.optimizer_class(model_mapper=model_mapper,
                                                      phase_folders=self.phase_folders, phase_name=name_path)
            optimizer_instance.constant = self.constant
            result = optimizer_instance.fit(analysis)
            results.append(result)

            results_list.append([*[prior.lower_limit for prior in arguments.values()], result.figure_of_merit])

            write_results()

        return GridSearchResult(results, lists)
