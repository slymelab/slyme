# Node

`@node` turns a function into a factory for mutable, directly callable `Node` objects. A Node may update `Context`, perform side effects, coordinate child Nodes, or return a derived value for `Auto` injection.

## Define and create a Node

A Node function has exactly one non-keyword-only runtime parameter. Every build parameter must be keyword-only:

```python
from slyme.context import Context, R, Ref
from slyme.node import Auto, node

@node
def add(ctx: Context, *, x: Auto[int], y: Auto[int], output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result

task = add(x=R.input.x, y=2, output=R.output.total)
```

Runtime parameter names and annotations are optional; Slyme identifies runtime and build parameters by parameter count and keyword-only placement.

## Execute

Call a Node directly when managing Context yourself:

```python
ctx = Context()
ctx.set(R.input.x, 3)
result = task(ctx)  # 5
```

Use `run()` as the application boundary when inputs, `Arg` validation, CLI parsing, or output extraction are needed:

```python
result = task.run(inputs={R.input.x: 3}, outputs=R.output.total)
```

There is no Def/Exec conversion or `prepare()` step. Each call validates and resolves a local immutable snapshot of the current parameters and wrappers.

## Parameters and Auto

Every build parameter has a `Spec`. `Auto[T]` is shorthand for enabling automatic evaluation:

```python
@node
def parent(ctx, *, child: Auto[int]):
    return child + 1

@node
def child(ctx, *, value: int):
    return value

root = parent(child=child(value=4))
assert root(Context()) == 5
```

`Auto` recursively resolves registered evaluator leaves such as `Ref` and `Node`. Without `Auto`, those objects are passed through unchanged.

Missing required build parameters are represented by `UNDEFINED` and rejected when the Node is called. Use `UNSET` to request a declared default explicitly.

## Dynamic modification

Node and Wrapper parameters remain mutable between calls:

```python
root["child"]["value"] = 10
assert root(Context()) == 11
```

At call time, ordinary parameter containers are recursively frozen (`list` to `tuple`, `dict` to a read-only mapping). Node and Wrapper objects remain leaves, preventing the snapshot operation from traversing composition cycles.

## Wrappers

`@wrapper` functions have exactly three non-keyword-only runtime parameters: Context, the wrapped Node, and the next callable. Build parameters remain keyword-only.

```python
from collections.abc import Callable
from slyme.node import Node, wrapper

@wrapper
def trace(ctx, wrapped: Node, call_next: Callable, *, name: str):
    print(name, "start")
    result = call_next(ctx)
    print(name, "end")
    return result

task.add_wrappers(trace(name="add"))
```

Wrappers use onion ordering and also resolve their own call-local parameter snapshots.

## Node structure validation {#node-struct}

`check_node_structure(root)` inspects the physical Node graph and validates wrapper placement and synchronous/asynchronous containment. `@builder` invokes it automatically unless structure checking is disabled.

## Sequential composition

Use `sequential(nodes=[...])` for a declarative synchronous sequence and `async_sequential(nodes=[...])` for mixed asynchronous execution. Their corresponding `*_exec` helpers execute an existing iterable inside a higher-order Node.
