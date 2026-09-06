---
name: slyme-developer
description: API guidance, architectural best practices, and code-style conventions for projects built with Slyme. Use when designing, developing, reviewing, or debugging applications based on the Slyme framework.
---

# Develop with Slyme

## Mental model

Slyme uses one mutable `Node` graph throughout assembly and execution. Node and Wrapper calls pass their current static parameter containers directly to user functions; mutations to those containers remain on the live element and are visible to later calls. Call the corresponding factory or Builder again when another independently configurable graph is needed. An application Context owns an explicit `Schema`; `Context.fork()` shares those declarations while creating a live local layer with C3 parent lookup. `Context.flatten()` exposes the visible Ref-to-value mapping without copying stored values.

Build parameters use the explicit Node and Wrapper parameter API. Read with `node.get(name)`, replace with `node.set(name, value)`, and restore the declared default with `node.reset(name)`. The read-only `node.params` mapping exposes all current parameters. Parameter names may overlap framework API names because parameters are not projected as attributes.

Application code creates a `Context`, handles external inputs, calls the root Node with that Context, and reads outputs explicitly. `Arg` metadata and `slyme.cli` can prepare command-line inputs without adding a second Node execution interface. Wrappers modify a mounted Node's execution and are not independently runnable workflow boundaries.

- `@node` defines an execution unit and may return either a derived value or control information.
- `@wrapper` surrounds a Node with cross-cutting behavior such as tracing, retry, or error handling.
- `@builder` is a build-time factory that assembles reusable Node trees. It requires the outer result to be a `Node` or `AsyncNode` (`None` is reported as a missing return), but does not validate the nested object graph or perform runtime work.
- `Context` has local mutable data and immutable ordered parents. Reads are effective by default, writes are local, and `local=True` restricts read operations to one Context.
- Context construction accepts a declared path-to-value mapping. Structural containers are derived from leaf paths and disappear when their last leaf is deleted.
- `Context.add()` installs one non-replaceable local binding and returns its exact idempotent disposer.
- `Compose` stores ordered values by Context identity and resolves those visible through C3 lookup. Use `one()`, `collect()`, `merge()`, or a synchronous custom resolver.

## Architecture

Decompose the execution flow from top to bottom into atomic operations and steps, then represent each with Slyme `@node` or `@wrapper`. Model execution patterns that coordinate child Nodes with higher-order `@node`s.

A higher-order Node accepts child Nodes through named parameters containing either one child or a structured collection. Each parameter represents a distinct role or extensible region in the execution topology, while the higher-order Node defines how its children participate in execution. Use a custom higher-order Node when it should provide execution semantics beyond simple linear chaining; use `sequential(...)` for a plain linear pipeline.

Represent a single, stable child role with an individual named `Node` parameter. For a statically assembled extensible group, use a container such as `Sequence[Node]` for ordered execution or `Mapping[K, Node]` for keyed dispatch. Use `Compose` when independent owners must add and remove values by Context at runtime. Keep independently meaningful roles or phases in separate named parameters. See [references/core-api.md](references/core-api.md) for examples.

`Auto` Ref parameters read the supplied Context. Every Auto child Node receives its own Context fork, so use a returned value for dataflow. Call children explicitly with a selected Context when their local writes must be shared.

Reuse existing Nodes whenever possible. Extend behavior through composition before introducing new Nodes.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It provides the Slyme API reference and best-practice guidance to follow throughout development.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.
