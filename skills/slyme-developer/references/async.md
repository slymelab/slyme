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
async def fetch(
    ctx: Context,  # Same runtime position as the synchronous @expression.
    /,
    *,
    client,         # Ordinary build-time dependency.
    key: Auto[str], # Async Auto may resolve Ref or async/sync expressions.
) -> dict:
    # Use an async decorator only when the body awaits real asynchronous work.
    return await client.fetch(key)


@async_node
async def execute_async(
    ctx: Context,
    /,
    *,
    value: Auto[dict],
    output: Ref[dict],
    # A mixed sequence keeps the higher-order intrusion point extensible.
    nodes: Sequence[Union[Node, AsyncNode]],
) -> Context:
    # async_sequential_exec awaits AsyncNode children. Synchronous Node children
    # are dispatched through asyncio.to_thread so they do not block the event loop.
    ctx = await async_sequential_exec(ctx, nodes)
    # `output` and its concrete value follow the same trailing-underscore rule.
    output_ = value
    return ctx.set(output, output_)


@async_wrapper
async def retry(
    ctx: Context,  # Runtime Context.
    wrapped: AsyncNode,  # Wrapped async Node.
    # Unlike a sync wrapper, async call_next returns Awaitable[Context].
    call_next: Callable[[Context], Awaitable[Context]],
    /,
    *,
    attempts: int,
) -> Context:
    for attempt in range(attempts):
        try:
            # Async wrappers must await the next wrapper/Node in the onion chain.
            return await call_next(ctx)
        except TransientError:
            # Keep retry scoped to the Node carrying the wrapper. For per-item
            # retry, mount it on the per-item Node rather than the whole batch.
            if attempt + 1 == attempts:
                raise
    raise AssertionError("unreachable")
```

Prepared async nodes execute with `await node_exec(ctx)`. `async_sequential` builds a declarative mixed sequence; `async_sequential_exec` runs one inside a higher-order async node and dispatches synchronous children through `asyncio.to_thread`. Do not perform blocking I/O directly in an async function.
