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

from collections.abc import Awaitable, Generator, Iterable, Sequence
from typing import Any

from slyme.context import Context
from slyme.utils.continuation import run
from slyme.utils.exception import BatchError, Result

from .core import Node, node

__all__ = ["sequential_exec", "sequential"]


def sequential_exec(ctx: Context, nodes: Iterable[Node]) -> None | Awaitable[None]:
    """Execute nodes in order, returning None or raising BatchError on failure."""

    def execute() -> Generator[Any, Any, None]:
        results: list[Result[Any]] = []
        for item in nodes:
            try:
                value = yield item(ctx)
            except BaseException as error:
                results.append(Result(error=error))
                raise BatchError(results) from error
            results.append(Result(value=value))

    return run(execute())


@node
def sequential(ctx: Context, /, *, nodes: Sequence[Node]) -> None | Awaitable[None]:
    """Execute nodes in order against the same mutable Context."""
    return sequential_exec(ctx, nodes)
