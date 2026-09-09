---
name: slyme-developer
description: API guidance, architectural best practices, and code-style conventions for projects built with Slyme. Use when designing, developing, reviewing, or debugging applications based on the Slyme framework.
---

# Develop with Slyme

## Mental model

Slyme uses one mutable `Node` graph throughout assembly and execution. Node and Wrapper calls pass their current static parameter containers directly to user functions; mutations to those containers remain on the live element and are visible to later calls. Call the corresponding factory or Builder again when another independently configurable graph is needed. An application Context root holds an explicit `Schema` reference and owns its data store and lifetime subtree. Contexts form a single-parent lifetime tree, while immutable `Scope` objects independently provide C3 visibility. `Context.fork()` creates an owned child that shares its parent's Scope by default; use `ctx.fork(scope=ctx.scope.fork())` for a local data identity. `Scope.fork()` is single-parent; construct `Scope(parents=(...))` explicitly for C3 multiple inheritance. `Context.flatten()` exposes the visible Ref-to-value mapping without copying stored values.

Build parameters use the explicit Node and Wrapper parameter API. Read with `node.get(name)`, replace with `node.set(name, value)`, and restore the declared default with `node.reset(name)`. The read-only `node.params` mapping exposes all current parameters. Parameter names may overlap framework API names because parameters are not projected as attributes.

Application code creates a `Context`, handles external inputs, calls the root Node with that Context, and reads outputs explicitly. Wrappers modify a mounted Node's execution and are not independently runnable workflow boundaries.

- `@node` defines an execution unit and may return either a derived value or control information.
- `@wrapper` surrounds a Node with cross-cutting behavior such as tracing, retry, or error handling.
- `@builder` is a build-time factory that assembles reusable Node trees. It requires the outer result to be a `Node` or `AsyncNode` (`None` is reported as a missing return), but does not validate the nested object graph or perform runtime work.
- `Context` has at most one lifetime parent and one bound Scope. Reads follow `ctx.scope.mro` by default, writes target `ctx.scope`, and `local=True` restricts reads to that exact Scope. Context CRUD never accepts a separate Scope.
- A Context tree and its mutable Schema and Compose objects belong to one thread and one event loop. This usage rule has no runtime thread-identity check. Offloaded thread or process work receives ordinary values and returns results for owner-thread Context mutation.
- Context construction accepts a declared path-to-value mapping. Schema fixes each path's leaf or container role; runtime data stores only leaf bindings.
- `Context.effect()` and `async_effect()` run synchronous setup and own its cleanup. Setup and cleanup cannot dispose their owner or an ancestor while running. Each Context processes directly owned effects and child Contexts in LIFO order, recursively; cleanup runs once and terminal failures are reproduced by later disposal calls. Use `dispose()` for a wholly synchronous subtree and `await async_dispose()` when asynchronous cleanup may exist.
- `Context.add()` installs one binding at the bound Scope and owns its exact disposer. `Context.declare()` and `contribute()` likewise bind registration cleanup to the calling Context. `Schema.leaf(replaceable=False)` prevents later replacement through `set()` at the same Scope.
- `Context.isolate()` creates an owned child with a child Scope that blocks selected inherited leaf values.
- `Compose` stores ordered values under Compose-local identities and resolves those visible through Scope C3 lookup. Use `compose.bind(*scopes, identity=key)` for one-time sharing that affects only that Compose, and `ctx.bind(ref, identity=key)` to share one Context leaf across selected Context Scopes. Prefer `ctx.contribute()` for a lifecycle-owned registration; use `compose.add(scope, ...)` as the lower-level primitive.

## Architecture

Decompose the execution flow from top to bottom into atomic operations and steps, then represent each with Slyme `@node` or `@wrapper`. Model execution patterns that coordinate child Nodes with higher-order `@node`s.

A higher-order Node accepts child Nodes through named parameters containing either one child or a structured collection. Each parameter represents a distinct role or extensible region in the execution topology, while the higher-order Node defines how its children participate in execution. Use a custom higher-order Node when it should provide execution semantics beyond simple linear chaining; use `sequential(...)` for a plain linear pipeline.

Represent a single, stable child role with an individual named `Node` parameter. For a statically assembled extensible group, use a container such as `Sequence[Node]` for ordered execution or `Mapping[K, Node]` for keyed dispatch. Use `Compose` when independent owners must add and remove values by Scope at runtime. Keep independently meaningful roles or phases in separate named parameters. See [references/core-api.md](references/core-api.md) for examples.

`Auto` Ref parameters read the supplied Context. Every Auto child Node receives an owned child Context with a distinct child Scope, and Slyme disposes that child before parent execution continues. Use a returned value for self-contained dataflow; it must not depend on a resource owned by the disposed child. Call children explicitly with a selected Context when their writes and effects must share its lifetime. Context does not own arbitrary tasks that use it, so stop and await them before disposal.

Reuse existing Nodes whenever possible. Extend behavior through composition before introducing new Nodes.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It provides the Slyme API reference and best-practice guidance to follow throughout development.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.
