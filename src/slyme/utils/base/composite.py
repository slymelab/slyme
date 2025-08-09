from collections import deque
import slyme.logging.logger as logger
from slyme.utils.abc.base.composite import (
    ComponentABC,
    ComponentCollectionABC,
    ComponentContainerABC,
    CompositeMixinABC,
    CompositeStructureABC,
)
from .collection import BaseList
from slyme.utils.typing.extension import (
    NOTHING,
    Nothing,
    is_empty_flag,
    resolve_private_attr_name,
    Stop,
    STOP,
)
from slyme.utils.typing.native import (
    Callable,
    Deque,
    Generic,
    Iterable,
    Iterator,
    List,
    SupportsIndex,
    TypeVar,
    Union,
    overload,
)
from . import BaseObjectInit

_ThisT = TypeVar("_ThisT", bound=Union[CompositeMixinABC, Stop])
_ChildrenT = TypeVar("_ChildrenT", bound=Union[Iterable[CompositeMixinABC], Stop])
_CompositeMixinT = TypeVar("_CompositeMixinT", bound=CompositeMixinABC)
_ComponentT = TypeVar("_ComponentT", bound=ComponentABC)
_ComponentCollectionT = TypeVar("_ComponentCollectionT", bound=ComponentCollectionABC)
_ComponentContainerT = TypeVar("_ComponentContainerT", bound=ComponentContainerABC)


class CompositeStructure(
    CompositeStructureABC[_ThisT, _ChildrenT], Generic[_ThisT, _ChildrenT]
):
    def __init__(self, this: _ThisT, children: _ChildrenT):
        self.this = this
        self.children = children


def composite_DFT(
    __item: _CompositeMixinT, __func: Callable[[_CompositeMixinT], None]
) -> None:
    stack: Deque[Iterator[_CompositeMixinT]] = deque([iter([__item])])

    while len(stack) > 0:
        node_iter = stack[-1]

        try:
            node = next(node_iter)
        except StopIteration:
            stack.pop()
            continue

        structure = node.smx_composite()
        if structure.this is not STOP:
            __func(structure.this)
        if structure.children is not STOP:
            stack.append(iter(structure.children))


def composite_DFS(
    __item: _CompositeMixinT, __func: Callable[[_CompositeMixinT], bool]
) -> List[_CompositeMixinT]:
    results = []

    def _search(item):
        if __func(item):
            results.append(item)

    composite_DFT(__item, _search)
    return results


def composite_BFT(
    __item: _CompositeMixinT, __func: Callable[[_CompositeMixinT], None]
) -> None:
    queue: Deque[Iterator[_CompositeMixinT]] = deque([iter([__item])])

    while len(queue) > 0:
        node_iter = queue[0]

        try:
            node = next(node_iter)
        except StopIteration:
            queue.popleft()
            continue

        structure = node.smx_composite()
        if structure.this is not STOP:
            __func(structure.this)
        if structure.children is not STOP:
            queue.append(iter(structure.children))


def composite_BFS(
    __item: _CompositeMixinT, __func: Callable[[_CompositeMixinT], bool]
) -> List[_CompositeMixinT]:
    results = []

    def _search(item):
        if __func(item):
            results.append(item)

    composite_BFT(__item, _search)
    return results


class Component(
    ComponentABC[_ComponentT, _ComponentCollectionT],
    BaseObjectInit,
    Generic[_ComponentT, _ComponentCollectionT],
):
    """
    NOTE: The item can only have at most one parent at a time, and inserting or assigning
    an item that already has a parent to another ``ComponentCollection`` will trigger warning.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.__parent: Union[_ComponentCollectionT, Nothing] = NOTHING
        # Cache the name of the private attribute ``__parent``.
        self.__parent_attr_name: str = resolve_private_attr_name(Component, "__parent")

    def smx_set_parent(self, parent: _ComponentCollectionT) -> None:
        prev_parent = self.smx_get_parent()
        if not is_empty_flag(prev_parent) and parent is not prev_parent:
            # duplicate parent
            logger.core_logger.warning(
                f"Component ``{str(self)}`` has already had a parent, but another parent is set. "
                "This may be because you add a single Component object to multiple ComponentCollections "
                "and may cause some inconsistent problems."
            )
        self.__parent = parent

    def smx_get_parent(self) -> Union[_ComponentCollectionT, Nothing]:
        return getattr(self, self.__parent_attr_name, NOTHING)

    def smx_get_verified_parent(
        self, contain_check: bool = True
    ) -> Union[_ComponentCollectionT, Nothing]:
        parent = self.smx_get_parent()
        if is_empty_flag(parent):
            # root node
            logger.core_logger.warning(
                f"Component ``{str(self)}`` does not have a parent."
            )
            return NOTHING
        if contain_check and self not in parent:
            self.smx_process_unmatched_parent()
            return NOTHING
        return parent

    def smx_process_unmatched_parent(self) -> None:
        """
        Output warnings and delete the ``__parent`` reference if the parent is unmatched.
        NOTE: This method does not perform any actual checking, and it should not be called
        externally in most cases, otherwise inconsistency may occur.
        """
        logger.core_logger.warning(
            f"Component ``{str(self)}`` is not contained in its specified parent."
        )
        self.smx_del_parent()

    def smx_del_parent(self):
        self.__parent = NOTHING

    def smx_replace_self(self, __item: _ComponentT) -> None:
        parent = self.smx_get_verified_parent(contain_check=False)
        try:
            index = parent.index(self)
        except ValueError:
            self.smx_process_unmatched_parent()
        else:
            parent[index] = __item

    def smx_insert_before_self(self, __item: _ComponentT) -> None:
        parent = self.smx_get_verified_parent(contain_check=False)
        try:
            index = parent.index(self)
        except ValueError:
            self.smx_process_unmatched_parent()
        else:
            parent.insert(index, __item)

    def smx_insert_after_self(self, __item: _ComponentT) -> None:
        parent = self.smx_get_verified_parent(contain_check=False)
        try:
            index = parent.index(self)
        except ValueError:
            self.smx_process_unmatched_parent()
        else:
            parent.insert(index + 1, __item)

    def smx_remove_self(self) -> None:
        parent = self.smx_get_verified_parent(contain_check=False)
        try:
            parent.remove(self)
        except ValueError:
            self.smx_process_unmatched_parent()

    def smx_composite(self) -> CompositeStructure[_ComponentT, Stop]:
        return CompositeStructure(self, STOP)


class ComponentCollection(
    BaseList[_ComponentT], ComponentCollectionABC[_ComponentT], Generic[_ComponentT]
):
    """
    The ``ComponentCollection`` container that contains ``Component``.
    """

    def smx_set_list(self, __list: List[_ComponentT]) -> None:
        prev_list = self.smx_get_list()

        for prev_item in prev_list:
            prev_item.smx_del_parent()

        for item in __list:
            item.smx_set_parent(self)

        return super().smx_set_list(__list)

    @overload
    def __setitem__(self, __key: SupportsIndex, __value: _ComponentT) -> None:
        pass

    @overload
    def __setitem__(self, __key: slice, __value: Iterable[_ComponentT]) -> None:
        pass

    def __setitem__(
        self,
        __key: Union[SupportsIndex, slice],
        __value: Union[_ComponentT, Iterable[_ComponentT]],
    ) -> None:
        # delete parents of the replaced items and set parents to the replacing items
        if isinstance(__key, slice):
            for replaced_item in self[__key]:
                replaced_item.smx_del_parent()

            for item in __value:
                item: _ComponentT
                item.smx_set_parent(self)
        else:
            self[__key].smx_del_parent()
            __value: _ComponentT
            __value.smx_set_parent(self)
        return super().__setitem__(__key, __value)

    @overload
    def __delitem__(self, __key: SupportsIndex) -> None:
        pass

    @overload
    def __delitem__(self, __key: slice) -> None:
        pass

    def __delitem__(self, __key: Union[SupportsIndex, slice]) -> None:
        if isinstance(__key, slice):
            for item in self[__key]:
                item.smx_del_parent()
        else:
            self[__key].smx_del_parent()
        return super().__delitem__(__key)

    def insert(self, __index: SupportsIndex, __item: _ComponentT) -> None:
        __item.smx_set_parent(self)
        return super().insert(__index, __item)

    def smx_composite(self) -> CompositeStructure[Stop, Iterable[_ComponentT]]:
        return CompositeStructure(STOP, self)


class ComponentContainer(
    Component[_ComponentT, _ComponentContainerT],
    ComponentCollection[_ComponentT],
    ComponentContainerABC[_ComponentT, _ComponentContainerT],
    Generic[_ComponentT, _ComponentContainerT],
):
    def smx_composite(
        self,
    ) -> CompositeStructure[_ComponentContainerT, Iterable[_ComponentT]]:
        return CompositeStructure(self, self)
