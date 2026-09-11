# Core API

## Value-producing and effectful Nodes

```python
from slyme.context import Compose, Context, Ref, Schema, Scope
from slyme.node import Auto, Node, node, sequential_exec, wrapper


R = Schema(
    {
        "input": {
            "value": Schema.leaf(float),
        },
        "state": {"counter": Schema.leaf()},
        "output": {"result": Schema.leaf()},
        "request": {"id": Schema.leaf()},
        "tools": Schema.leaf(replaceable=False),
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

An Auto Ref reads the supplied Context. Every Auto child Node runs in an owned child Context with a distinct `ctx.scope.fork()`. Slyme disposes that child, including its effects, before parent execution continues. Call a child explicitly with `ctx`, or use `sequential_exec`, when later steps must observe its writes.

`set()`, `update()`, `delete()`, and other ordinary Context mutations modify data in place and return `None`. `add()`, `declare()`, and `contribute()` return exact early disposers and are also removed automatically with their owning Context. A Node may return any value.

## Context lifetime, Scope visibility, and Compose

```python
root_ctx = Context(schema=R)
agent_scope = root_ctx.scope.fork(name="agent")
agent_ctx = root_ctx.fork(scope=agent_scope)

remove_request = agent_ctx.add(R.resolve("request.id"), "request-1")

tools = Compose[str, tuple[str, ...]].collect()
root_ctx.add(R.resolve("tools"), tools)
root_ctx.contribute(R.resolve("tools"), "read")
remove_agent = agent_ctx.contribute(
    R.resolve("tools"), "shell", metadata={"plugin": "shell"}
)

assert tools.resolve(agent_ctx.scope) == ("shell", "read")

remove_agent()
remove_request()
agent_ctx.dispose()
root_ctx.dispose()
```

Context reads follow the bound Scope's C3 order by default and accept `local=True` for that Scope's Compose-local identity. Writes always target the identity bound to the Context's Scope; no Context CRUD method accepts a separate `scope=` argument. Contexts in one application root that share a Scope therefore see the same data, and `ctx.bind(ref, identity=key)` can make different Scopes share one selected Context leaf. Independent Context roots keep separate data even when bound to the same Scope. `Context.add()` rejects an existing value at the bound identity. `Compose.one()` selects the first visible value, `collect()` returns all visible values, and `merge()` combines mappings with first-visible key precedence.

Create an application root with `Context(data, schema=R, scope=optional_scope)`. Every path must belong to its Schema declaration tree. A child has one `parent`, inherits `ctx.schema`, and is owned by that parent until disposal. `ctx.fork()` shares `ctx.scope`; pass a Scope explicitly when visibility should differ. `Scope.fork()` creates a single-parent child. Scope parents may come from unrelated roots when explicit C3 composition is needed: `Scope(name="combined", parents=(left, right))`. `ctx.scope.mro` is the visibility order, while `ctx.root` owns the application data store and lifetime subtree and holds their shared Schema reference. `to_dict()` returns a nested ordinary-dict projection; `flatten()` returns the exact visible Ref-to-value leaf mapping. Neither copies stored values.

## Effects and disposal

`ctx.effect(setup)` runs synchronous setup immediately; setup returns synchronous cleanup and the method returns an idempotent synchronous early disposer. `ctx.async_effect(setup)` also runs setup synchronously, but setup returns asynchronous cleanup and its early disposer must be awaited. Setup and cleanup cannot dispose their owner Context or an ancestor while running. Each Context disposes directly owned child Contexts and effects in LIFO order, recursively; cleanup continues after failures and then raises the first failure. `ctx.dispose()` first rejects a subtree containing asynchronous cleanup without partially tearing it down; use `await ctx.async_dispose()` for mixed cleanup. Idempotence means cleanup runs once and later disposal calls reproduce the same terminal failure. A disposed Context rejects further data and lifecycle operations.

Effect setup and cleanup cannot dispose their owner Context or an ancestor while running, and cleanup cannot re-enter its own disposer; Slyme rejects these operations with `RuntimeError`.

Use `ctx.contribute(ref, value, scope=target)` when a Compose stored at `ref` should receive a lifecycle-owned contribution. The Compose is looked up through `ctx.scope`; `scope` only selects the contribution's target and defaults to `ctx.scope`. Direct `compose.add(scope, value)` remains available when the caller will manage its returned disposer itself. `compose.bind(scope_a, scope_b, identity=key)` gives those Scopes one shared bucket in that Compose only. Binding is permanent for each live Scope and has no disposer; contributions remain independently reversible.

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

The Node graph stays mutable. Read build parameters with `root.get(name)`, replace them with `root.set(name, value)`, and restore defaults with `root.reset(name)`. Static parameter containers are passed directly to Node and Wrapper functions, so in-call mutations remain on the live element. A change affects subsequent calls without an explicit prepare phase. Call the relevant factory or assembly function again when another independently configurable graph is needed, and copy mutable application values explicitly when they must not be shared.

Use named child parameters for stable roles and Python sequences or mappings for extensible physical composition. Use `sequential(...)` for a plain declarative pipeline and a custom higher-order Node when execution semantics differ.
