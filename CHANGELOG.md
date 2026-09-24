# Changelog

All notable changes to Slyme will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Slyme is currently in the 0.x development stage, so minor releases may include
breaking changes when they are documented here.

## [Unreleased]

### Added

- `Context.install(name, func)` registers lifecycle-owned extension methods under
  `$.methods`. Dynamic attribute access binds the accessing Context, follows Scope
  visibility, and preserves synchronous or asynchronous results. Native members
  remain reserved; each installation withdraws its value before its declaration.
- Generic `Context[A]` facets, created by an optional synchronous `facet_factory`
  on Context construction, `fork()`, or `derive()`. Facets are per-Context,
  remain available after disposal, and are not inherited or automatically
  disposed. Factory return types determine facet types; bare Context defaults
  to Any. Derive bindings are installed before the factory runs.
- Immutable `Context.dispose_mode` selects sequential LIFO or batch cleanup.
  Context construction, `fork()`, and `derive()` default independently to
  `"sequential"`; batch groups join all cleanup and report failures in reverse
  registration order. Nested Contexts and shared early disposers compose cleanup
  dependencies without an additional Effect grouping API.
- Context-owned Tree rules and Auto evaluators, installed by `context/default.py`
  in independent Composes at `$.tree.data.rules`, `$.tree.node.rules`, and `$.eval.handlers`.
  Defaults follow normal Scope visibility with no root fallback and are released
  with their root Context. Public Ref constants expose these configuration paths.
- Stateless `TreeEngine` traversal with explicit immutable `TreeRules` and public
  `TreeHandler`. Each Context operation captures effective rules once; Schema
  declaration retains separate immutable dict-only rules.
- Per-call `TreeResolver(func, takes_aux=False)` replaces `is_leaf` and the
  pre/post resolver lists. It can force a leaf, select a handler, or defer to
  exact-type lookup. Traversal constructs paths only for path output or a
  resolver requiring `TraverseAux`; Context does not install resolver fields.

- Exposed `RefEntry`, `RefConfig`, `RefLeafConfig`, and `RefContainerConfig`,
  plus `Schema.resolve_entry()` and the `Schema.entries` tuple property for
  metadata introspection. Withdrawn entries reject config reads with `LookupError`.
- Added `slyme.utils.exception.exception_group(message, excs)` with native groups
  on Python 3.11+ and a simple Python 3.10 fallback preserving catch semantics.
- Added `slyme.utils.execution.once()` to share a callback's first result or
  failure, and `SharedAwaitable` for lazy, cancellation-isolated shared waits.
- Added explicit `Auto(tree)` parameter bindings and per-call keyword overrides
  for Node and Wrapper. `delete(name)` removes a saved binding.
- Added `Wrapper.compose()` to assemble outermost-first callable chains without
  execution, preserving live wrapper parameters and caller-controlled Contexts.
- Added `slyme.utils.execution.run(generator)` to drive ordinary generator
  control flow, returning synchronously until a yielded awaitable requires an
  asynchronous remainder. Awaited errors are thrown at the suspended yield.
- Added `slyme.utils.execution.await_result()` to await an immediate or asynchronous
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
- Added lifecycle-owned `Context.register()` and `declare()`
  operations, each with an exact disposer for optional early cleanup.
- Added `Context.flatten(ref=None, *, local=False)` for exact visible Ref-to-value
  leaf mappings of the root or a selected container.
- Added `Compose` for reversible registrations in application-defined layers,
  resolved through Scope C3 lookup and caller-supplied queries.
- Added immutable Compose-local identity bindings so selected Scopes can share
  contribution storage without changing other Scope lookup; Context leaves
  support the same identity-sharing rules through `derive(bindings=...)`.
- Added Schema-owned write modes and immutable `Identity` objects with optional
  identity-level barriers. `ScopeBinding` specifies identity and Scope-local
  barriers independently of payload lifetime. Both default to unblocked.
- Added keyword-only `Context.derive(label=..., parents=..., bindings=...)` to
  create an owned child Context and one configured Scope. `Compose.derive()`
  and static `Compose.derive_many()` create a Scope for one or multiple Composes
  without lifecycle ownership; both require explicit parents. Derive APIs accept
  ScopeBinding or Identity configurations, not None; Compose.derive requires its binding.

### Fixed

- Context construction owns children before Scope restoration and uses disposal
  for restoration or default-installation failures. Internal finalization shares
  its first result or failure through per-instance `once` wrappers.
- Lifecycle disposal follows Python's `finally` semantics: a finalization failure
  propagates with any cleanup failure retained as its implicit exception context,
  without rewriting exception causes.
- Lifecycle and effect disposal share execution through `once` and
  `SharedAwaitable`. Each effect waits for its own setup before cleanup;
  independent tasks can dispose an owner while setup or cleanup is pending.
  Setup and cleanup must not reenter or await disposal containing themselves;
  Lifecycle does not detect these unsupported calls or wait cycles.
  Background failures are not retrieved just to suppress asyncio's
  unobserved-exception diagnostics.
- Aligned Node and Wrapper deletion documentation and tests with idempotent
  removal of absent bindings. Context regression tests cover explicit Scope
  reuse after disposal, Schema withdrawal, and collection of detached values.
- Retained sparse binding history in weak-key Scope usage records. The final
  viewer releases identity data without discarding the index; reusing a Scope
  restores only its indexed bindings and prunes withdrawn entries, without
  scanning the application's Schema.
  `ContextStore.dispose()` clears the remaining bindings and detaches the Store
  from Schema after all viewers have been released.
- Reused a single parent Scope's C3 order directly when constructing a child,
  making single-parent construction linear in the ancestor count.
- Replaced exception-driven missing-identity scans with direct lookups. Context
  lookup stops at the first value or inheritance barrier; Compose traversal
  deduplicates shared identities.
- Made Context mutation checks constant-time by closing the ownership subtree
  before cleanup, preserving readable cleanup and recursive LIFO disposal.
- Separated Context binding storage from Compose: each identity has one record
  for its value, barrier, registration token, and observed Scopes. Scope usage
  records combine viewer ownership and a sparse Schema-entry index.
- Indexed Context bindings by Scope so viewer registration and release visit
  only related leaves, preserving Scope reuse without retaining removed values.
- Made Context `set()` and `delete()` direct single-path operations while
  retaining whole-batch preflight checks for `update()` and `drop()`.
- Reduced Context `extract()` to one input flatten and one result unflatten;
  Auto Ref evaluation reads the flat Ref batch directly through `Context.get()`.
- Moved exception context enrichment into failure handlers so successful Node
  and Wrapper calls do not allocate exception context managers or messages.
- Indexed Schema entries by complete path for direct Context lookups while
  preserving tree-based traversal and exact declaration teardown.
- Made ownership removal constant-time on average and synchronous ownership
  traversal linear-time, preserving recursive LIFO cleanup.
- Preserved Context disposal failures after a cancelled waiter so every later
  disposal call observes the same terminal result without repeating cleanup.
- Rejected disposal of an effect's owner or ancestors during synchronous setup
  and cleanup, preventing reentrant teardown from invalidating live cleanup.
- Released Context-owned `register()` values when their final Schema declaration is
  removed, without weakening public Compose ownership.
- Auto evaluates all sibling Nodes before reporting errors, without cancelling
  siblings on failure. Successful children dispose immediately; failure cleanup
  completes before reporting child and cleanup errors together.
- Prevented Context updates from changing Schema-owned leaf/container roles.
- Context batch writes and deletions validate inputs before applying changes
  to flat per-entry bindings. Preflight failures leave bindings unchanged;
  failures during application do not trigger rollback.

### Removed

- Removed `Compose.one()`, `collect()`, `merge()`, and generic entry snapshots.
  Define layer storage and queries explicitly; inspect live layers with `layers()`.
- Mutable global Tree/evaluator registries and `utils.registry`; scoped Compose
  contributions replace registration APIs. Root views include `$`, so bulk
  assignment snapshots must select assign-mode business fields explicitly.

- Removed `NodeTerminate` and its automatic source-node annotation.
- Removed the `Continuation` class and its chain, batch, and sequential APIs.
  Use `run(generator)` with native loops and exception handling; consumers own
  scheduling, result collection, and error aggregation.
- Removed `slyme.utils.awaitable`; import `await_result` from `slyme.utils.execution`.
- Removed `TypeRegistry` and `TreeEngine.allow_inheritance`. Tree handlers and
  Auto evaluators use exact type keys; subclasses
  require explicit registration. Explicit Tree resolvers remain supported.
- Removed `Context.mutate()`. Use separate `drop()` and `update()` calls for
  deletion followed by assignment; the two calls are not one transaction.
- Removed `contains_eval_type()`, `EvaluationPlan`, `prepare_eval_plan()`, and
  `execute_eval_plan()`. `eval_tree()` handles traversal, batched evaluation,
  and reconstruction directly without an intermediate evaluation plan.
- Removed execution-mode decorators and separate async Node, Wrapper, evaluator,
  sequence, and Context lifecycle APIs. Use the unified APIs and `await_result()`
  when immediate and awaitable results are both possible.
- Removed public `Context.bind()`, `Context.isolate()`, and in-place Compose binding.
  Configure new Scopes through `Context.derive(bindings=...)` or
  `Compose.derive()` / `derive_many()`, using immutable `Identity` and `ScopeBinding`
  values. `Context.fork()` only creates a lifetime with a supplied or shared Scope.
- Removed the `ContextElement` base class; Context and ContextView retain their
  data access methods without a shared abstract base.
- Removed `ContextView.extract()`. Views provide only `get/exists/keys/to_dict/flatten`;
  tree-shaped extraction uses `Context.extract()` with absolute paths or Refs.
- ContextView accepts only relative string paths; use Context directly for Ref
  lookups. Its `flatten()` output remains keyed by absolute Refs.
- Removed `Context.contribute()`. Use
  `ctx.effect(lambda: compose.register(scope, value))` for owned contributions.

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
- Removed the public Context data-container type and Context Tree
  registration; runtime Context objects remain opaque Tree leaves.
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

- Postpone annotations consistently across source and tests, avoiding repeated
  type-expression evaluation when creating `once` and execution callbacks.
  Raw runtime annotations are strings; the minimum Python version remains 3.10.
- Root Contexts require sequential disposal to keep framework defaults available
  throughout cleanup. Batch disposal remains available on child Contexts.
- `Context.fork()` and `Context.derive()` create base Context instances;
  `Scope.fork()` creates a base Scope, without propagating receiver subclasses.
- Tree traversal represents leaves with `None` internally, avoiding placeholder
  container data and redundant flatten flags in both traversal paths.
- Node and Wrapper execute directly in `__call__`, without separate `_call`
  methods or nested Wrapper continuations. Node Auto evaluation remains deferred
  to each `call_next` invocation, preserving short-circuiting and Context selection.
- Compose accepts a layer factory and optional default query. `ComposeLayer`
  requires synchronous `register(token, /, *args, **kwargs)` returning a synchronous
  disposer, with no separate deletion method;
  `Compose.register(scope, /, *args, **kwargs)` forwards business arguments and
  returns an exact, single-execution disposer that also owns registration and
  empty-layer cleanup. `layers()` exposes live layers without reconstruction.
  Default Tree/Eval layers reject duplicate exact classes within one Identity;
  child layers can override ancestors. Their static `merge()` methods produce
  query snapshots.
- ContextStore implements registration policy with a `once()` disposer;
  binding writes and deletions enforce stored ownership tokens. An unprotected
  (`None`) token accepts any caller token, but a protected value requires identity
  matching. Revoked registrations cannot delete replacement values. Identity
  records are data-only and remain internal to Binding; writes return no record.
  Registration disposers release their Binding reference in `finally`.
- Store owns viewer and reverse indexes; private bindings own their identities
  and values without sharing mutable Store state. Schema manages Store attachment,
  and ContextView delegates all data access through Context.
- Context coordinates a private shared Schema, shared ContextStore, and one
  owned Lifecycle. Schema access uses Context `resolve()`, `resolve_entry()`,
  and `entries`; direct `ctx.schema` access is removed. Normal access checks
  the calling Lifecycle; cleanup uses exact ownership without active-state checks.
- Context owns the single parent/child tree and exposes a `children` snapshot.
  Lifecycle is bound to its Context, with no independent parent or finalizer;
  child disposal is an effect preserving registration-order LIFO cleanup.
- Exported `ContextStore` and the Context-owned `Lifecycle` type. Ref lives in
  `context.schema`, and tree engines live with their Schema or Store consumers;
  the separate `context.ref` and `context.tree` modules are removed.
- Node inspection rules live alongside Node and Wrapper in `node.core`.
- Schema retains application stores and their Context viewers until explicit
  disposal and directly clears bindings when a field is withdrawn. Field cleanup order between stores
  is unspecified.
- Context and effect disposal use generator control flow for immediate and
  asynchronous cleanup, preserving recursive LIFO order, continued cleanup
  after failure, repeatable results, and cancellation and reentrancy protection.
  Lifecycle owns the cleanup loop and groups failures in cleanup execution order.
- Auto calls all independent evaluator groups before scheduling asynchronous
  results, collecting Ref and Node failures
  into nested exception groups without cancelling siblings. Group messages list
  failed input indices; partial successful results are not exposed. Node and
  Wrapper records retain ordinary failures, including ordinary exception groups,
  through `__cause__` without a duplicate exception field.
  Caller cancellation follows asyncio propagation without aggregating partial
  batch results; Context-owned child cleanup may continue after evaluation exits.
- Renamed `slyme.utils.pytree` to `slyme.utils.tree`, `PyTree*` types to `Tree*`,
  and removed the process-global engine registry, including its namespace
  from `pytree_engine` to `tree_engine`. No compatibility aliases are provided.
- Scope viewers, Context-binding identities, and Schema declarations use direct
  ownership records managed by internal registration and cleanup methods. Compose
  keeps contributing Scopes by registration token and removes each layer after
  its last registration is withdrawn. Cleanup preserves failure replay and
  reentrant identity reuse; cleared values do not return when an identity is reused.
- Raised the minimum supported Python version from 3.9 to 3.10, following the
  upstream CPython maintenance lifecycle, and adopted native 3.10 typing syntax.
- Renamed `RefFactory` to `Schema` and aligned Context construction and
  inspection on `ctx.declare(R)` and Context declaration queries.
- `Ref` is now an immutable path-only value. `Schema.leaf()` and
  `Schema.container()` configure declared paths, while `Schema.resolve()`
  returns their Refs.
- Node and Wrapper build parameters now use explicit `get()`, `set()`, and
  `reset()` methods. Parameter names may overlap framework API names without
  changing attribute behavior.
- Node and Wrapper factories construct the same graph element types for
  immediate and asynchronous functions without inspecting signatures.
- Node and Wrapper calls now pass their current static parameter containers
  directly to user functions instead of creating an implicit frozen snapshot.
- Auto parameters always reconstruct Tree containers, including ordinary-only
  subtrees, while preserving ordinary leaf identities. Each Wrapper `call_next`
  invocation evaluates the current Auto containers without a content pre-scan.
- Context construction accepts only keyword `parent` and `scope`; roots create
  their own Schema and Store with independent framework defaults. Declare application paths and assign values separately
  with `declare()` and `update()`. Importing a Schema copies definitions rather
  than sharing future changes. `root` is a fixed field rather than a property.
  Every Context data access rejects undeclared paths.
  Schema is the only source of container structure; an application root stores
  flat entry-indexed bindings by Scope, uses `to_dict()` for a nested projection,
  and uses `flatten()` for the exact leaf mapping.
- Replaced `replaceable` with Schema leaf `mode`: `"assign"` (default)
  permits `set()`/`delete()`, while `"register"` permits `register()` and
  exact disposer-based removal. Replaced `Context.add()` with `register()`;
  assignments and registrations cannot overwrite or delete one another.
- `Context` now has one lifetime parent and one bound Scope. `fork()` creates an
  owned child and shares the Scope by default; a forked Scope provides an
  explicit local data layer. Context CRUD always uses the bound Scope.
- `Scope.fork()` now creates only a single-parent child; C3 multiple inheritance
  uses explicit `Scope(parents=(...))` construction. Context roots track the
  exact live Context viewers for every Scope instead of anonymous counts.
- Scope construction is keyword-only. Its `parents` input accepts a Scope or a
  tuple, while the stored `parents` attribute is always a tuple.
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
- Removed `slyme.node.signature`, `Spec`, `spec()`, parameter sentinels, and
  `reset()`. Nodes store only explicit bindings; function defaults, variadic
  declarations, and argument validation follow Python's native call semantics.
  Framework call arguments are positional-only and do not reserve business keys.

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
