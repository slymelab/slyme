# Async Differences

All core rules remain unchanged. Use async decorators only for functions that actually await.

```python
from collections.abc import Awaitable, Callable, Sequence
from typing import Union

from slyme.context import Context, Ref
from slyme.node import (
    AsyncNode,
    Auto,
    Node,
    async_expression,
    async_node,
    async_sequential_exec,
    async_wrapper,
)


@async_expression
async def fetch(ctx: Context, /, *, client, key: Auto[str]) -> dict:
    return await client.fetch(key)  # Async Auto can evaluate async expressions.


@async_node
async def execute_async(
    ctx: Context,
    /,
    *,
    value: Auto[dict],
    output: Ref[dict],
    nodes: Sequence[Union[Node, AsyncNode]],
) -> Context:
    ctx = await async_sequential_exec(ctx, nodes)
    output_ = value
    return ctx.set(output, output_)


@async_wrapper
async def retry(
    ctx: Context,
    wrapped: AsyncNode,
    call_next: Callable[[Context], Awaitable[Context]],
    /,
    *,
    attempts: int,
) -> Context:
    for attempt in range(attempts):
        try:
            return await call_next(ctx)
        except TransientError:
            if attempt + 1 == attempts:
                raise
    raise AssertionError("unreachable")
```

Prepared async nodes execute with `await node_exec(ctx)`. `async_sequential` builds a declarative mixed sequence; `async_sequential_exec` runs one inside a higher-order async node and dispatches synchronous children through `asyncio.to_thread`. Do not perform blocking I/O directly in an async function.
