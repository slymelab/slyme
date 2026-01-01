from slyme.utils.typing import (
    TypeVar,
    Self,
    Union,
    Iterable,
)
from slyme.utils.collection.base import MutableSequenceProxy, SequenceData
from .mixin import CompositeMixin, CompositeStructure

_ComponentT = TypeVar("_ComponentT", bound="Component")


class Component(CompositeMixin[_ComponentT]):
    __slots__ = ()

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

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[None, Self](None, self)


class ComponentContainer(
    Component[_ComponentT],
    ComponentCollection[_ComponentT],
):
    __slots__ = ()

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[Self, Self](self, self)
