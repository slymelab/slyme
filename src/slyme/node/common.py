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

from __future__ import annotations

from collections.abc import Awaitable, Generator, Iterable, Sequence
from typing import Any

from slyme.context import Context
from slyme.utils.execution import continuation

from .core import Node, node

__all__ = ["sequential_exec", "sequential"]


@continuation
def sequential_exec(ctx: Context, nodes: Iterable[Node]) -> Generator[Any, Any, None]:
    """Execute nodes in order."""
    for item in nodes:
        yield item(ctx)


@node
def sequential(ctx: Context, /, *, nodes: Sequence[Node]) -> None | Awaitable[None]:
    """Execute nodes in order against the same mutable Context."""
    return sequential_exec(ctx, nodes)
