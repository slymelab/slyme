# Changelog

All notable changes to Slyme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Slyme is currently in the 0.x development stage, so minor releases may include
breaking changes when they are documented here.

## [Unreleased]

### Added

- Added `Node.acall()` and `Context.adispose()` as always-awaitable adapters
  that preserve immediate synchronous execution and the unified completion rules.
- Added `slyme.utils.awaitable.resolve()` to await an immediate or asynchronous
  result without starting a loop or offloading synchronous work.
- Unified Node, Auto, and Wrapper execution around actual returned values;
  async dependencies and child cleanup can promote a synchronous parent call.
- Unified Context setup and cleanup through `effect()` and `dispose()`, including
  owned asynchronous setup and cancellation-safe repeatable cleanup completion.

- Added a pytest-based unit and integration suite with asynchronous tests,
  property-based checks, branch coverage enforcement, and Python 3.10–3.14 CI.
- Added locked development dependency groups, Ruff and mypy quality gates,
  pre-commit/pre-push hooks, CodeQL, dependency review, and Dependabot updates.
- Added verified package builds, trusted PyPI publishing with provenance
  attestations, contribution guidance, issue templates, and a security policy.
- Added mutable `Schema` declaration trees with explicit `resolve()` lookup,
  leaf/container-aware recursive merging, independent declaration ownership,
  and exact reversible teardown.
- Added immutable `Scope` identities with names, C3 multiple inheritance across
  otherwise unrelated visibility roots, and unique visible-name lookup.
- Added single-parent Context lifetime trees with synchronous and asynchronous
  effects, recursive owner-local LIFO disposal, and explicit Scope binding.
- Added lifecycle-owned `Context.add()` and `declare()`
  operations, each with an exact disposer for optional early cleanup.
- Added `Context.flatten()` for exact visible Ref-to-value leaf mappings.
- Added `Compose` for ordered, reversible values resolved through a Scope's C3
  hierarchy, including first-value, collection, mapping, and custom rules.
- Added immutable Compose-local identity bindings so selected Scopes can share
  one contribution or Context-leaf bucket without changing other Scope lookup.
- Added Schema-owned `replaceable` policies and `Context.isolate()` for owned
  child Contexts that block selected inherited Scope values.

### Fixed

- Moved exception context enrichment into failure handlers so successful Node
  and Wrapper calls do not allocate exception context managers or messages.
- Indexed Schema entries by complete path for direct Context lookups while
  preserving tree-based traversal and exact declaration teardown.
- Made ownership removal constant-time on average and synchronous disposal
  a single subtree traversal, preserving recursive LIFO cleanup.
- Preserved Context disposal failures after a cancelled waiter so every later
  disposal call observes the same terminal result without repeating cleanup.
- Rejected disposal of an effect's owner or ancestors during synchronous setup
  and cleanup, preventing reentrant teardown from invalidating live cleanup.
- Released Context-owned `add()` values when their final Schema declaration is
  removed, without weakening public Compose ownership.
- Preserved failures from cancelled Auto siblings and their cleanup during
  evaluation failure and repeated cancellation.
- Rejected variadic `*args` and `**kwargs` in Node and Wrapper signatures so
  they cannot bypass fixed runtime-arity and named build-parameter validation.
- Prevented Context updates from changing Schema-owned leaf/container roles.
- Context mutations now validate the complete transaction before applying it
  to flat per-entry bindings, retaining no-partial-write behavior.

### Removed

- Removed `contains_eval_type()`, `EvaluationPlan`, `prepare_eval_plan()`, and
  `execute_eval_plan()`. `eval_tree()` handles traversal, batched evaluation,
  and reconstruction directly without an intermediate evaluation plan.
- Removed execution-mode decorators and separate async Node, Wrapper, evaluator,
  sequence, and Context lifecycle APIs. Use the unified APIs and `resolve()`
  when immediate and awaitable results are both possible.
- Removed `Context.bind()` and `Context.contribute()`. Use
  `Context.isolate(..., identity=...)` for shared isolated leaf storage and
  `ctx.effect(lambda: compose.add(scope, value))` for owned contributions.

- Removed `slyme.builder`; use ordinary Python functions to assemble Node graphs.
- Removed `slyme.cli`, its argparse helpers, Context argument metadata, and
  general-purpose Ref metadata so the core model no longer embeds input-adapter
  configuration.
- Removed `Node.run()`, `AsyncNode.run()`, and their convenience runner module;
  applications now construct Context, handle external inputs and outputs, and
  invoke Nodes explicitly.
- Removed the experimental runner, command package, command-line entry point,
  and their runner-only output metadata.
- Removed the Def/Exec split and recursive `Node.prepare()` compilation model;
  Node and Wrapper objects are now directly callable.
- Removed the legacy asynchronous decorator aliases; use `@node` and
  `@wrapper`, which compose immediate and awaitable results.
- Removed positional Scope injection from Node factories.
- Removed Context hooks and the asynchronous mirrors of locally synchronous
  Context operations.
- Removed the public Context data-container type and Context PyTree
  registration; runtime Context objects remain opaque PyTree leaves.
- Removed `Context.clear()` and `collect_leaves()`; empty structural containers
  are not retained, and `flatten()` returns Ref-keyed leaf mappings.
- Removed `Context.diff()`, `ContextDiff`, and `DIFF_MISSING`.
- Removed whole-graph Node structure validation. Node and Wrapper parameters
  may contain arbitrary nested values.
- Removed `Ref.key_path` and its `CallKey`, `KeyPathExpr`, and `P` helpers.
- Removed the global open-path `R` and reference deletion; applications now
  declare Context paths through `Schema`.
- Removed Schema attribute paths, `Schema.from_refs()`, `RefLike`, and the
  `...` declaration shorthand.

### Changed

- Scope viewers, Context-binding identities, and Schema declarations use direct
  ownership sets managed by internal registration and cleanup methods. Compose
  entries use unique tokens and ordered buckets, without per-entry internal
  release callbacks. Cleanup preserves failure replay and reentrant identity
  reuse; cleared values do not return when a Scope or identity is reused.
- Raised the minimum supported Python version from 3.9 to 3.10, following the
  upstream CPython maintenance lifecycle, and adopted native 3.10 typing syntax.
- Renamed `RefFactory` to `Schema` and aligned Context construction and
  inspection on `Context(..., schema=R)` and `ctx.schema`.
- `Ref` is now an immutable path-only value. `Schema.leaf()` and
  `Schema.container()` configure declared paths, while `Schema.resolve()`
  returns their Refs.
- Node and Wrapper build parameters now use explicit `get()`, `set()`, and
  `reset()` methods. Parameter names may overlap framework API names without
  changing attribute behavior.
- Node and Wrapper factories share signature analysis and construct the same
  graph element types for immediate and asynchronous functions.
- Node and Wrapper calls now pass their current static parameter containers
  directly to user functions instead of creating an implicit frozen snapshot.
- Auto parameters always reconstruct PyTree containers, including ordinary-only
  subtrees, while preserving ordinary leaf identities. Each Wrapper `call_next`
  invocation evaluates the current Auto containers without a content pre-scan.
- Context construction now accepts a Ref-to-value mapping and keyword-only
  `schema`, `parent`, or `scope`. Every Context access rejects undeclared paths.
  Schema is the only source of container structure; an application root stores
  flat entry-indexed bindings by Scope, uses `to_dict()` for a nested projection,
  and uses `flatten()` for the exact leaf mapping.
- `Context.add()` now provides only exact reversible installation. Schema's
  stable `replaceable` policy determines whether `set()` may replace that
  Scope's current value.
- `Context` now has one lifetime parent and one bound Scope. `fork()` creates an
  owned child and shares the Scope by default; a forked Scope provides an
  explicit local data layer. Context CRUD always uses the bound Scope.
- `Scope.fork()` now creates only a single-parent child; C3 multiple inheritance
  uses explicit `Scope(parents=(...))` construction. Context roots track the
  exact live Context viewers for every Scope instead of anonymous counts.
- `Context.root` owns the application data store and lifetime subtree and holds
  their shared Schema reference. Scope C3 order is independent of the Context
  lifetime tree and has no common-root restriction.
- Auto evaluation now gives every child Node an owned Context with a distinct
  child Scope inheriting the caller's Scope, and disposes it before parent
  execution continues. Asynchronous Auto evaluation awaits cleanup, including
  after cancellation.
- Context trees, mutable Schema declarations, and Compose values are now
  single-thread-owned. Asynchronous evaluation invokes synchronous child Nodes
  inline instead of moving live Context state into worker threads; applications
  explicitly offload ordinary value computation when needed.
- Node and Wrapper construction now binds every declared parameter through one
  `Spec` build path. Missing required parameters remain `UNDEFINED` until the
  call boundary instead of being rejected or processed by a second kwargs path.

## [0.1.1] - 2026-08-26

### Added

- Added the immutable `RefFactory` and global `R` shorthand for constructing
  references with attribute syntax.
- Added `Arg` metadata and the `slyme.cli` argparse integration for declaring,
  parsing, and validating external inputs.
- Added `Node.run()` as the high-level application boundary for preparing a
  Context, validating inputs, executing a Node, and extracting outputs.
- Added the `slyme-developer` Agent Skill with application-development guidance.
- Added an experimental runner, command package, and command-line entry point.

### Changed

- Unified synchronous and asynchronous function decoration under `@node`,
  `@expression`, and `@wrapper`.
- Added automatic `async def` detection and explicit `mode="sync"` /
  `mode="async"` overrides. Return annotations are not used for mode detection.
- Normalized `RefFactory` values transparently across Context, Node, and CLI
  APIs.
- Updated the documentation and examples to use `R`, `Arg`, and `Node.run()` as
  the recommended application-facing APIs.

### Fixed

- Fixed `RefFactory` normalization in `Context.extract()` and related Context
  operations.
- Fixed documentation examples and mobile code-block overflow.

[Unreleased]: https://github.com/slymelab/slyme/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/slymelab/slyme/compare/v0.1.0...v0.1.1
