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

`run(generator)` from `slyme.utils.continuation` drives ordinary generator control flow: write `value = yield operation()` for immediate or asynchronous results. `run()` executes the synchronous prefix and returns a value or an unscheduled awaitable remainder; use `await await_result(run(generator))` for either case. Awaited errors and cancellation are thrown at the suspended yield, so native `try/except/finally` defines handling and cleanup. Only yielded outer values are awaited; containers and the final return value stay unchanged. The driver does not schedule tasks, collect batch results, or own resources. Use `yield from` for generator delegation; a Node returning a generator must explicitly drive it with `run()`.

Auto uses batches both across independent evaluator groups and within Ref and Node evaluation. A Ref failure does not prevent Node evaluation. Calls execute inline before scheduling their asynchronous results concurrently when awaited. Every sibling is attempted; child failure or cancellation never cancels siblings. Completed batches retain errors in nested `BatchError` objects from `slyme.utils.exception`; final child cleanup errors form the Node batch's cause. Cancelling the outer evaluation Task follows asyncio propagation without aggregating partial batch results. Application code owns timeouts and abort policies. Each child has an owned Context and distinct Scope; successful children dispose immediately, and final cleanup completes before error or cancellation propagation, including repeated cancellation. Returned values must not depend on child-owned resources. `sequential_exec(ctx, children)` instead stops at failure and shares the supplied Context; `sequential(nodes=children)` builds that ordering as a Node.

`ctx.effect(setup)` registers ownership before invoking setup. Await `await_result(ctx.effect(setup))` when setup may be asynchronous to obtain the early disposer. Cancelling that waiter does not cancel already scheduled setup; owner disposal waits for it and releases the acquired resource. Setup must undo partial acquisition if it fails.

Use `await await_result(ctx.dispose())` to finish mixed cleanup. Calling `dispose()` marks the Context as disposing and runs synchronous cleanup immediately; async cleanup is scheduled when awaited. A direct waiter's cancellation does not cancel scheduled cleanup but may return before completion. Await disposal again before shutdown to observe its retained result. Context does not own arbitrary Node tasks: stop and await them before disposal.
