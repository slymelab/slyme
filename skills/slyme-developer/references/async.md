# Async API

The unified `@node` and `@wrapper` decorators detect `async def`. Build parameters and call-local snapshot semantics are identical to synchronous Nodes.

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

Use `await node(ctx)` for direct execution or `await node.run(...)` at an application boundary. `async_sequential` builds a declarative mixed sequence; `async_sequential_exec` dispatches synchronous children through `asyncio.to_thread`. Do not perform blocking I/O directly inside an async function.
