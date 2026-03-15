import asyncio
from typing import Union
from collections.abc import Iterable, Sequence
from slyme.context import Context
from .core import Node, node, AsyncNode, async_node

__all__ = [
    "sequential_exec",
    "sequential",
    "async_sequential_exec",
    "async_sequential",
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
    for node_ in nodes:
        ctx = node_(ctx)
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


async def async_sequential_exec(
    ctx: Context, nodes: Iterable[Union[Node, AsyncNode]]
) -> Context:
    """
    Sequentially execute a list of nodes, passing the context from one to the next.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Iterable[Union[Node, AsyncNode]]): An iterable of nodes to execute.

    Returns:
        Context: The final context after all nodes have been executed.
    """
    for node_ in nodes:
        if isinstance(node_, AsyncNode):
            ctx = await node_(ctx)
        else:
            ctx = await asyncio.to_thread(node_, ctx)
    return ctx


@async_node
async def async_sequential(
    ctx: Context, /, *, nodes: Sequence[Union[Node, AsyncNode]]
) -> Context:
    """
    Sequentially execute a list of nodes, passing the context from one to the next.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Sequence[Union[Node, AsyncNode]]): A sequence of nodes to execute.

    Returns:
        Context: The final context after all nodes have been executed.
    """
    return await async_sequential_exec(ctx, nodes)
