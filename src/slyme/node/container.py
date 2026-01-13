from slyme.utils.typing import TypeVar
from slyme.context import Context
from slyme.node.base import Node, NodeExpression
from slyme.node.wrapper import NodeWrapper
from slyme.utils.composite import DictComposite, ListComposite
from slyme.utils.collection.base import SequenceData, MappingData

_R = TypeVar("_R")


class NodeList(Node, ListComposite[Node]):
    """ """

    def __init__(
        self,
        /,
        node_wrappers: SequenceData["NodeWrapper"] = None,
        children: SequenceData[Node] = None,
        **kwargs,
    ):
        super().__init__(node_wrappers=node_wrappers, children=children, **kwargs)

    def execute(self, ctx: Context) -> None:
        for node in self:
            node(ctx)


class NodeDict(Node, DictComposite[str, Node]):
    def __init__(
        self,
        /,
        node_wrappers: SequenceData["NodeWrapper"] = None,
        children: MappingData[str, Node] = None,
        **kwargs,
    ):
        super().__init__(node_wrappers=node_wrappers, children=children, **kwargs)

    def execute(self, ctx: Context) -> None:
        raise NotImplementedError("`NodeDict` cannot be directly called.")


class NodeExpressionList(
    NodeExpression[list[_R]], ListComposite[NodeExpression[_R]]
):
    def __init__(
        self,
        /,
        children: SequenceData[NodeExpression[_R]] = None,
        **kwargs,
    ):
        super().__init__(children=children, **kwargs)

    def evaluate(self, ctx: Context) -> list[_R]:
        return [node_exp(ctx) for node_exp in self]


class NodeExpressionDict(
    NodeExpression[dict[str, _R]],
    DictComposite[str, NodeExpression[_R]],
):
    def __init__(
        self,
        /,
        children: MappingData[str, NodeExpression[_R]] = None,
        **kwargs,
    ):
        super().__init__(children=children, **kwargs)

    def evaluate(self, ctx: Context) -> dict[str, _R]:
        return {key: node_exp(ctx) for key, node_exp in self.items()}
