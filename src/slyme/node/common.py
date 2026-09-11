# Copyright 2026 The SlymeLab Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from collections.abc import Iterable, Sequence

from slyme.context import Context

from .core import AsyncNode, Node, node

__all__ = [
    "sequential_exec",
    "sequential",
    "async_sequential_exec",
    "async_sequential",
]


def sequential_exec(ctx: Context, nodes: Iterable[Node]) -> None:
    """
    Sequentially execute nodes against the same mutable context.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Iterable[Node]): An iterable of nodes to execute.

    Returns:
        None.
    """
    for node_ in nodes:
        node_(ctx)


@node
def sequential(ctx: Context, /, *, nodes: Sequence[Node]) -> None:
    """
    Sequentially execute nodes against the same mutable context.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Sequence[Node]): A sequence of nodes to execute.

    Returns:
        None.
    """
    sequential_exec(ctx, nodes)


async def async_sequential_exec(
    ctx: Context, nodes: Iterable[Node | AsyncNode]
) -> None:
    """
    Sequentially execute nodes against the same mutable context.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Iterable[Node | AsyncNode]): An iterable of nodes to execute.

    Returns:
        None.
    """
    for node_ in nodes:
        if isinstance(node_, AsyncNode):
            await node_(ctx)
        else:
            node_(ctx)


@node
async def async_sequential(
    ctx: Context, /, *, nodes: Sequence[Node | AsyncNode]
) -> None:
    """
    Sequentially execute nodes against the same mutable context.

    Args:
        ctx (Context): The initial context to pass through the nodes.
        nodes (Sequence[Node | AsyncNode]): A sequence of nodes to execute.

    Returns:
        None.
    """
    await async_sequential_exec(ctx, nodes)
