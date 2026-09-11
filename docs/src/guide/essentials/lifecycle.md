# Lifecycle

Slyme uses one live `Node` graph rather than separate definition and execution trees. Creating a decorated function builds a mutable Node; calling it executes that same Node with its current parameters.

## Build and modify

```python
from slyme.context import Context, Schema
from slyme.node import Auto, node

R = Schema({"user": {"age": Schema.leaf(), "name": Schema.leaf()}, "a": Schema.leaf(), "b": Schema.leaf(), "items": Schema.leaf()})


@node
def process(ctx: Context, /, *, timeout: int = 30, data: Auto[list]):
    return timeout, data


task = process(data=[R.resolve("user.age"), R.resolve("user.name")])
task.set("timeout", 60)
```

Node parameters and wrappers may be changed between calls. A change never requires recompiling the whole graph and becomes visible on the next call.

## Live calls and explicit branches

At the start of each Node or Wrapper call, Slyme:

1. reads and validates the object's current parameters;
2. separates static values from values that require Auto evaluation;
3. builds a temporary evaluation plan and wrapper chain;
4. passes static parameter containers directly to the user function.

There is no implicit frozen snapshot. Mutating a static `list`, `dict`, or other leaf from inside a Node or Wrapper mutates the live parameter and is visible to later calls. An Auto structure containing `Ref` or child Node leaves is reconstructed with the evaluated values, because evaluation produces a new result tree.

Call the relevant Node factory or assembly function again when another independently configurable graph is required. `context.fork()` creates an owned lifetime child and shares `context.scope` by default. Use `context.fork(scope=context.scope.fork())` when that child needs a separate local data layer with live Scope C3 lookup. Use `Context(context.flatten(), schema=context.schema)` when current visible bindings must be materialized into a new application root. None of these operations copies application values.

A Context owns its child Contexts and the cleanup registered through `effect()`, `async_effect()`, `add()`, and `declare()`. Each Context processes its direct ownership in last-in-first-out order, recursively. It does not automatically own arbitrary tasks that happen to use it: application code must stop and await those tasks before disposing their Context. `dispose()` handles a wholly synchronous subtree; `await async_dispose()` handles both synchronous and asynchronous cleanup. The returned registration disposers permit early cleanup without changing this ownership model.

## Auto values

Static parameter values and values retrieved from `Context` keep their normal Python mutability:

```python
ctx = Context(schema=R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=[R.resolve("a"), R.resolve("b")])(ctx)  # Auto produces the evaluated list [1, 2]
process(data=R.resolve("items"))(ctx)  # data is the list stored in Context
```

Auto Ref values are read from `ctx`. Every Auto child Node instead executes with an owned child Context and a distinct child Scope. Synchronous evaluation disposes that child before continuing and rejects `async_effect()` before setup; asynchronous evaluation waits for child cleanup before propagating cancellation. A synchronous child reached during asynchronous evaluation runs inline on the event-loop thread and cannot be cancelled until it returns. If one Auto child fails, that remains the primary error while failures from cancelled siblings and their cleanup remain attached through the exception cause. If cancellation is the only primary result but cleanup fails, the cleanup failure is raised with the cancellation as its cause so the failure cannot be hidden by `asyncio` cancellation state. Values returned by an Auto child must not depend on resources owned by its child Context: those resources are closed before the parent function runs. Transfer ownership explicitly or use a longer-lived Context when returning such a value. The child still shares mutable leaf objects and cannot undo files, network requests, or other external side effects that did not register cleanup.

Explicit orchestration has different semantics. Calling a Node directly or using `sequential_exec(ctx, children)` passes the selected Context itself, so those steps intentionally observe one another's local writes.

Cancellation of a direct `async_dispose()` waiter does not cancel cleanup, but that waiter may return before cleanup finishes. Await `async_dispose()` again, or maintain another waiter, to observe the retained terminal result before application shutdown.

Application code owns Context construction, external input validation, and output extraction. Core execution has one entry point: call the Node directly with `node(ctx)`.
