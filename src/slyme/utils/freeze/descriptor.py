from slyme.utils.typing import (
    overload,
    Union,
    Type,
    Self,
    Any,
    TypeVar,
    Generic,
)

_GetT = TypeVar("_GetT")


class FrozenAttr(Generic[_GetT]):
    def __init__(self, value: _GetT):
        self._value = value

    @overload
    def __get__(self, instance: None, owner: Union[Type, None] = None) -> Self: ...
    @overload
    def __get__(self, instance: object, owner: Union[Type, None] = None) -> _GetT: ...
    def __get__(self, instance, owner=None):
        if instance is None:
            return self
        return self._value

    def __set__(self, instance, value: Any):
        raise TypeError("The attribute is frozen and cannot be modified.")

    def __delete__(self, instance):
        raise TypeError("The attribute is frozen and cannot be modified.")
