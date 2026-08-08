---
name: slyme-developer
description: API guidance, architectural best practices, and code-style conventions for projects built with Slyme. Use when designing, developing, reviewing, or debugging applications based on the Slyme framework.
---

# Develop with Slyme

## Mental model

Slyme has distinct build and execution phases. Building produces mutable `*Def` objects whose Node structure, wrappers, and parameters may be freely assembled or modified. Calling `.prepare()` recursively freezes the complete Node tree and its parameters—including parameter PyTree containers—and produces an immutable `*Exec` tree. Execute the workflow by calling the prepared root Node with a `Context`; the prepared tree can then be reused.

- `@node` defines an execution unit for state transitions, side effects, or higher-order control flow.
- `@expression` derives a value for other Nodes without updating state.
- `@wrapper` surrounds a Node with cross-cutting behavior such as tracing, retry, or error handling.
- `@builder` is a build-time factory that assembles and validates reusable Node trees; it does not perform runtime work.
- `Context` is the structurally immutable state passed through execution. Nodes produce the next Context rather than mutating shared state in place.

## Architecture

Decompose the execution flow from top to bottom into atomic operations and steps, then represent each with Slyme `@node`, `@expression`, or `@wrapper`. Model complex control flow with higher-order `@node`s.

A higher-order Node coordinates control flow through composition slots: parameters that accept child Nodes at defined positions in its execution topology. Use independent, named `Node` parameters when a slot has a fixed number of children with stable, distinct roles. When a slot represents a variable or extensible group of children, use a container that expresses its composition semantics, such as `Sequence[Node]` for ordered execution or `Mapping[K, Node]` for keyed dispatch. Adding or removing children within an extensible slot should change assembly code, not the higher-order Node's signature. Keep independently meaningful phases in separate named slots. See [references/core-api.md](references/core-api.md) for examples.

Reuse existing Nodes whenever possible. Extend behavior through composition before introducing new Nodes.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It provides the Slyme API reference and best-practice guidance to follow throughout development.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.
