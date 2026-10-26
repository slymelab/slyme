---
name: slyme-developer
description: API guidance, architectural best practices, and code-style conventions for projects built with Slyme. Use when designing, developing, reviewing, or debugging applications based on the Slyme framework.
---

# Develop with Slyme

## Mental model

Use `node`, `wrapper`, and `auto` from `slyme.node`, and `ref` from `slyme.context` as the four common function entry points. `Node`, `Wrapper`, and `Ref` remain available for type annotations and explicit construction; the Auto marker class is internal. Node/Wrapper configuration is read-only; use `partial` or `replace` to derive a new object.

Slyme uses directly callable Node and Wrapper objects. Their parameter mappings are read-only; `partial(**params)` creates a new object with shallowly merged bindings. Node and Wrapper calls pass their current static parameter containers directly to user functions; mutations to those containers remain on the live element and are visible to later calls. Use `partial(...)` or call an assembly function again when another independently configurable graph is needed. An application Context root holds an explicit `Schema` reference and owns its data store and lifetime subtree. Contexts form a single-parent lifetime tree, while immutable `Scope` objects independently provide C3 visibility. `Context.fork()` creates an owned child that shares its parent's Scope by default; use `ctx.fork(scope=ctx.scope.fork())` for a local data identity. `Scope.fork()` is single-parent; construct `Scope(parents=(...))` explicitly for C3 multiple inheritance. `Context.flatten()` exposes the visible Ref-to-value mapping without copying stored values.

`@node` and `@wrapper` directly return Node and Wrapper objects, with optional `params` and `metadata` mappings. Read bindings with `get(name, default)` or the read-only `params` mapping; use `partial(**params)` to derive new bindings. There are no parameter set/delete methods. Call-time kwargs override bindings for one invocation before Auto evaluation. Function defaults and argument errors follow Python. Auto marks explicit binding trees for evaluation; ordinary values pass through unchanged. `replace(params=..., wrappers=..., metadata=...)` returns a new Node with entire supplied fields replaced; omit a field to retain it, or pass None to clear it. Wrapper.replace accepts params and metadata. Public configuration is read-only; referenced values are not deep-copied or frozen.

Application code creates a `Context`, handles external inputs, calls the root Node with that Context, and reads outputs explicitly. Wrappers modify a mounted Node's execution and are not independently runnable workflow boundaries.

- `@node` defines an execution unit and may return either a derived value or control information.
- `@wrapper` surrounds a Node with cross-cutting behavior such as tracing, retry, or error handling.
- Ordinary Python functions assemble reusable Node graphs. Use return annotations for their intended result type; no assembly decorator is needed.
- `Context` has at most one lifetime parent and one bound Scope. Reads follow `ctx.scope.mro` by default, writes target `ctx.scope`, and `local=True` restricts reads to that exact Scope. Context CRUD never accepts a separate Scope.
- A Context tree and its mutable Schema and Compose objects belong to one thread and one event loop. This usage rule has no runtime thread-identity check. Offloaded thread or process work receives ordinary values and returns results for owner-thread Context mutation.
- Context construction accepts only keyword `parent` and `scope`. Create it, call `ctx.declare(schema_or_dict)`, then `ctx.update(values)`. Importing a Schema copies its current definitions with independent ownership; source changes do not propagate. Schema fixes each path's leaf or container role; runtime data stores only leaf bindings.
- `Context.effect()` owns immediate or awaitable setup and cleanup. Use `await_result` from `slyme.execution` to await either form. Setup and cleanup cannot dispose their owner or an ancestor. `dispose()` runs synchronous cleanup immediately and returns an awaitable if unfinished; await it to finish recursive LIFO cleanup. Repeated disposal shares its completion and reproduces terminal failures.
- `Schema.leaf()` defaults to `mode="assign"`, permitting `set()`/`delete()`. With `mode="register"`, use `Context.register()` and its exact disposer instead; the caller owns that installation. A mode rejects the other mode's writes even when no value is present. `Context.declare()` likewise binds declaration cleanup to the calling Context.
- `ctx.derive(label=..., parents=..., bindings=...)` creates an owned child Context and one new Scope configured for declared leaf paths and Compose objects. Parents defaults to `ctx.scope`, accepts one Scope or a tuple, and does not change the child's lifecycle parent. Binding values accept `ScopeBinding` or `Identity`, not `None`. A direct Identity is shorthand for `ScopeBinding(identity=identity)`. `ScopeBinding()` creates private storage; `ScopeBinding(identity=shared)` shares storage. Both ScopeBinding and Identity default to unblocked; enable either barrier explicitly with `blocked=True`. Scope construction and derive APIs are keyword-only; stored `scope.parents` is always a tuple.
- `Compose(factory=..., query=...)` manages reversible registrations in application-defined layers. Each layer implements synchronous `register(token, /, *args, **kwargs)` returning a synchronous disposer; Compose injects only the token and invokes the disposer at most once. `layers(scope)` follows C3 visibility; `resolve(scope)` applies the query. Use `ctx.effect(lambda: compose.register(scope, value))` for owned cleanup, or manage its disposer explicitly. `compose.derive()` and `Compose.derive_many()` configure new Scopes without lifecycle ownership. See [references/core-api.md](references/core-api.md) for layer examples and default Tree/Eval rules.

## Architecture

Decompose the execution flow from top to bottom into atomic operations and steps, then represent each with Slyme `@node` or `@wrapper`. Model execution patterns that coordinate child Nodes with higher-order `@node`s.

A higher-order Node accepts child Nodes through named parameters containing either one child or a structured collection. Each parameter represents a distinct role or extensible region in the execution topology, while the higher-order Node defines how its children participate in execution. Use a custom higher-order Node when it should provide execution semantics beyond simple linear chaining; use `sequential(...)` for a plain linear pipeline.

Represent a single, stable child role with an individual named `Node` parameter. For a statically assembled extensible group, use a container such as `Sequence[Node]` for ordered execution or `Mapping[K, Node]` for keyed dispatch. Use `Compose` when independent owners must add and remove values by Scope at runtime. Keep independently meaningful roles or phases in separate named parameters. See [references/core-api.md](references/core-api.md) for examples.

`Auto` Ref parameters read the supplied Context. Every Auto child Node receives an owned child Context with a distinct child Scope, and Slyme disposes that child before parent execution continues. Use a returned value for self-contained dataflow; it must not depend on a resource owned by the disposed child. Call children explicitly with a selected Context when their writes and effects must share its lifetime. Context does not own arbitrary tasks that use it, so stop and await them before disposal.

Reuse existing Nodes whenever possible. Extend behavior through composition before introducing new Nodes.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It provides the Slyme API reference and best-practice guidance to follow throughout development.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.
