# Async Differences

All core rules remain unchanged. The unified `@node`, `@expression`, and
`@wrapper` decorators automatically recognize `async def` functions. Use an
async function only when its body actually awaits.

```python
from collections.abc import Awaitable, Callable, Sequence
from typing import Union

from slyme.context import Context, Ref
from slyme.node import (
    AsyncNode,
    Auto,
    Node,
    async_sequential_exec,
    expression,
    node,
    wrapper,
)


@expression
async def fetch(
    ctx: Context,  # Same runtime position as the synchronous @expression.
    /,
    *,
    client,         # Ordinary build-time dependency.
    key: Auto[str], # Async Auto may resolve Ref or async/sync expressions.
) -> dict:
    # Use an async decorator only when the body awaits real asynchronous work.
    return await client.fetch(key)


@node
async def execute_async(
    ctx: Context,
    /,
    *,
    value: Auto[dict],
    output: Ref[dict],
    # A mixed sequence keeps the higher-order composition slot extensible.
    nodes: Sequence[Union[Node, AsyncNode]],
) -> Context:
    # async_sequential_exec awaits AsyncNode children. Synchronous Node children
    # are dispatched through asyncio.to_thread so they do not block the event loop.
    ctx = await async_sequential_exec(ctx, nodes)
    # `output` and its concrete value follow the same trailing-underscore rule.
    output_ = value
    return ctx.set(output, output_)


@wrapper
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

Automatic mode detection checks the callable itself, not its return annotation.
For the uncommon case where a regular `def` returns an Awaitable, explicitly
use `@node(mode="async")` or `@wrapper(mode="async")`.

Use `await node.run(...)` at the application boundary; it follows the same input, output, and Context contract as synchronous `Node.run(...)`. Call a prepared async Exec directly only when managing Context explicitly. `async_sequential` builds a declarative mixed sequence; `async_sequential_exec` runs one inside a higher-order async node and dispatches synchronous children through `asyncio.to_thread`. Do not perform blocking I/O directly in an async function.
