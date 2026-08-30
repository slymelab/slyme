# Core API

## Value-producing and effectful Nodes

```python
from slyme.context import ARG, Arg, Context, R, Ref
from slyme.node import Auto, Node, node, sequential_exec, wrapper


@node
def calculate(ctx, *, value: Auto[float], scale: float = 1.0) -> float:
    return value * scale


@node
def increment(ctx, *, counter: Ref[int]) -> None:
    ctx.set(counter, ctx.get(counter) + 1)


@node
def execute(
    ctx,
    *,
    derived: Auto[float],
    children: tuple[Node, ...],
    output: Ref[float],
) -> float:
    sequential_exec(ctx, children)
    ctx.set(output, derived)
    return derived
```

A Node function has exactly one non-keyword-only runtime parameter. All build parameters are keyword-only. `Auto` resolves registered leaves such as `Ref` and value-producing `Node`; omit it when the function needs the object itself.

Context mutation methods modify data in place and return `None`. A Node may return any value.

## Wrappers

```python
@wrapper
def trace(ctx, wrapped: Node, call_next, *, name: str):
    print(name, "start")
    try:
        return call_next(ctx)
    finally:
        print(name, "end")
```

A Wrapper has exactly three non-keyword-only runtime parameters. Attach it only through `node.add_wrappers(...)`.

## Assembly and execution

```python
root = execute(
    derived=calculate(
        value=R.input.value(metadata={ARG: Arg(type=float, required=True)}),
        scale=2.0,
    ),
    children=(increment(counter=R.state.counter),),
    output=R.output.result,
).add_wrappers(trace(name="execute"))

result = root.run(
    inputs={R.input.value: 3.0, R.state.counter: 0},
    outputs=R.output.result,
)
```

The Node graph stays mutable. Build parameters are real attributes (`root.derived`, `root.children`, and so on), and assignment runs the parameter's `Spec` build logic. Static parameter containers are passed directly to Node and Wrapper functions, so in-call mutations remain on the live element. A change affects subsequent calls without an explicit prepare phase. Call `root.clone()` before making changes that need an independent Node/Wrapper and parameter-PyTree structure; unregistered leaf values remain shared.

Use named child parameters for stable roles and Python sequences or mappings for extensible physical composition. Use `sequential(...)` for a plain declarative pipeline and a custom higher-order Node when execution semantics differ.
