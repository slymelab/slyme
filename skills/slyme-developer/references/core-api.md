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
        "request": {"id": Schema.leaf(mode="register")},
        "tools": Schema.leaf(mode="register"),
    }
)


@node
def calculate(ctx, *, value: float, scale: float = 1.0) -> float:
    return value * scale


@node
def increment(ctx, *, counter: Ref[int]) -> None:
    ctx.set(counter, ctx.get(counter) + 1)


@node
def execute(
    ctx,
    *,
    derived: float,
    children: tuple[Node, ...],
    output: Ref[float],
) -> float:
    sequential_exec(ctx, children)
    ctx.set(output, derived)
    return derived
```

Factories accept keyword bindings without inspecting function signatures. Node execution passes Context positionally and bindings by keyword; Python handles defaults, variadic parameters, and argument errors. Wrap a bound parameter tree in `Auto(...)` to resolve registered leaves such as Ref and value-producing Node; omit the wrapper when the function needs the object itself.

An Auto Ref reads the supplied Context. Every Auto child Node runs in an owned child Context with a distinct `ctx.scope.fork()`. Slyme disposes that child, including its effects, before parent execution continues. Call a child explicitly with `ctx`, or use `sequential_exec`, when later steps must observe its writes.

`Schema.leaf()` defaults to `mode="assign"`: `set()`, `update()`, and `delete()` modify data in place and return `None`. Declare `mode="register"` for `register()`, which returns an exact early disposer and is removed automatically with its owning Context. Each mode rejects the other mode's writes. `declare()` also returns an owned early disposer. A Node returns an immediate value or an awaitable completion.

## Context lifetime, Scope visibility, and Compose

```python
root_ctx = Context()
root_ctx.declare(R)
agent_scope = root_ctx.scope.fork(name="agent")
agent_ctx = root_ctx.fork(scope=agent_scope)

remove_request = agent_ctx.register(R.resolve("request.id"), "request-1")

tools = Compose[str, tuple[str, ...]].collect()
root_ctx.register(R.resolve("tools"), tools)
root_ctx.effect(lambda: tools.add(root_ctx.scope, "read"))
remove_agent = agent_ctx.effect(
    lambda: agent_ctx.get(R.resolve("tools")).add(
        agent_ctx.scope, "shell", metadata={"plugin": "shell"}
    )
)

assert tools.resolve(agent_ctx.scope) == ("shell", "read")

remove_agent()
remove_request()
agent_ctx.dispose()
root_ctx.dispose()
```

Context reads follow the bound Scope's C3 order by default and accept `local=True` for that Scope's Compose-local identity. Writes always target the identity bound to the Context's Scope; no Context CRUD method accepts a separate `scope=` argument. Contexts in one application root that share a Scope therefore see the same data. `ctx.isolate(ref, identity=key)` creates an owned child that blocks inherited values for that leaf; calls with the same identity share the selected isolated storage. Independent Context roots keep separate data even when bound to the same Scope. `Context.register()` rejects an existing value at the bound identity. `Compose.one()` selects the first visible value, `collect()` returns all visible values, and `merge()` combines mappings with first-visible key precedence.

Create an application root with `Context(scope=optional_scope)`, declare paths with `ctx.declare(R)`, then assign values with `ctx.update(data)`. Schema import copies definitions with independent ownership; later source changes do not propagate. Every path must belong to the application Schema. A child has one `parent`, shares its private Schema and ContextStore, and owns a distinct Lifecycle attached to its parent's Lifecycle. Resolve application-wide declarations through `ctx.resolve(path)`, `ctx.resolve_entry(path)`, and `ctx.entries`; entries include containers and unset leaves. `ctx.fork()` shares `ctx.scope`; pass a Scope explicitly when visibility should differ. `Scope.fork()` creates a single-parent child. Scope parents may come from unrelated roots when explicit C3 composition is needed: `Scope(name="combined", parents=(left, right))`. `ctx.scope.mro` is the visibility order, while `ctx.root` owns the application data store and lifetime subtree. `to_dict()` returns a nested ordinary-dict projection; `flatten()` returns the exact visible Ref-to-value leaf mapping. Neither copies stored values.

Normal Context operations check the caller's lifecycle state, not a state shared by Schema or Store. Disposal forbids new mutations throughout the owned subtree, but allows reads until each Context finishes releasing. Exact registration disposers and Scope release remain available during cleanup. Another active Context can still modify shared data. Application cleanup belongs in `ctx.effect()`; Lifecycle, not overridden Context methods, traverses child disposal. `Lifecycle` can also own effects independently of Context data.

## Effects and disposal

`ctx.effect(setup)` owns one setup and its cleanup. A synchronous setup runs immediately and returns an early disposer. If setup returns an awaitable, `effect()` returns an awaitable resolving to that disposer; use `await await_result(ctx.effect(setup))` when either form is possible. Async setup is owned before it starts: owner disposal waits for it and then runs its cleanup, even if its caller never awaited registration. Await setup before using the resource it acquires. Setup remains responsible for undoing partial acquisition if it raises before returning cleanup.

A parent strongly owns its child Contexts. Each Context processes directly owned effects and child Contexts in last-in-first-out order, recursively. `dispose()` runs synchronous cleanup immediately and returns `None` when complete, or an awaitable for the unfinished asynchronous cleanup. Use `await await_result(ctx.dispose())` for either case, importing `await_result` from `slyme.utils.execution`. An async continuation is not scheduled until awaited; merely discarding it leaves disposal unfinished. Once scheduled, its task survives waiter cancellation. Early effect disposers follow the same completion protocol. Cleanup continues after failure, then raises an exception group containing only failures in cleanup execution order; repeated calls share the completion and reproduce its terminal failure without repeating cleanup. A disposed Context rejects further data and lifecycle operations.

Effect setup and cleanup cannot dispose their owner Context or an ancestor while running, and cleanup cannot re-enter its own disposer; Slyme rejects these operations with `RuntimeError`.

Use `ctx.effect(lambda: ctx.get(ref).add(target_scope, value))` when a Compose stored at `ref` should receive a lifecycle-owned contribution. `ctx.get(ref)` selects the Compose through the Context's bound Scope; `target_scope` explicitly selects where to store the contribution. The calling Context owns cleanup regardless of the target Scope. Direct `compose.add(scope, value)` remains available when the caller will manage its returned disposer itself. `compose.bind(scope_a, scope_b, identity=key)` gives those Scopes one shared bucket in that Compose only. Binding is permanent for each live Scope and has no disposer; contributions remain independently reversible.

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

Wrapper execution passes Context, the wrapped Node, and the next callable positionally, followed by keyword bindings. Attach it through `node.add_wrappers(...)`. This example and the `execute` function above assume synchronous children. For mixed children, use `async def` and `await await_result(...)` before inspecting results, executing following statements, or leaving `try/finally`; see [async.md](async.md).

## Assembly and execution

```python
root = execute(
    derived=Auto(
        calculate(
            value=Auto(R.resolve("input.value")),
            scale=2.0,
        )
    ),
    children=(increment(counter=R.resolve("state.counter")),),
    output=R.resolve("output.result"),
).add_wrappers(trace(name="execute"))

ctx = Context()
ctx.declare(R)
ctx.update({R.resolve("input.value"): 3.0, R.resolve("state.counter"): 0})
result = root(ctx)
assert ctx.get(R.resolve("output.result")) == result
```

The Node graph stays mutable. Read explicit bindings with `root.get(name)`, replace them with `root.set(name, value)`, and remove them with `root.delete(name)`. Absent bindings use the function's native defaults at invocation. `root(ctx, **kwargs)` overrides saved bindings for one call before Auto evaluation. Static parameter containers are passed directly to Node and Wrapper functions, so in-call mutations remain on the live element. Call the relevant factory or assembly function again when another independently configurable graph is needed, and copy mutable application values explicitly when they must not be shared.

Use named child parameters for stable roles and Python sequences or mappings for extensible physical composition. Use `sequential(...)` for a plain declarative pipeline and a custom higher-order Node when execution semantics differ.
