---
name: slyme-developer
description: Build, extend, debug, or review downstream Python applications that use Slyme. Use for Slyme node trees, Context/Ref/Auto dependency injection, builders, wrappers, async flows, CLI metadata, or architecture decisions. Do not use for maintaining Slyme framework internals.
---

# Develop with Slyme

Treat Slyme as an installed dependency. Inspect the target project's Slyme version and existing nodes before coding.

## Mental model

Slyme separates definition from execution. Decorated factories produce mutable node definitions; `.prepare()` freezes the complete tree into a reusable execution object. Execution threads an immutable `Context` through the tree. `Ref` identifies state, `Auto` resolves `Ref` and expression dependencies before a function body runs, and builders own assembly.

Use `@node` for state transitions or effects, `@expression` for derived values, `@wrapper` for cross-cutting behavior, and `@builder` for composition. Define runtime parameters before `/`, build-time parameters after `*`, prepare once at the application boundary, and retain every returned `Context`.

## Architecture

Design nodes, expressions, and wrappers as small abstractions with one reason to change. Do not map each requirement phase directly to one node: cleaning versus validation, API work versus retry/concurrency, and aggregation versus persistence are separate contracts even when currently used once. If a function both coordinates work and performs domain work, split the coordinator from the injected operations. Compose policy in builders rather than growing flag-heavy nodes.

Reuse an existing primitive before adding another. Prefer parameterization and composition over near-duplicate nodes; add a new node only when it establishes a distinct, reusable contract.

For higher-order nodes, expose extension points as sequences or mappings of nodes instead of fixed parameters such as `input_node`, `process_node`, and `output_node`. Execute sequences with `sequential_exec` or `async_sequential_exec` so builders can inject zero, one, or many stages without changing the higher-order node.

Create application-specific Refs at the assembly boundary and pass them explicitly. Do not keep Ref instances or prepared trees in module globals. Keep expressions free of state writes, wrappers focused on cross-cutting policy, and each state transition owned by a clear node.

## API by example

Read [references/core-api.md](references/core-api.md) before writing Slyme code. It is the compact canonical example for decorators, signatures, `Context`, `Ref`, `R`, `Auto`, PyTrees, higher-order nodes, wrappers, builders, CLI metadata, and execution.

For async or mixed sync/async work, also read [references/async.md](references/async.md); it only describes differences from the core example.

Avoid deprecated positional Scope dictionaries and `Ref.key_path`; use explicit keyword arguments with `R`, `@expression`, and `Auto`.
