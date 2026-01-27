from abc import ABC, abstractmethod
from typing import TypeVar, Generic
from slyme.utils.collection import SequenceData
from slyme.node import Node, check_node_structure
from .extension import BuilderExtension

_NodeT = TypeVar("_NodeT", bound=Node)


class Builder(ABC, Generic[_NodeT]):

    def __init__(
        self,
        /,
        extensions: SequenceData[BuilderExtension[_NodeT]] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.extensions: list[BuilderExtension[_NodeT]] = (
            list(extensions) if extensions is not None else []
        )

    @abstractmethod
    def build(self) -> _NodeT:
        """
        Build the node structure.
        """
        pass

    def __call__(self, check_structure: bool = True) -> _NodeT:
        """
        Perform a complete build operation for building node structure.
        """
        node = self.build()
        if node is None:
            raise ValueError(
                f"The `build` method of {type(self).__name__} returned None. "
                "Did you forget to return the constructed Node?"
            )
        for extension in self.extensions:
            node = extension(node)
        if check_structure:
            check_node_structure(node)
        return node
