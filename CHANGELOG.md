# Changelog

All notable changes to Slyme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Slyme is currently in the 0.x development stage, so minor releases may include
breaking changes when they are documented here.

## [Unreleased]

### Added

- Added a pytest-based unit and integration suite with asynchronous tests,
  property-based checks, branch coverage enforcement, and Python 3.10–3.14 CI.
- Added locked development dependency groups, Ruff and mypy quality gates,
  pre-commit/pre-push hooks, CodeQL, dependency review, and Dependabot updates.
- Added verified package builds, trusted PyPI publishing with provenance
  attestations, contribution guidance, issue templates, and a security policy.
- Added mutable, monotonic `Schema` declaration trees with explicit `resolve()`
  lookup, branch metadata, leaf/container-aware recursive merging, and
  path-free `ref()` declarations.
- Added application-owned Ref declarations to `Context`; forks share later
  `declare()` additions while Context data continues to follow C3 lookup.
- Added live `Context.fork()` layers with C3 multiple inheritance, local-only
  read options, and reversible `Context.add()` bindings.
- Added `Context.flatten()` for exact visible Ref-to-value leaf mappings.
- Added `Compose` for ordered, reversible values resolved through a Context's
  C3 hierarchy, including first-value, collection, mapping, and custom rules.

### Fixed

- Rejected variadic `*args` and `**kwargs` in Node and Wrapper signatures so
  they cannot bypass fixed runtime-arity and named build-parameter validation.
- Prevented Context updates from implicitly changing existing leaf/container
  roles; an exact-path `delete` or `drop` now makes structural replacement
  explicit.
- Context mutations now validate the complete transaction before applying it
  directly to existing local branches, avoiding full-tree replacement while
  retaining no-partial-write behavior.

### Removed

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
  `@wrapper`, which detect `async def` directly.
- Removed positional Scope injection from Node factories.
- Removed Context hooks and the asynchronous mirrors of locally synchronous
  Context operations.
- Removed the public Context data-container type and Context PyTree
  registration; Context hierarchies are identity-bearing C3 graphs.
- Removed `Context.clear()` and `collect_leaves()`; empty structural containers
  are not retained, and `flatten()` returns Ref-keyed leaf mappings.
- Removed `Context.diff()`, `ContextDiff`, and `DIFF_MISSING`.
- Removed whole-graph Node structure validation. Node and Wrapper parameters
  may contain arbitrary nested values; mounted wrappers still match Node mode.
- Removed `Ref.key_path` and its `CallKey`, `KeyPathExpr`, and `P` helpers.
- Removed the global open-path `R` and reference deletion; applications now
  declare Context paths through `Schema`.
- Removed Schema attribute paths, `Schema.from_refs()`, `RefLike`, and the
  `...` declaration shorthand.

### Changed

- Raised the minimum supported Python version from 3.9 to 3.10, following the
  upstream CPython maintenance lifecycle, and adopted native 3.10 typing syntax.
- Renamed `RefFactory` to `Schema` and aligned Context construction and
  inspection on `Context(..., schema=R)` and `ctx.schema`.
- `Ref` is now an immutable path value, `ref()` creates path-free Schema
  declarations, and `Schema.resolve()` returns the Ref declared for a path.
- Node and Wrapper build parameters now use explicit `get()`, `set()`, and
  `reset()` methods. Parameter names may overlap framework API names without
  changing attribute behavior.
- Node and Wrapper construction now uses separate mode-aware factories while
  synchronous and asynchronous decorators share the same signature-analysis
  path.
- Node and Wrapper calls now pass their current static parameter containers
  directly to user functions instead of creating an implicit frozen snapshot.
- Context construction now accepts a Ref-to-value mapping and keyword-only
  `schema` or direct parents. All parents share one application root, and every
  Context access rejects undeclared paths. Context mutations prune empty
  structural containers; use `to_dict()` for a nested projection and
  `flatten()` for the exact leaf mapping.
- `Context.mro` is now an immutable property, `Context.root` exposes its final
  application ancestor, and descendant `schema` properties resolve the Schema
  stored by that root.
- Auto evaluation now gives every child Node an independent Context fork while
  Ref evaluation reads the supplied Context directly.
- Builder functions now require their outer result to be a `Node` or
  `AsyncNode` without recursively validating the returned parameter graph.
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
