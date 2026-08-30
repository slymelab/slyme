# Lifecycle

Slyme uses one live `Node` graph rather than separate definition and execution trees. Creating a decorated function builds a mutable Node; calling it executes that same Node with its current parameters.

## Build and modify

```python
from slyme.context import Context, R
from slyme.node import Auto, node


@node
def process(ctx: Context, /, *, timeout: int = 30, data: Auto[list]):
    return timeout, data


task = process(data=[R.user.age, R.user.name])
task.timeout = 60
```

Node parameters and wrappers may be changed between calls. A change never requires recompiling the whole graph and becomes visible on the next call.

## Live calls and explicit clones

At the start of each Node or Wrapper call, Slyme:

1. reads and validates the object's current parameters;
2. separates static values from values that require Auto evaluation;
3. builds a temporary evaluation plan and wrapper chain;
4. passes static parameter containers directly to the user function.

There is no implicit frozen snapshot. Mutating a static `list`, `dict`, or other leaf from inside a Node or Wrapper mutates the live parameter and is visible to later calls. An Auto structure containing `Ref` or child Node leaves is reconstructed with the evaluated values, because evaluation produces a new result tree.

Use `node.clone()` when a branch needs an independent Node/Wrapper and parameter-PyTree structure. Use `context.clone()` when it needs an independent ContextData structure. Both operations preserve unregistered leaf identities; they are structural clones, not arbitrary deep copies.

## Auto values

Static parameter values and values retrieved from `Context` keep their normal Python mutability:

```python
ctx = Context()
ctx.update({R.a: 1, R.b: 2, R.items: [1, 2]})

process(data=[R.a, R.b])(ctx)  # Auto produces the evaluated list [1, 2]
process(data=R.items)(ctx)  # data is the list stored in Context
```

Call a Node directly with `node(ctx)` when managing Context yourself. Use `node.run(...)` as the application boundary when Slyme should prepare external inputs, validate `Arg` metadata, and extract outputs.
