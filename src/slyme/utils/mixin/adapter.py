class InitAdapterMixin:
    """
    Accepts any ``__init__`` parameters and passes no arguments to ``super().__init__()``,
    enabling safe method chaining in multiple inheritance (specifically, ``object.__init__``
    takes no extra arguments except ``self``).
    """

    __slots__ = ()

    def __init__(self, *args, **kwargs) -> None:
        """Placeholder ``__init__`` method."""
        super().__init__()


class NewAdapterMixin:
    __slots__ = ()

    def __new__(cls, *args, **kwargs):
        """Placeholder ``__new__`` method."""
        return super().__new__(cls)


class InitSubclassAdapterMixin:
    __slots__ = ()

    def __init_subclass__(cls, *args, **kwargs) -> None:
        """Placeholder ``__init_subclass__`` method."""
        super().__init_subclass__()
