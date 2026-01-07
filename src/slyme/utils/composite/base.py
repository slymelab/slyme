from slyme.utils.typing import (
    TypeVar,
    Self,
    Union,
    Iterable,
    Generic,
)
from slyme.utils.collection.base import (
    MutableSequenceProxy,
    SequenceData,
    MutableMappingProxy,
    MappingData,
)
from .mixin import CompositeMixin, CompositeStructure

_ComponentT = TypeVar("_ComponentT", bound="Component")
_KT = TypeVar("_KT")


class Component(CompositeMixin[_ComponentT]):

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[Self, None](self, None)


class ComponentList(
    CompositeMixin[_ComponentT], MutableSequenceProxy[_ComponentT]
):

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


class ListComposite(
    Component[_ComponentT],
    ComponentList[_ComponentT],
):

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[Self, Self](self, self)


class ComponentDict(
    CompositeMixin[_ComponentT],
    MutableMappingProxy[_KT, _ComponentT],
    Generic[_KT, _ComponentT],
):

    def __init__(self, /, children: MappingData[_KT, _ComponentT] = None, **kwargs):
        super().__init__(mapping_data=children, **kwargs)

    @property
    def children(self, /) -> tuple[_ComponentT, ...]:
        """Returns a readonly view of the children (values)"""
        return tuple(self.values())

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[None, Iterable[_ComponentT]](None, self.values())


class DictComposite(
    Component[_ComponentT],
    ComponentDict[_KT, _ComponentT],
    Generic[_KT, _ComponentT],
):

    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_ComponentT], None]]:
        return CompositeStructure[Self, Iterable[_ComponentT]](self, self.values())
