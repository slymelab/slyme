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

Design nodes, expressions, and wrappers as small abstractions with one reason to change. Do not map each requirement phase directly to one node: cleaning versus validation, API work versus retry/concurrency, and aggregation versus persistence are separate contracts even when currently used once. If a function both coordinates work and performs domain work, split the coordinator from the injected operations. Compose policy in builders rather than growing flag-heavy nodes.

Reuse an existing primitive before adding another. Prefer parameterization and composition over near-duplicate nodes; add a new node only when it establishes a distinct, reusable contract.

For higher-order nodes, expose extension points as sequences or mappings of nodes instead of fixed parameters such as `input_node`, `process_node`, and `output_node`. Execute sequences with `sequential_exec` or `async_sequential_exec` so builders can inject zero, one, or many stages without changing the higher-order node.

Create application-specific Refs at the assembly boundary and pass them explicitly. Do not keep Ref instances or prepared trees in module globals. Keep expressions free of state writes, wrappers focused on cross-cutting policy, and each state transition owned by a clear node.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It is the compact canonical example for decorators, signatures, `Context`, `Ref`, `R`, `Auto`, PyTrees, higher-order nodes, wrappers, builders, CLI metadata, and execution.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.

Avoid deprecated positional Scope dictionaries and `Ref.key_path`; use explicit keyword arguments with `R`, `@expression`, and `Auto`.
