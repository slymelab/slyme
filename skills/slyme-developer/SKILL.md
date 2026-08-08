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

Decompose the execution flow from top to bottom into atomic operations and steps, then represent each with Slyme `@node`, `@expression`, or `@wrapper`. Model complex control flow with higher-order `@node`s; follow the concrete composition guidance in the references.

Reuse existing Nodes whenever possible. Extend behavior through composition before introducing new Nodes.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It is the compact canonical example for decorators, signatures, `Context`, `Ref`, `R`, `Auto`, PyTrees, higher-order nodes, wrappers, builders, CLI metadata, and execution.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.

Avoid deprecated positional Scope dictionaries and `Ref.key_path`; use explicit keyword arguments with `R`, `@expression`, and `Auto`.
