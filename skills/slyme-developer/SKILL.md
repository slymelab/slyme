---
name: slyme-developer
description: API guidance, architectural best practices, and code-style conventions for projects built with Slyme. Use when designing, developing, reviewing, or debugging applications based on the Slyme framework.
---

# Develop with Slyme

## Mental model

Slyme has distinct build and execution phases. Building produces mutable `*Def` objects whose Node structure, wrappers, and parameters may be freely assembled or modified. Calling `.prepare()` recursively freezes the complete Node tree and its parameters—including parameter PyTree containers—and produces an immutable `*Exec` tree.

Use the root Node's `.run(...)` method as the application boundary. It prepares a Def automatically, creates or extends a `Context`, resolves and validates external inputs declared by `Arg` metadata, executes the Node, and extracts an optional output Ref PyTree into ordinary Python values. Every Context value that must exist before the Node tree starts should be declared as an `Arg`; pass concrete values through `inputs`, or enable argparse when values should come from the command line. Omit `outputs` to receive the final `Context`, or use `return_context=True` with an output schema when both the extracted value and final Context are needed. Prepare and call an Exec directly only for lower-level execution where Context orchestration is intentionally managed by the caller.

Expose this application-boundary contract only on synchronous and asynchronous `@node`s. Expressions derive values inside a Node tree and wrappers modify a mounted Node's execution; neither is an independently runnable workflow boundary.

- `@node` defines an execution unit for state transitions, side effects, or higher-order execution composition.
- `@expression` derives a value for other Nodes without updating state.
- `@wrapper` surrounds a Node with cross-cutting behavior such as tracing, retry, or error handling.
- `@builder` is a build-time factory that assembles and validates reusable Node trees; it does not perform runtime work.
- `Context` is the structurally immutable state passed through execution. Nodes produce the next Context rather than mutating shared state in place.

## Architecture

Decompose the execution flow from top to bottom into atomic operations and steps, then represent each with Slyme `@node`, `@expression`, or `@wrapper`. Model execution patterns that coordinate child Nodes with higher-order `@node`s.

A higher-order Node accepts child Nodes through composition slots: named parameters that receive either an individual child Node or a structured collection of child Nodes. Each slot represents a distinct role or extensible region in the execution topology, while the higher-order Node defines how its children participate in execution. Use a custom higher-order Node when it should provide execution semantics beyond simple linear chaining; use `sequential(...)` for a plain linear pipeline.

Represent a single, stable child role with an individual named `Node` parameter. When a composition slot represents a variable or extensible group of children, use a container that expresses the group's composition semantics, such as `Sequence[Node]` for ordered execution or `Mapping[K, Node]` for keyed dispatch. Adding or removing children within an extensible slot should change assembly code, not the higher-order Node's signature. Keep independently meaningful roles or phases in separate named slots. See [references/core-api.md](references/core-api.md) for examples.

Reuse existing Nodes whenever possible. Extend behavior through composition before introducing new Nodes.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It provides the Slyme API reference and best-practice guidance to follow throughout development.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.
