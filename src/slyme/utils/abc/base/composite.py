from abc import ABC, abstractmethod
from slyme.utils.abc.base.collection import BaseListABC
from slyme.utils.typing.extension import Nothing, Stop
from slyme.utils.typing.native import (
    Generic,
    Iterable,
    List,
    SupportsIndex,
    Union,
    TypeVar,
)

_ThisT = TypeVar("_ThisT")
_ChildrenT = TypeVar("_ChildrenT")
_ComponentT = TypeVar("_ComponentT")
_ComponentCollectionT = TypeVar("_ComponentCollectionT")
_ComponentContainerT = TypeVar("_ComponentContainerT")


class CompositeStructureABC(ABC, Generic[_ThisT, _ChildrenT]):
    this: _ThisT
    children: _ChildrenT


class CompositeMixinABC(ABC):

    @abstractmethod
    def smx_composite(
        self,
    ) -> CompositeStructureABC[
        Union["CompositeMixinABC", Stop], Union[Iterable["CompositeMixinABC"], Stop]
    ]:
        """
        Return the composite components.
        """
        pass


class ComponentABC(CompositeMixinABC, ABC, Generic[_ComponentT, _ComponentCollectionT]):
    """
    ABC of ``Component``.
    """

    @abstractmethod
    def smx_set_parent(self, parent: _ComponentCollectionT) -> None:
        """
        Set parent of the Component.
        """
        pass

    @abstractmethod
    def smx_get_parent(self) -> Union[_ComponentCollectionT, Nothing]:
        """
        Get the parent. If no parent is specified, return ``NOTHING``.
        """
        pass

    @abstractmethod
    def smx_get_verified_parent(
        self, contain_check: bool = True
    ) -> Union[_ComponentCollectionT, Nothing]:
        """
        Check parent validity and return the parent. If any inconsistencies occur,
        try to fix them and return ``NOTHING``.

        ``contain_check``: Whether to perform a containment check. This can improve
        performance if subsequent operations on the parent also check containment (
        e.g., parent.index, parent.remove, etc.).
        """
        pass

    @abstractmethod
    def smx_del_parent(self):
        """
        Remove the parent.
        """
        pass

    @abstractmethod
    def smx_replace_self(self, __item: _ComponentT) -> None:
        """
        Replace self with ``__item`` in the parent.
        """
        pass

    @abstractmethod
    def smx_insert_before_self(self, __item: _ComponentT) -> None:
        """
        Insert ``__item`` before self in the parent.
        """
        pass

    @abstractmethod
    def smx_insert_after_self(self, __item: _ComponentT) -> None:
        """
        Insert ``__item`` after self in the parent.
        """
        pass

    @abstractmethod
    def smx_remove_self(self) -> None:
        """
        Remove self from the parent.
        """
        pass


class ComponentCollectionABC(
    CompositeMixinABC, BaseListABC[_ComponentT], ABC, Generic[_ComponentT]
):
    """
    ABC of ``ComponentCollection``.
    """

    # NOTE: Some abstract methods have already been defined in super classes,
    # but we still re-define them here to denote that these method should be
    # overridden.

    @abstractmethod
    def smx_set_list(self, __list: List[_ComponentT]) -> None:
        """
        Change the list reference of self.
        """
        pass

    @abstractmethod
    def __setitem__(
        self,
        __key: Union[SupportsIndex, slice],
        __value: Union[_ComponentT, Iterable[_ComponentT]],
    ) -> None:
        """
        ComponentCollection set item.
        """
        pass

    @abstractmethod
    def __delitem__(self, __key: Union[SupportsIndex, slice]) -> None:
        """
        ComponentCollection delete item.
        """
        pass

    @abstractmethod
    def insert(self, __index: SupportsIndex, __item: _ComponentT) -> None:
        """
        ComponentCollection insert.
        """
        pass


class ComponentContainerABC(
    ComponentABC[_ComponentT, _ComponentContainerT],
    ComponentCollectionABC[_ComponentT],
    CompositeMixinABC,
    ABC,
    Generic[_ComponentT, _ComponentContainerT],
):
    pass
