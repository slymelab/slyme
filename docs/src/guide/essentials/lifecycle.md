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

Call the relevant Node factory or Builder again when another independently configurable graph is required. Use `context.fork()` for an empty local Context layer with live C3 lookup into its parents. Use `Context(context.flatten(), schema=context.schema)` when current visible bindings must be materialized into a new application root. Neither operation copies application values.

## Auto values

Static parameter values and values retrieved from `Context` keep their normal Python mutability:

```python
ctx = Context(schema=R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=[R.resolve("a"), R.resolve("b")])(ctx)  # Auto produces the evaluated list [1, 2]
process(data=R.resolve("items"))(ctx)  # data is the list stored in Context
```

Auto Ref values are read from `ctx`. Every Auto child Node instead executes with its own `ctx.fork()`: child-local writes are discarded after its return and cannot race with writes from sibling Auto children. The fork still shares mutable leaf objects and does not undo files, network requests, or other external side effects.

Explicit orchestration has different semantics. Calling a Node directly or using `sequential_exec(ctx, children)` passes the selected Context itself, so those steps intentionally observe one another's local writes.

Application code owns Context construction, external input validation, and output extraction. Core execution has one entry point: call the Node directly with `node(ctx)`.
