from abc import ABC, abstractmethod
from slyme.utils.typing.native import Protocol, TypeVar, Generic
from slyme.utils.decorator import not_implemented
from .base.collection import BaseListABC

_T = TypeVar("_T")
_DescriptorT = TypeVar("_DescriptorT")


class DescriptorProtocol(Protocol[_T]):

    def __set_name__(self, owner, name):
        pass

    def __set__(self, instance, value: _T) -> None:
        pass

    def __get__(self, instance, owner=None) -> _T:
        pass

    def __delete__(self, instance) -> None:
        pass


class DescriptorABC(ABC, Generic[_T]):
    """
    NOTE: We don't separate ``DescriptorABC`` into ``DescriptorSetABC``,
    ``DescriptorGetABC`` and ``DescriptorDeleteABC`` (just like what
    ``ScopeAttrGuard`` does), because the three methods in ``DescriptorABC``
    are not optional, but all need to be implemented for full attribute
    support.
    """

    @abstractmethod
    def __set_name__(self, owner, name):
        pass

    @abstractmethod
    def __set__(self, instance, value: _T) -> None:
        pass

    @abstractmethod
    def __get__(self, instance, owner=None) -> _T:
        pass

    @abstractmethod
    def __delete__(self, instance) -> None:
        pass

    @not_implemented
    def set_yield(self, instance, value: _T):
        yield

    @not_implemented
    def get_yield(self, instance, owner, value: _T):
        yield

    @not_implemented
    def delete_yield(self, instance):
        yield


class DescriptorContainerABC(
    DescriptorABC[_T], BaseListABC[_DescriptorT], ABC, Generic[_DescriptorT, _T]
):
    """
    NOTE: The ``DescriptorContainerABC`` is designed to hold a single descriptor
    list rather than three separate ones (just like what ``ScopeAttrGuardCollection``
    does) for the following reasons:

    1. The ``DescriptorABC`` is "one for three methods" (see the doc for details).
    2. It is not so convenient to set a shared descriptor to three separate lists
    in the class body. For example:

    ```python
    class BadExample:
        # Complex code in the class body is not a good design.
        descriptor1 = SomeCustomDescriptor(*args, **kwargs)
        descriptor2 = OtherCustomDescriptor(*args, **kwargs)
        attr = BadDescriptorContainer(set=[descriptor1, descriptor2], get=[descriptor2], delete=[descriptor1])
        del descriptor1, descriptor2  # NOTE: Should delete the temp variables, or they will become class vars.
    ```

    The following design is better:

    ```python
    class GoodExample:
        # Simple and easy to read.
        attr = GoodDescriptorContainer([
            SomeCustomDescriptor(*args, **kwargs),
            OtherCustomDescriptor(*args, **kwargs),
        ])
    ```
    """

    pass
