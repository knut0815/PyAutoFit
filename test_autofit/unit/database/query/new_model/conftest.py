import pytest

from autofit.database import query_model as q


@pytest.fixture(
    name="less_than"
)
def make_less_than():
    return q.V(
        "<", 1
    )


@pytest.fixture(
    name="greater_than"
)
def make_greater_than():
    return q.V(
        ">", 0
    )


@pytest.fixture(
    name="simple_combination"
)
def make_simple_combination(
        less_than,
        greater_than
):
    return q.Q(
        "a",
        q.And(
            less_than,
            greater_than
        )
    )


@pytest.fixture(
    name="second_level"
)
def make_second_level(
        less_than,
        greater_than
):
    return q.Q(
        "a",
        q.And(
            less_than,
            q.Q('b', greater_than)
        )
    )