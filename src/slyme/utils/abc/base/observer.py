from abc import ABC, abstractmethod
from slyme.utils.decorator import not_implemented
from slyme.utils.typing.extension import MISSING, EmptyFlag, Missing
from slyme.utils.typing.native import (
    Any,
    Callable,
    Dict,
    Generic,
    Sequence,
    Union,
    TypeVar,
)

_AttrObserverT = TypeVar("_AttrObserverT")


class AttrObserverABC(ABC):
    """
    ABC of ``AttrObserver``.
    """

    @abstractmethod
    def smx_detach_inspect(
        self, namespaces: Union[Sequence[str], EmptyFlag] = MISSING
    ) -> Dict[str, Callable]:
        """
        Inspect detach items of the observer.
        """
        pass

    @abstractmethod
    def smx_attach_inspect(
        self, namespaces: Union[Sequence[str], EmptyFlag] = MISSING
    ) -> Dict[str, Callable]:
        """
        Inspect attach items of the observer.
        """
        pass

    @abstractmethod
    def smx_detach_all(self) -> None:
        """
        Detach self from all the observers it has attached to.
        """
        pass


class AttrObservableABC(ABC, Generic[_AttrObserverT]):
    """
    ABC of ``AttrObservable``.
    """

    @abstractmethod
    def smx_attach(
        self,
        __observer: _AttrObserverT,
        *,
        init: Union[bool, Missing] = MISSING,
        namespaces: Union[Sequence[str], EmptyFlag] = MISSING
    ) -> None:
        """
        Attach the observer functions to self.
        """
        pass

    @abstractmethod
    def smx_attach_attr(
        self, __observer: _AttrObserverT, __name: str, *, init: bool = True
    ) -> None:
        """
        Attach a single observer function to self.
        """
        pass

    @abstractmethod
    def smx_detach(
        self,
        __observer: _AttrObserverT,
        *,
        namespaces: Union[Sequence[str], EmptyFlag] = MISSING
    ) -> None:
        """
        Detach the observer functions from self.
        """
        pass

    @abstractmethod
    def smx_detach_attr(self, __observer: _AttrObserverT, __name: str) -> None:
        """
        Detach a single observer function from self.
        """
        pass

    @abstractmethod
    def smx_notify(
        self,
        __observer: _AttrObserverT,
        __name: str,
        __new_value: Any,
        __old_value: Any,
    ) -> None:
        """
        Notify the attached observers that the corresponding attribute has changed.
        """
        pass

    @abstractmethod
    def __setattr__(self, __name: str, __value: Any) -> None:
        """
        Set attribute and notify changes.
        """
        return super().__setattr__(__name, __value)
