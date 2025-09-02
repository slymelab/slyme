from abc import ABC, abstractmethod
from slyme.utils.typing import (
    Generic,
    TypeVar,
    Self,
    Union,
    Iterable,
)

_ThisT_co = TypeVar("_ThisT_co", covariant=True)
_ChildrenT_co = TypeVar("_ChildrenT_co", covariant=True)


class CompositeStructure(Generic[_ThisT_co, _ChildrenT_co]):
    def __init__(self, this: _ThisT_co, children: _ChildrenT_co):
        self.this = this
        self.children = children


class CompositeMixin(ABC):
    @abstractmethod
    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable["CompositeMixin"], None]]:
        pass
