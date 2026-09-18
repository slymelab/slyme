# Lifecycle

`Lifecycle` is also an independently usable effect owner, exported from `slyme.context`. `Lifecycle(parent=owner)` joins an ownership tree without requiring Schema, Store, or Scope. Its `effect()`, `dispose()`, and `adispose()` follow the same rules as Context. An optional synchronous `finalize` callback runs once after all owned cleanup, including when cleanup fails; it is not an independently revocable effect. Context uses this finalizer to release its data viewers and, for an application root, detach its Store from Schema. Register application cleanup with `ctx.effect()` rather than overriding `Context.dispose()`: the ownership tree is traversed by Lifecycle.

Slyme uses one live `Node` graph rather than separate definition and execution trees. Creating a decorated function builds a mutable Node; calling it executes that same Node with its current parameters.

## Build and modify

```python
from slyme.context import Context, Schema
from slyme.node import Auto, node

R = Schema(
    {
        "user": {"age": Schema.leaf(), "name": Schema.leaf()},
        "a": Schema.leaf(),
        "b": Schema.leaf(),
        "items": Schema.leaf(),
    }
)


@node
def process(ctx: Context, /, *, timeout: int = 30, data: list):
    return timeout, data


task = process(data=Auto([R.resolve("user.age"), R.resolve("user.name")]))
task.set("timeout", 60)
```

Node parameters and wrappers may be changed between calls. A change never requires recompiling the whole graph and becomes visible on the next call.

## Live calls and explicit branches

At the start of each Node or Wrapper call, Slyme:

1. shallowly snapshots saved bindings and applies this call's keyword overrides;
2. separates ordinary values from explicitly wrapped Auto trees;
3. builds the wrapper chain and evaluates Auto parameters before each user function invocation;
4. passes static parameter containers directly to the user function.

Parameter bindings are shallow-snapshotted, not deep-copied. Mutating a non-Auto `list`, `dict`, or other leaf from inside a Node or Wrapper mutates the live parameter and is visible to later calls. Every Auto parameter is traversed and its containers reconstructed according to Tree rules, whether or not they contain evaluatable leaves. Ordinary leaves and evaluator results remain shared.

Call the relevant Node factory or assembly function again when another independently configurable graph is required. `context.fork()` creates an owned lifetime child and shares `context.scope` by default. Use `context.fork(scope=context.scope.fork())` when that child needs a separate local data layer with live Scope C3 lookup. To materialize `assign` fields into a new root, create it, declare the copied paths, then call `snapshot.update(context.get("app").flatten())`; `register` fields require explicit `register()` calls on the new owner. None of these operations copies application values.

A Context owns its children and cleanup registered through `effect()`, `register()`, and `declare()`, releasing direct ownership recursively in LIFO order. `dispose()` returns `None` on synchronous completion or an awaitable when cleanup is asynchronous; `await await_result(ctx.dispose())` handles either. Context does not own arbitrary tasks using it: stop and await those tasks before disposal.

Calling `dispose()` immediately marks the Context as disposing and runs its synchronous portion; await its asynchronous portion to schedule it. Early cleanup stays owned until completion, so owner disposal joins it. Removing an early registration preserves the remaining release order.

Each owned cleanup finishes before the next starts, including asynchronous cleanup. A failure or cleanup cancellation does not skip remaining ownership or Scope release. Context reports owned cleanup failures as an exception group containing only failures in cleanup execution order; subsequent disposal calls observe that same terminal result without repeating cleanup. Context's generator loop orders cleanup, while a separate shared completion protects cleanup from waiter cancellation and supports repeated waits.

## Auto values

Static parameter values and values retrieved from `Context` keep their normal Python mutability:

```python
ctx = Context()
ctx.declare(R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=Auto([R.resolve("a"), R.resolve("b")]))(
    ctx
)  # Auto produces the evaluated list [1, 2]
process(data=Auto(R.resolve("items")))(ctx)  # data is the list stored in Context
```

Auto Ref values are read from `ctx`. Every Auto child Node instead executes with an owned child Context and a distinct child Scope. Child calls and synchronous cleanup run inline; asynchronous results are scheduled concurrently when awaited, retaining input order. Each child disposes its Context in `finally` on success or failure, independently of other siblings. Without caller cancellation, evaluation waits for every child and its cleanup before reporting errors or running the parent function.

Auto attempts every sibling and every evaluator group, even after a synchronous failure. Evaluator groups are independent and may execute concurrently; a Ref lookup failure does not prevent Node evaluation. A child failure or cancellation does not cancel siblings: evaluation waits for all of them. Failures form nested exception groups: the outer group follows evaluator-group order of first appearance, and built-in evaluator groups follow their inputs' order. Only failures are included; each evaluation group's message lists the original failing indices. Partial successful results are not exposed. A child cleanup failure becomes that child's error; if the Node also failed, Python's `finally` semantics retain the Node exception as the cleanup error's `__context__`. Node and Wrapper record ordinary groups through `__cause__`; groups containing non-`Exception` control failures propagate unchanged. Exception objects returned as ordinary values remain data. A child that never finishes keeps evaluation pending; application code owns timeouts and abort policies.

Cancelling the outer evaluation Task propagates to its pending children through asyncio, without aggregating partial evaluation results. Each child enters its `finally` cleanup. Cancellation during the cleanup wait can let evaluation exit while Context-owned cleanup continues in the background. A synchronous child runs inline on the event-loop thread and cannot be interrupted while executing. Task cancellation does not guarantee that underlying network, thread, or process work has stopped; application adapters own that behavior.

Values returned by an Auto child must not depend on resources owned by its child Context: those resources are closed before the parent function runs. Transfer ownership explicitly or use a longer-lived Context when returning such a value. The child still shares mutable leaf objects and cannot undo files, network requests, or other external side effects that did not register cleanup.

Explicit orchestration has different semantics. Calling a Node directly or using `sequential_exec(ctx, children)` passes the selected Context itself, so those steps intentionally observe one another's local writes.

Cancelling a direct `await await_result(ctx.dispose())` waiter does not cancel scheduled cleanup, but that waiter may exit first. Await the same disposal again before shutdown to observe its retained result. A parent joins child cleanup only while that child remains owned. Auto does not return its temporary child Contexts; after cancelled evaluation, a background cleanup failure may therefore reach neither the caller nor a later parent disposal.

Application code owns Context construction, external input validation, and output extraction. Invoke the graph with `node(ctx)`, or use `await node.acall(ctx)` for an always-awaitable result. `await ctx.adispose()` similarly adapts disposal without changing its execution or ownership rules.
