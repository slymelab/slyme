# Changelog

All notable changes to Slyme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Slyme is currently in the 0.x development stage, so minor releases may include
breaking changes when they are documented here.

## [Unreleased]

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

### Deprecated

The following APIs are deprecated in 0.1.1 and scheduled for removal in 0.2.0:

- `@async_node`, `@async_expression`, and `@async_wrapper`; use the unified
  decorators instead.
- Positional Scope dictionary injection; pass keyword arguments directly.
- Context and ContextView `async_*` methods; Context storage is local and
  synchronous.
- Context hooks, `Hook`, and `HookChain`; use Node wrappers for execution
  interception.
- `Ref.key_path`, `CallKey`, `KeyPathExpr`, and `P`; use `@expression` and
  `Auto` for dynamic value resolution.

### Fixed

- Fixed `RefFactory` normalization in `Context.extract()` and related Context
  operations.
- Fixed documentation examples and mobile code-block overflow.

[Unreleased]: https://github.com/slymelab/slyme/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/slymelab/slyme/compare/v0.1.0...v0.1.1
