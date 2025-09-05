from slyme.utils.typing import (
    TypeVar,
    Self,
    Generic,
    Union,
    Iterable,
    overload,
    cast,
)
from slyme.utils.descriptor.protocol import Attribute
from slyme.utils.collection.base import MutableSequenceProxy, SequenceData
from .mixin import CompositeMixin, CompositeStructure

_ComponentT = TypeVar("_ComponentT", bound="Component")
_ComponentCollectionT = TypeVar("_ComponentCollectionT", bound="ComponentCollection")
_ComponentContainerT = TypeVar("_ComponentContainerT", bound="ComponentContainer")


class Component(
    CompositeMixin[_ComponentT], Generic[_ComponentT, _ComponentCollectionT]
):
    __slots__ = ()
    _prop__parent: Attribute[
        Union[_ComponentCollectionT, None], Union[_ComponentCollectionT, None]
    ]

    def __init__(self, /, **kwargs):
        super().__init__(**kwargs)
        self._parent = None

    @property
    def parent(self, /) -> Union[_ComponentCollectionT, None]:
        """Readonly parent reference."""
        return self._parent

    # NOTE: Inner parent operations.
    @property
    def _parent(self, /) -> Union[_ComponentCollectionT, None]:
        return self._prop__parent

    @_parent.setter
    def _parent(self, value: Union[_ComponentCollectionT, None], /) -> None:
        """Set parent reference.

        NOTE: ``None`` values will always be directly set without raising any error, which is a
        stable feature to ensure consistency operations (i.e., this operation will always succeed).
        """
        if value is None:
            self._prop__parent = value
            return
        if self._prop__parent is not None:
            raise ValueError(
                f"Cannot attach to a new parent when the component has already been attached."
            )
        if not isinstance(value, ComponentCollection):
            raise ValueError(
                f"Parent of component can only be of ``ComponentCollection`` type, got {type(value)}."
            )
        self._prop__parent = value

    @_parent.deleter
    def _parent(self) -> None:
        """Set parent reference to None.

        NOTE: This operation will always succeed without raising any error.
        """
        self._parent = None

    # NOTE: Inner checked parent operations (for parent-child consistency).
    @property
    def _checked_parent(self) -> _ComponentCollectionT:
        """Raise ValueError if ``self._parent`` is None, else return ``self._parent``."""
        if self._parent is None:
            raise ValueError(
                "Trying to access ``_checked_parent``, but no parent found."
            )
        return self._parent

    @_checked_parent.deleter
    def _checked_parent(self) -> None:
        """Set ``self._parent`` to None and raise ValueError.

        NOTE: This method is called when inconsistency is found.
        """
        error_msg = f"Parent-child inconsistency has occurred, please check: {self} is not found in {self._parent}."
        del self._parent
        raise ValueError(error_msg)

    def replace_self(self, value: _ComponentT, /) -> None:
        parent = self._checked_parent
        try:
            index = parent.index(self)
        except ValueError:
            del self._checked_parent
        else:
            parent[index] = value

    def insert_before_self(self, value: _ComponentT, /) -> None:
        parent = self._checked_parent
        try:
            index = parent.index(self)
        except ValueError:
            del self._checked_parent
        else:
            parent.insert(index, value)

    def insert_after_self(self, value: _ComponentT, /) -> None:
        parent = self._checked_parent
        try:
            index = parent.index(self)
        except ValueError:
            del self._checked_parent
        else:
            parent.insert(index + 1, value)

    def remove_self(self, /) -> None:
        parent = self._checked_parent
        try:
            parent.remove(self)
        except ValueError:
            del self._checked_parent

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[Self, None](self, None)


class ComponentCollection(
    CompositeMixin[_ComponentT], MutableSequenceProxy[_ComponentT]
):
    __slots__ = ()

    def __init__(self, /, children: SequenceData[_ComponentT] = None, **kwargs):
        super().__init__(sequence_data=children, **kwargs)

    @property
    def children(self, /) -> tuple[_ComponentT, ...]:
        """Returns a readonly view of the children"""
        return tuple(self)

    # NOTE: parent-child consistency assurance.
    @overload
    def __setitem__(self, index: int, value: _ComponentT, /) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[_ComponentT], /) -> None: ...
    def __setitem__(
        self,
        index: Union[int, slice],
        value: Union[_ComponentT, Iterable[_ComponentT]],
        /,
    ) -> None:
        old_items = (
            self[index] if (_is_slice := isinstance(index, slice)) else [self[index]]
        )
        new_items = (
            cast(Iterable[_ComponentT], value)
            if _is_slice
            else [cast(_ComponentT, value)]
        )
        # >>> ATOMIC start <<<
        for item in old_items:
            del item._parent  # Should always succeed.
        try:
            # Rationale for this implementation:
            # This design is optimized to prevent the redundant memory allocation of two identical
            # lists.
            # The challenge is twofold:
            # a) The input ``value`` could be a single-pass iterable (like a generator), so it must
            #    be materialized into a list before it can be iterated for multiple times (1. set the
            #    parent pointer and 2. call super().__setitem__).
            # b) We need a list of newly attached items (`attached`) to enable precise rollback in
            #    case of an exception.
            # A naive solution would be to first do `new_items_list = list(value)` and then iterate
            # over `new_items_list` to populate `attached`. This would result in two identical lists
            # in memory if the operation succeeds (`new_items_list` and `attached`).
            # This implementation solves the redundancy by merging the two steps: it iterates through
            # the `new_items` iterable exactly once, simultaneously setting the parent pointers and
            # building the `attached` list. This list then serves both as the source for the super()
            # call and as the manifest for the rollback mechanism.
            # Set parent.
            attached: list[_ComponentT] = []
            for item in new_items:
                item._parent = self
                attached.append(item)
            # Set item(s).
            super().__setitem__(
                index, attached if _is_slice else cast(_ComponentT, value)
            )
        except Exception:
            # Rollback to keep consistency
            for item in attached:
                # This is safe because the attach operation (`item._parent = self`) only
                # succeeds if the item's parent was `None` to begin with.
                del item._parent  # Should always succeed.
            for item in old_items:
                item._parent = self
            raise
        # >>> ATOMIC end <<<

    @overload
    def __delitem__(self, index: int, /) -> None: ...
    @overload
    def __delitem__(self, index: slice, /) -> None: ...
    def __delitem__(self, index: Union[int, slice], /) -> None:
        items = self[index] if isinstance(index, slice) else [self[index]]
        # >>> ATOMIC start <<<
        # Remove parent
        for item in items:
            del item._parent  # Should always succeed.
        try:
            # Delete item(s).
            super().__delitem__(index)
        except Exception:
            # Rollback to keep consistency
            for item in items:
                item._parent = self
            raise
        # >>> ATOMIC end <<<

    def insert(self, index: int, value: _ComponentT, /) -> None:
        # >>> ATOMIC start <<<
        value._parent = self
        try:
            super().insert(index, value)
        except Exception:
            # Rollback to keep consistency
            # This is safe because the attach operation (`value._parent = self`) only
            # succeeds if the value's parent was `None` to begin with.
            del value._parent  # Should always succeed.
            raise
        # >>> ATOMIC end <<<

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[None, Self](None, self)


class ComponentContainer(
    Component[_ComponentT, _ComponentContainerT],
    ComponentCollection[_ComponentT],
):
    __slots__ = ()

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[Self, Self](self, self)
