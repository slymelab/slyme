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

from collections.abc import Awaitable, Iterable, Sequence
from inspect import isawaitable
from typing import Any

from slyme.context import Context
from slyme.utils.awaitable import resolve

from .core import Node, node

__all__ = ["sequential_exec", "sequential"]


def sequential_exec(ctx: Context, nodes: Iterable[Node]) -> None | Awaitable[None]:
    """Execute nodes in order, waiting for each completion before the next."""
    iterator = iter(nodes)

    async def continue_async(pending: Awaitable[Any]) -> None:
        await pending
        for item in iterator:
            await resolve(item(ctx))

    for item in iterator:
        result = item(ctx)
        if isawaitable(result):
            return continue_async(result)
    return None


@node
def sequential(ctx: Context, /, *, nodes: Sequence[Node]) -> None | Awaitable[None]:
    """Execute nodes in order against the same mutable Context."""
    return sequential_exec(ctx, nodes)
