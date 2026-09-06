# Core API

## Value-producing and effectful Nodes

```python
from slyme.context import ARG, Arg, Compose, Context, Ref, Schema, ref
from slyme.node import Auto, Node, node, sequential_exec, wrapper


R = Schema(
    {
        "input": {
            "value": ref(metadata={ARG: Arg(type=float, required=True)}),
        },
        "state": {"counter": ref()},
        "output": {"result": ref()},
        "request": {"id": ref()},
    }
)


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

An Auto Ref reads the supplied Context. Every Auto child Node runs with its own `ctx.fork()`, so its local writes are discarded after it returns. Call a child explicitly with `ctx`, or use `sequential_exec`, when later steps must observe those writes. In rendered Node trees, `?` marks a parameter that will be evaluated at call time.

Context mutation methods modify data in place and return `None`. A Node may return any value.

## Context layers and Compose

```python
root_ctx = Context(schema=R)
agent_ctx = root_ctx.fork()

remove_request = agent_ctx.add(R.resolve("request.id"), "request-1")

tools = Compose[str, tuple[str, ...]].collect()
remove_global = tools.add(root_ctx, "read")
remove_agent = tools.add(agent_ctx, "shell", metadata={"plugin": "shell"})

assert tools.resolve(agent_ctx) == ("shell", "read")

remove_agent()
remove_global()
remove_request()
```

Context reads follow C3 order by default and accept `local=True` for one local layer. Writes affect only the receiver. `Context.add()` rejects an existing local path and returns an idempotent disposer. `Compose.one()` selects the first visible value, `collect()` returns all visible values, and `merge()` combines mappings with first-visible key precedence.

Create an application root with `Context(data, schema=R)`. Every path must belong to its Schema declaration tree. Forks inherit the same `ctx.schema`; `ctx.declare(plugin_schema)` adds a plugin's immutable declarations for every existing and future fork. Pass direct parents as `Context(data, parents=(base, mixin))`; all parents must share one application root. `ctx.mro` is the immutable C3 order and `ctx.root` is its final entry. `base.fork(mixin)` is the empty-data shorthand. `to_dict()` returns a nested ordinary-dict projection; `flatten()` returns the exact visible Ref-to-value leaf mapping. Neither copies stored values.

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
        value=R.resolve("input.value"),
        scale=2.0,
    ),
    children=(increment(counter=R.resolve("state.counter")),),
    output=R.resolve("output.result"),
).add_wrappers(trace(name="execute"))

ctx = Context(
    {R.resolve("input.value"): 3.0, R.resolve("state.counter"): 0},
    schema=R,
)
result = root(ctx)
assert ctx.get(R.resolve("output.result")) == result
```

The Node graph stays mutable. Read build parameters with `root.get(name)`, replace them with `root.set(name, value)`, and restore defaults with `root.reset(name)`. Static parameter containers are passed directly to Node and Wrapper functions, so in-call mutations remain on the live element. A change affects subsequent calls without an explicit prepare phase. Call the relevant factory or Builder again when another independently configurable graph is needed, and copy mutable application values explicitly when they must not be shared.

Use named child parameters for stable roles and Python sequences or mappings for extensible physical composition. Use `sequential(...)` for a plain declarative pipeline and a custom higher-order Node when execution semantics differ.
