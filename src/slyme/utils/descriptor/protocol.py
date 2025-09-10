from slyme.utils.typing import (
    Union,
    Protocol,
    overload,
    Self,
    TypeVar,
)

_GetT_co = TypeVar("_GetT_co", covariant=True)
_SetT_contra = TypeVar("_SetT_contra", contravariant=True)

# NOTE: Currently (py <= 3.15), @property with @abstractmethod is the best way to declare
# a readonly attribute, which also passes type checkers like mypy. Overriding the abstract
# property with a custom descriptor is also accepted by ABC at runtime.


class Attribute(Protocol[_GetT_co, _SetT_contra]):
    """Declaring an attribute."""
    @overload
    def __get__(self, instance: None, owner: Union[type, None] = None) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: Union[type, None] = None) -> _GetT_co: ...
    def __get__(self, instance: Union[object, None], owner: Union[type, None]=None) -> Union[_GetT_co, Self]: ...
    def __set__(self, instance, value: _SetT_contra) -> None: ...
    def __delete__(self, instance) -> None: ...
