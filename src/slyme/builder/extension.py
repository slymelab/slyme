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
        Build operations after ``build`` is called.
        """
        pass

    def __call__(self, node: _NodeT) -> _NodeT:
        node = self.apply(node)
        if node is None:
            raise ValueError(
                f"Extension {type(self).__name__}.apply returned None. "
                "Extensions must explicitly return the node instance."
            )
        return node
