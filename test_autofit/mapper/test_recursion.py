class Wrapper:
    def __init__(
            self,
            item
    ):
        self.item = item


class A:
    def __init__(
            self,
            b=None
    ):
        self.b = b


class B:
    def __init__(
            self,
            a=None
    ):
        self.a = a


def dict_recurse(
        item
):
    try:
        for key, value in item.__dict__.items():
            setattr(
                item,
                key,
                dict_recurse(value)
            )
    except AttributeError:
        pass
    return Wrapper(item)


def test_basic():
    result = dict_recurse(
        A(B())
    )
    print(result)
