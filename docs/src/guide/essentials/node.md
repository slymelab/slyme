# Node

`@node` turns a function into a factory for mutable, directly callable `Node` objects. A Node may update `Context`, perform side effects, coordinate child Nodes, or return a derived value for `Auto` injection.

## Define and create a Node

A Node function has exactly one non-keyword-only runtime parameter. Every build parameter must be keyword-only:

```python
from slyme.context import Context, Ref, Schema
from slyme.node import Auto, node

R = Schema({"input": {"x": Schema.leaf()}, "output": {"total": Schema.leaf()}})


@node
def add(ctx: Context, *, x: Auto[int], y: Auto[int], output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add(x=R.resolve("input.x"), y=2, output=R.resolve("output.total"))
```

Runtime parameter names and annotations are optional; Slyme identifies runtime and build parameters by parameter count and keyword-only placement.
Variadic `*args` and `**kwargs` parameters are not supported: runtime arity and
build parameter names must remain explicit.

## Execute

Call a Node directly when managing Context yourself:

```python
ctx = Context(schema=R)
ctx.set(R.resolve("input.x"), 3)
result = task(ctx)  # 5
```

The caller owns Context construction, external input handling, and output extraction. Core Node execution neither infers a Schema nor creates a Context implicitly.

There is no Def/Exec conversion or `prepare()` step. Each call binds and resolves the current parameters and wrappers.

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

`Auto` recursively resolves registered evaluator leaves such as `Ref` and `Node`. A Ref reads the supplied Context directly. Each value-producing child Node receives its own `ctx.fork()`, so its local Context writes do not leak into the parent or a sibling Auto child. Returned values, mutations to shared leaf objects, and external side effects are not isolated. Without `Auto`, Ref and Node objects are passed through unchanged.

Node rendering shows the current parameter values. A `?` prefix on a parameter edge means that the value will be evaluated from the supplied Context when the Node runs.

Missing required build parameters are represented by `UNDEFINED` and rejected when the Node is called. Use `UNSET` to request a declared default explicitly.

## Dynamic modification

Node and Wrapper parameters remain mutable between calls through an explicit parameter API:

```python
root.get("child").set("value", 10)
assert root(Context()) == 11
```

Use `get(name)` to read, `set(name, value)` to replace, and `reset(name)` to restore a parameter's declared default or `UNDEFINED`. The read-only `params` mapping exposes all current parameters. Parameter names may overlap framework attributes such as `func`, `get`, or `wrappers` because parameters are not projected as object attributes.

At call time, static parameter containers are passed directly to the user function. Mutating one therefore updates the live Node or Wrapper parameter. Auto parameters containing evaluator leaves are reconstructed with their resolved values.

Call the Node factory or a Builder again when another independently configurable graph is required. Copy mutable application values explicitly according to their own semantics; Slyme does not guess which shared references should be duplicated.

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

Wrappers use onion ordering and read their live parameters when invoked.

## Composition structure

Node and Wrapper parameters may contain arbitrary values and nested PyTrees, including other Nodes or Wrappers. Slyme does not impose a global legality check on that object graph. Only an object's active execution role is constrained: wrapper modes must match their Node, `sequential` accepts only synchronous Nodes, and `async_sequential` accepts synchronous or asynchronous Nodes.

## Sequential composition

Use `sequential(nodes=[...])` for a declarative synchronous sequence and `async_sequential(nodes=[...])` for mixed asynchronous execution. Their corresponding `*_exec` helpers execute an existing iterable with the same Context; unlike Auto child evaluation, these helpers intentionally share local writes between steps.
