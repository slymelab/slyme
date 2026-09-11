# Async API

Node and Wrapper share one immediate-or-awaitable completion protocol. Actual returned values, not whether the function uses `async def`, determine execution. A synchronous parent can receive an asynchronous Auto result.

```python
from slyme.node import Auto, Node, node, wrapper
from slyme.utils.awaitable import resolve


@node
async def fetch(ctx, *, key: Auto[str]) -> str:
    return await remote_fetch(key)


@wrapper
async def trace(ctx, wrapped: Node, call_next, *, name: str):
    print(name, "start")
    try:
        return await resolve(call_next(ctx))
    finally:
        print(name, "end")
```

Use `await resolve(graph(ctx))` for either immediate or awaitable results. A forwarding synchronous wrapper may return `call_next(ctx)` unchanged, but synchronous statements after that call do not wait for it. No wrapper automatically rewrites user `try/finally`. Ordinary awaitables returned as execution results are awaited; wrap one in a container when it is data.

Async continuations are lazy until awaited or scheduled; a synchronous prefix may already have run. `resolve` neither starts an event loop nor offloads work. Use `asyncio.run(resolve(graph(ctx)))` only at a synchronous application entry point, and await within an existing loop. Do not block the event-loop thread; offloaded workers receive ordinary values and return results for owner-thread Context mutation.

Auto runs synchronous children inline. After the first awaitable child or cleanup, it schedules the pending child and remaining siblings concurrently when awaited, retaining result order. Each child has an owned Context and distinct Scope. Child cleanup finishes before parent execution or cancellation propagation, including repeated cancellation. Returned values must not depend on child-owned resources. Sibling and cleanup failures remain attached to the primary exception; cleanup failure during cancellation remains observable. `sequential_exec(ctx, children)` instead waits for each step and shares the supplied Context; `sequential(nodes=children)` builds that ordering as a Node.

`ctx.effect(setup)` registers ownership before invoking setup. Await `resolve(ctx.effect(setup))` when setup may be asynchronous to obtain the early disposer. Cancelling that waiter does not cancel already scheduled setup; owner disposal waits for it and releases the acquired resource. Setup must undo partial acquisition if it fails.

Use `await resolve(ctx.dispose())` to finish mixed cleanup. Calling `dispose()` marks the Context as disposing and runs synchronous cleanup immediately; async cleanup is scheduled when awaited. A direct waiter's cancellation does not cancel scheduled cleanup but may return before completion. Await disposal again before shutdown to observe its retained result. Context does not own arbitrary Node tasks: stop and await them before disposal.
