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
    def _build(self) -> _NodeT:
        """
        Build the node structure.
        """
        pass

    def build(self, check_structure: bool = True) -> _NodeT:
        """
        Perform a complete build operation for building node structure.
        """
        node = self._build()
        if node is None:
            raise ValueError(
                f"The `_build` method of {type(self).__name__} returned None. "
                "Did you forget to return the constructed Node?"
            )
        for extension in self.extensions:
            node = extension.apply(node)
            if node is None:
                raise ValueError(
                    f"Extension {type(extension).__name__}.apply returned None. "
                    "Extensions must explicitly return the node instance."
                )
        if check_structure:
            check_node_structure(node)
        return node
