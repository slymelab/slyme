# Async API

Node and Wrapper share one immediate-or-awaitable completion protocol. Actual returned values, not whether the function uses `async def`, determine execution. A synchronous parent can receive an asynchronous Auto result.

```python
from slyme.node import Auto, Node, node, wrapper
from slyme.utils.continuation import await_result


@node
async def fetch(ctx, *, key: Auto[str]) -> str:
    return await remote_fetch(key)


@wrapper
async def trace(ctx, wrapped: Node, call_next, *, name: str):
    print(name, "start")
    try:
        return await await_result(call_next(ctx))
    finally:
        print(name, "end")
```

Use `await await_result(graph(ctx))` for either immediate or awaitable results. A forwarding synchronous wrapper may return `call_next(ctx)` unchanged, but synchronous statements after that call do not wait for it. No wrapper automatically rewrites user `try/finally`. Ordinary awaitables returned as execution results are awaited; wrap one in a container when it is data.

Async continuations are lazy until awaited or scheduled; a synchronous prefix may already have run. `await_result` neither starts an event loop nor offloads work. Use `asyncio.run(await_result(graph(ctx)))` only at a synchronous application entry point, and await within an existing loop. Do not block the event-loop thread; offloaded workers receive ordinary values and return results for owner-thread Context mutation.

Auto runs synchronous children inline. After the first awaitable child or cleanup, it schedules the pending child and remaining siblings concurrently when awaited, retaining result order. Every sibling is attempted; child failure or cancellation never cancels siblings. Errors are reported only after all children settle, with the first non-cancellation failure as primary and other failures attached through its cause. Application code owns timeouts and abort policies. Cancelling the outer evaluation Task follows asyncio cancellation propagation. Each child has an owned Context and distinct Scope; successful children dispose immediately, and final cleanup completes before error or cancellation propagation, including repeated cancellation. Returned values must not depend on child-owned resources. `sequential_exec(ctx, children)` instead stops at failure and shares the supplied Context; `sequential(nodes=children)` builds that ordering as a Node.

`ctx.effect(setup)` registers ownership before invoking setup. Await `await_result(ctx.effect(setup))` when setup may be asynchronous to obtain the early disposer. Cancelling that waiter does not cancel already scheduled setup; owner disposal waits for it and releases the acquired resource. Setup must undo partial acquisition if it fails.

Use `await await_result(ctx.dispose())` to finish mixed cleanup. Calling `dispose()` marks the Context as disposing and runs synchronous cleanup immediately; async cleanup is scheduled when awaited. A direct waiter's cancellation does not cancel scheduled cleanup but may return before completion. Await disposal again before shutdown to observe its retained result. Context does not own arbitrary Node tasks: stop and await them before disposal.
