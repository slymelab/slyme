# Async API

The unified `@node` and `@wrapper` decorators detect `async def`. Live parameter and Context-layering semantics are identical to synchronous Nodes.

```python
from slyme.context import Context, Ref
from slyme.node import AsyncNode, Auto, node, wrapper


@node
async def fetch(ctx, *, key: Auto[str]) -> str:
    return await remote_fetch(key)


@wrapper
async def trace(ctx, wrapped: AsyncNode, call_next, *, name: str):
    print(name, "start")
    try:
        return await call_next(ctx)
    finally:
        print(name, "end")
```

Use `await node(ctx)` for execution. Application code owns Context construction, external inputs, and output reads. `async_sequential` builds a declarative mixed sequence; `async_sequential_exec` dispatches synchronous children through `asyncio.to_thread`. Do not perform blocking I/O directly inside an async function.

During asynchronous Auto evaluation, synchronous and asynchronous child Nodes may run concurrently; synchronous children are dispatched through `asyncio.to_thread`. Each child receives a distinct `ctx.fork()`, so their local Context writes are isolated even though mutable leaf objects and external side effects remain shared. Synchronous Auto evaluation runs children in evaluation order. Use `async_sequential_exec(ctx, children)` when ordered steps should share one Context.
