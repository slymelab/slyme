from abc import ABC, abstractmethod
from typing import TypeVar, Generic
from slyme.node import Node

_NodeT = TypeVar("_NodeT", bound=Node)


class BuilderExtension(ABC, Generic[_NodeT]):
    """
    Extension for custom node build.
    """

    @abstractmethod
    def apply(self, node: _NodeT) -> _NodeT:
        """
        Build operations after ``_build`` is called.
        """
        pass
