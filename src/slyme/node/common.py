from collections.abc import Iterable, Sequence
from slyme.context import Context
from .core import Node, node

__all__ = [
    "sequential_exec",
    "sequential",
]


def sequential_exec(ctx: Context, nodes: Iterable[Node]) -> Context:
    """
    Sequentially execute a list of nodes, passing the context from one to the next.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Iterable[Node]): An iterable of nodes to execute.

    Returns:
        Context: The final context after all nodes have been executed.
    """
    for node in nodes:
        ctx = node(ctx)
    return ctx


@node
def sequential(ctx: Context, /, *, nodes: Sequence[Node]) -> Context:
    """
    Sequentially execute a list of nodes, passing the context from one to the next.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Sequence[Node]): A sequence of nodes to execute.

    Returns:
        Context: The final context after all nodes have been executed.
    """
    return sequential_exec(ctx, nodes)
