# Changelog

All notable changes to Slyme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Slyme is currently in the 0.x development stage, so minor releases may include
breaking changes when they are documented here.

## [Unreleased]

### Added

- Added a pytest-based unit and integration suite with asynchronous tests,
  property-based checks, branch coverage enforcement, and Python 3.9–3.14 CI.
- Added locked development dependency groups, Ruff and mypy quality gates,
  pre-commit/pre-push hooks, CodeQL, dependency review, and Dependabot updates.
- Added verified package builds, trusted PyPI publishing with provenance
  attestations, contribution guidance, issue templates, and a security policy.

### Fixed

- Fixed static workflow discovery reporting partial attribute chains such as
  `input` in addition to the actual `R.input.value` reference.

### Removed

- Removed the Def/Exec split and recursive `Node.prepare()` compilation model;
  Node and Wrapper objects are now directly callable.
- Removed the legacy asynchronous decorator aliases; use `@node` and
  `@wrapper`, which detect `async def` directly.
- Removed positional Scope injection from Node factories.
- Removed Context hooks and the asynchronous mirrors of locally synchronous
  Context operations.
- Removed `Ref.key_path` and its `CallKey`, `KeyPathExpr`, and `P` helpers.

### Changed

- Node and Wrapper build parameters are now real instance attributes. Parameter
  names are checked against reserved framework attributes when decorated;
  `_kwargs` and mapping-style parameter access have been removed.
- Node and Wrapper construction now uses separate mode-aware factories while
  synchronous and asynchronous decorators share the same signature-analysis
  path.
- Each Node and Wrapper call now freezes only its local ordinary Python
  containers and builds its evaluation and wrapper plan from that snapshot.
  Node-like objects remain leaves, allowing future logical Slot graphs to
  contain cycles without recursive preparation.
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
