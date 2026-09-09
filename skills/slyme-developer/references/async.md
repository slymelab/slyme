# Async API

The unified `@node` and `@wrapper` decorators detect `async def`. Live parameter, Context lifetime, and Scope visibility semantics are identical to synchronous Nodes.

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

Use `await node(ctx)` for execution. Application code owns Context construction, external inputs, and output reads. `async_sequential` builds a declarative mixed sequence; `async_sequential_exec` executes synchronous children inline and awaits asynchronous children. Do not perform blocking I/O directly inside an async function.

During asynchronous Auto evaluation, asynchronous child Nodes may overlap at suspension points. Synchronous child Nodes execute inline on the event-loop thread and finish before their evaluation task yields. Each child receives an owned Context with a distinct child Scope, and Slyme finishes `async_dispose()` before propagating cancellation or entering the parent function. Values returned by a child must not depend on resources owned by that child Context. The primary child failure remains primary, while sibling and cleanup failures are attached through its cause. Mutable leaf objects and external side effects remain shared. A Context tree and its mutable Schema and Compose objects are single-thread-owned; explicitly offloaded work must operate on ordinary values and return results for owner-thread mutation. Synchronous Auto evaluation runs children in evaluation order and rejects `async_effect()` before setup. Use `async_sequential_exec(ctx, children)` when ordered steps should share one Context and its lifetime.

Cancelling a direct `async_dispose()` waiter leaves cleanup running and may return before it finishes. Await the same method again, or keep another waiter, to observe its retained success or failure before shutdown. Context does not own unrelated tasks that use it; stop and await them before disposal.
