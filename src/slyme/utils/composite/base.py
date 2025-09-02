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


class Component(CompositeMixin, Generic[_ComponentT, _ComponentCollectionT]):
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


class ComponentCollection(CompositeMixin, MutableSequenceProxy[_ComponentT]):
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
        old_items = self[index] if isinstance(index, slice) else [self[index]]
        new_items = (
            # NOTE: Materialize ``value`` into list to allow multiple traversals.
            (value := list(cast(Iterable[_ComponentT], value)))
            if isinstance(index, slice)
            else [value := cast(_ComponentT, value)]
        )
        # >>> ATOMIC start <<<
        for item in old_items:
            del item._parent  # Should always succeed.
        try:
            # Set parent
            attached: list[_ComponentT] = []
            for item in new_items:
                item._parent = self
                attached.append(item)
            # Set item(s).
            super().__setitem__(index, value)
        except Exception:
            # Rollback to keep consistency
            for item in attached:
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
            del value._parent  # Should always succeed.
            raise
        # >>> ATOMIC end <<<

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[None, Self](None, self)


class ComponentContainer(
    Component[_ComponentT, _ComponentCollectionT],
    ComponentCollection[_ComponentT],
):
    __slots__ = ()

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[Self, Self](self, self)
