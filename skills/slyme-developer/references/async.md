# Async Differences

Read this only for async or mixed sync/async workflows. All synchronous rules for signatures, Context, Ref, Auto, composition, and preparation still apply.

## Decorators and Execution

Use async decorators only around functions that actually await:

```python
from collections.abc import Awaitable, Callable

from slyme.context import Context, Ref
from slyme.node import (
    AsyncNode,
    Auto,
    async_expression,
    async_node,
    async_wrapper,
)


@async_expression
async def fetch_value(ctx: Context, /, *, url: str) -> str:
    return await http_get_text(url)


@async_node
async def store_value(
    ctx: Context,
    /,
    *,
    value: Auto[str],
    output: Ref[str],
) -> Context:
    # Auto can await an AsyncExpression before entering this body.
    output_ = value.strip()
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
            return await call_next(ctx)  # Async wrappers await the chain.
        except TransientError:
            if attempt + 1 == attempts:
                raise
    raise AssertionError("unreachable")
```

Prepare normally, then await execution:

```python
node_def = store_value(
    value=fetch_value(url="https://example.test/value"),
    output=output,
).add_wrappers(retry(attempts=3))

ctx = await node_def.prepare()(ctx)
```

## Mixed Sequences and Higher-Order Nodes

`async_sequential` accepts both sync and async nodes. Slyme awaits async children and dispatches sync children through `asyncio.to_thread`:

```python
from typing import Sequence, Union

from slyme.node import (
    AsyncNode,
    Node,
    async_node,
    async_sequential,
    async_sequential_exec,
)


@async_node
async def run_stages(
    ctx: Context,
    /,
    *,
    stages: Sequence[Union[Node, AsyncNode]],
) -> Context:
    return await async_sequential_exec(ctx, stages)


pipeline = async_sequential(nodes=[sync_step(...), async_step(...)])
ctx = await pipeline.prepare()(ctx)
```

Prefer the sequence parameter over a single child so builders can inject arbitrary stages without changing the higher-order node.

## Selection Rules

- Keep a primitive synchronous unless it needs `await`; do not convert an entire tree merely for stylistic uniformity.
- Use `async_sequential` at build time and `async_sequential_exec` inside an executing higher-order async node.
- Never call blocking I/O directly inside an async node. Use an async client or isolate unavoidable blocking work with `asyncio.to_thread`.
- Apply concurrency only when branches are independent and their Context merge semantics are explicit. Sequential state threading remains the safe default.
- Test async expressions, wrapper retry/error behavior, mixed sync/async ordering, and the final returned Context.
