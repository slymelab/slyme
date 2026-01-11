from slyme.utils.typing import (
    Generic,
    TypeVar,
    overload,
    Self,
    Union,
    Callable,
    Type,
)
from slyme.utils.mixin import GetattrAdapterMixin
from slyme.utils.inspect import resolve_instance_classname

_T = TypeVar("_T")
_R = TypeVar("_R")


class cached_property(GetattrAdapterMixin, Generic[_T, _R]):
    """
    A minimal, lock-free implementation of cached_property.
    """

    def __init__(self, func: Callable[[_T], _R], doc: Union[str, None] = None):
        self.func = func
        self.__doc__ = doc or func.__doc__

    def __set_name__(self, owner: Type[_T], name: str):
        self.name = name

    def __getattr__(self, name: str):
        """
        Safeguard against accessing metadata (like `name`) on an improperly initialized descriptor.

        This explicitly traps missing `name` attributes to prevent confusing AttributeErrors
        inside `__get__` when `__set_name__` has not been triggered.
        """
        if name == "name":
            raise RuntimeError(
                f"cached_property attribute '{name}' is missing. "
                "This usually happens when the descriptor is not assigned to a class attribute "
                "correctly (missing __set_name__ call), or instantiated dynamically without manual setup."
            )
        return super().__getattr__(name)

    @overload
    def __get__(self, instance: None, owner: Union[Type[_T], None] = None) -> Self: ...
    @overload
    def __get__(self, instance: _T, owner: Union[Type[_T], None] = None) -> _R: ...
    def __get__(
        self, instance: Union[_T, None], owner: Union[Type[_T], None] = None
    ) -> Union[Self, _R]:
        if instance is None:
            return self

        val = self.func(instance)

        try:
            # Check for __dict__ existence first to provide a clear error message
            # if the class uses __slots__ without __dict__.
            cache = instance.__dict__
        except AttributeError:
            raise TypeError(
                f"The class {resolve_instance_classname(instance)!r} uses 'cached_property' but has no '__dict__'. "
                f"Ensure the class does not define __slots__ or includes '__dict__' in __slots__."
            ) from None

        cache[self.name] = val
        return val
