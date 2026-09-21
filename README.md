<div align="center">
  <img src="https://raw.githubusercontent.com/slymelab/slyme/main/assets/images/logo.jpg" alt="Slyme Logo" width="200" style="max-width: 50%;" />

  <p><em>SLYME Lets You Mold Everything.</em></p>

  <p>
    <a href="https://pypi.org/project/slyme/"><img src="https://img.shields.io/pypi/v/slyme.svg?label=PyPI" alt="PyPI version"></a>
    <a href="https://github.com/slymelab/slyme/actions/workflows/ci.yml"><img src="https://github.com/slymelab/slyme/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
    <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python version">
    <a href="https://slymelab.github.io/slyme/"><img src="https://img.shields.io/badge/docs-latest-blue.svg" alt="Documentation"></a>
    <a href="https://github.com/slymelab/slyme/blob/main/LICENSE"><img src="https://img.shields.io/github/license/slymelab/slyme" alt="License"></a>
  </p>

  <p>
    <b>English</b> |
    <a href="https://github.com/slymelab/slyme/blob/main/i18n/README_zh.md">简体中文</a>
  </p>
</div>

## About Slyme

Slyme (pronounced /slaɪm/) is a highly composable functional execution framework. It enables developers to seamlessly build arbitrarily complex execution flows based on simple, reusable functions, without needing to master cumbersome APIs or syntax.

Whether you are building complex LLM pipelines, executing DAGs, or creating generic data-processing flows, Slyme provides a structural, functional, and deeply Pythonic foundation.

`Auto(tree)` explicitly marks a bound parameter for evaluation. It evaluates
registered leaves and reconstructs Tree containers on each
invocation, including containers holding only ordinary values. Ordinary leaves
and evaluator results retain their identities; non-Auto parameters pass through
unchanged. `eval_tree(ctx, tree)` exposes the same evaluation behavior directly.
Tree handlers and Auto evaluators match exact types; subclasses need explicit
registration. The traversal utilities live in `slyme.utils.tree`.

Node and Wrapper factories save only supplied keyword bindings. Native function
defaults apply at invocation; `node(ctx, **kwargs)` overrides saved bindings for
one call before Auto evaluation. Use `get`, `set`, and `delete` to manage bindings.

## Installation

Slyme requires **Python 3.10+**. You can install it directly via pip:

```bash
pip install slyme
```

*(Note: Slyme is extremely lightweight and its only main dependency is `typing_extensions`.)*

## Quick Start

Here is a quick example using `@node`, `@wrapper`, and an ordinary function to assemble the graph:

```python
from time import time
from collections.abc import Callable
from slyme.context import Context, Ref, Schema
from slyme.node import node, wrapper, Auto, Node


R = Schema(
    {
        "input": {
            "articles": Schema.leaf(),
        },
        "output": {"responses": Schema.leaf()},
    }
)


# 1. Define an execution node
@node
def llm_api(ctx: Context, /, *, prompts: list[str], responses: Ref[list[str]]):
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    ctx.set(responses, responses_)


# 2. Define a value-producing node
@node
def format_prompts(ctx: Context, /, *, articles: list[dict]) -> list[str]:
    return [
        f"Summarize: {article['title']}. Content: {article['content']}"
        for article in articles
    ]


# 3. Define a wrapper for middleware (e.g., performance timing)
@wrapper
def timing(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], object],
    /,
    *,
    prefix: str,
):
    start_time = time()
    result = call_next(ctx)
    end_time = time()
    print(f"[{prefix}] Finished successfully in {end_time - start_time:.4f} seconds.")
    return result


# 4. Assemble the pipeline at Build-Time
def build_pipeline() -> Node[None]:
    return llm_api(
        responses=R.resolve("output.responses"),
        prompts=Auto(
            format_prompts(
                articles=Auto(R.resolve("input.articles")),
            )
        ),
    ).add_wrappers(timing(prefix="LLM API Call"))


# 5. Execute at Run-Time
if __name__ == "__main__":
    ctx = Context()
    ctx.declare(R)
    ctx.update(
        {
            R.resolve("input.articles"): [
                {"title": "Article 1", "content": "Content 1"},
                {"title": "Article 2", "content": "Content 2"},
            ]
        }
    )
    build_pipeline()(ctx)
    print(ctx.get(R.resolve("output.responses")))
```

Typed APIs rely on their annotations for argument and callback types, including synchronous versus asynchronous callbacks and `Literal` options. Use a static type checker to validate these calls. Slyme checks dynamic Schema declarations, structural conflicts, and lifecycle constraints at runtime. Assemble reusable Node graphs with ordinary Python functions.

`@node` / `@node()` and `@wrapper` / `@wrapper()` return ordinary keyword-only factory functions. Use `create_node(func, params=None, *, wrappers=None)` or `create_wrapper(func, params=None)` from `slyme.node` to create an instance directly. Business bindings live in `params`, separately from assembly configuration. Each instance shallow-copies its bindings and wrapper list, retaining the referenced values and Wrapper objects. Attach wrappers during assembly with `create_node(..., wrappers=[...])` or `task.add_wrappers(...)`, not on `@node`.

Use `await task.acall(ctx)` and `await ctx.adispose()` when execution or cleanup may be asynchronous. These always-awaitable adapters preserve immediate synchronous work and errors, and do not schedule tasks. Purely synchronous applications can call `task(ctx)` and `ctx.dispose()` directly.

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` builds an outermost-first callable chain without running it. Wrapper order is snapshotted; their parameters remain live. Each wrapper controls calls to the next layer and may return an immediate or asynchronous result.

For mixed execution, decorate an ordinary generator function with `@continuation` or `@continuation()` from `slyme.utils.execution`. Each call drives a fresh generator: `value = yield operation()` accepts an immediate or asynchronous result. The driver runs synchronously until an awaitable is yielded, then returns an unscheduled awaitable remainder. Loops, branches, and `try/except/finally` stay in the generator. Use `await await_result(execute(...))` when either return mode is possible. Place `@node` or `@wrapper` outside `@continuation` to create graph-element factories from these functions. `run(generator)` remains available for directly driving a generator. Neither interface schedules tasks, aggregates errors, or owns resource lifetimes.

Auto owns its independent all-settled evaluation policy; Context owns recursive LIFO cleanup. Both report failures as exception groups without exposing partial successful results. Auto orders errors by input index; Context orders them by cleanup execution. A returned exception remains ordinary data. Node and Wrapper wrap ordinary failures, including ordinary exception groups, in records whose `__cause__` retains the original error; existing Node records and non-`Exception` control failures propagate unchanged.

`slyme.utils.exception.exception_group(message, excs)` groups a non-empty sequence of errors. The module exports `ExceptionGroup` and `BaseExceptionGroup` for catching groups. Python 3.11+ uses native exception groups; Python 3.10 provides a simple fallback with `message` and ordered `exceptions`, without `except*`, subgroup operations, or grouped traceback rendering. Groups containing only `Exception` instances are caught by `except Exception`; groups containing cancellation or other non-`Exception` errors are not.

## Context lifetimes, Scope visibility, and Compose

Use `schema.resolve_entry(path)` to inspect one field and the `schema.entries` tuple to enumerate root, container, and leaf entries. Each public `RefEntry` exposes `ref`, `config`, and `alive`; read metadata through `entry.config.metadata`. Configs use the public `RefConfig`, `RefLeafConfig`, and `RefContainerConfig` types exported by `slyme.context`.

`Schema.leaf()` and `Schema.container()` accept `metadata={"namespace.key": item}`. Items derive from the frozen `Metadata` dataclass exported by `slyme.context`: its default merge accepts the same instance, while subclasses can override `merge()` for immutable content-based composition. Distinct keys coexist, matching keys merge in declaration order, and withdrawal invalidates the merged config for lazy rebuilding. See [Schema metadata](docs/src/guide/essentials/context.md#metadata).

`Ref("")` identifies Schema's permanently declared root container. `ctx.get("")` returns a live root view even without visible values. Root `Context()` installs independent rule Composes at `$.tree.data`, `$.tree.node`, and `$.eval.handlers` through `context/default.py`. They appear in root views and use `register` mode, so `ctx.delete("")` rejects them; delete an assign-only business subtree instead. Deletion preserves inherited data, isolation barriers, declarations, and effects. Root assignment is rejected like any other container assignment.

`set` and `delete` change individual local paths. `update` and `drop` validate
the complete batch before applying changes; preflight failures leave bindings
unchanged, while failures during application do not trigger rollback.

`Schema.leaf()` defaults to `mode="assign"` for `set()`/`delete()`. Declare
`mode="register"` for `ctx.register(ref, value)` and disposer-based removal.
Each mode rejects the other mode's writes. Container deletion, including
`ctx.delete("")`, rejects any `register` descendant before changing data.
Modes govern bindings, not whether the stored objects are mutable.

`Context(*, parent=None, scope=None)` creates ownership and visibility only. Call `ctx.declare(schema_or_dict)` and then `ctx.update(values)` to initialize application data. Importing a Schema copies its current definitions, not its future changes. Context privately composes `Schema` for declarations, `ContextStore` for scoped data, and `Lifecycle` for effects and child ownership. Forks share Schema and Store, but each has its own Lifecycle. Use `ctx.resolve(path)`, `ctx.resolve_entry(path)`, and `ctx.entries` to inspect application declarations; `ctx.declare()` also owns their cleanup. Normal access checks the calling Context's lifecycle, while exact registration withdrawal and Scope release remain available during cleanup. `Lifecycle` can also manage effects independently of Context data.

A Context root holds a live `Schema` reference and owns an application data store and lifetime tree. Each Context has at most one parent and is bound to one immutable `Scope`. `Context.fork()` creates an owned child that shares the current Scope by default; pass `scope=ctx.scope.fork()` when the child needs its own local visibility identity. `Scope.fork()` is single-parent, while explicit `Scope(parents=(...))` construction provides C3 multiple inheritance. Reads follow the bound Scope's C3 order, and writes target the leaf-local identity bound to that Scope. `Compose.derive()` creates a Scope with a private or shared identity for that Compose, without changing other Compose bindings. `effect()` owns immediate or asynchronous setup and cleanup; `register()` and `declare()` own synchronous registration cleanup. `dispose()` returns `None` when finished synchronously or an awaitable for remaining cleanup. Use `await await_result(ctx.dispose())` with `await_result` from `slyme.utils.execution` when either is possible. A Context tree and its mutable Schema and Compose objects belong to one thread, and to one event loop during asynchronous execution; this requirement is not enforced through thread-identity checks. Worker threads or processes should receive ordinary input values and return results for mutation on the owner thread. Registrations return exact disposers for optional early removal:

```python
from slyme.context import Compose, Context, Schema

class ValueLayer(dict):
    def register(self, token, /, value):
        self[token] = value

        def dispose():
            del self[token]

        return dispose

R = Schema({"hooks": Schema.leaf(mode="register")})
root = Context()
root.declare(R)
hooks = Compose(
    factory=ValueLayer,
    query=lambda layers: tuple(value for layer in layers for value in layer.values()),
)
root.register(R.resolve("hooks"), hooks)
agent = root.fork(scope=root.scope.fork(label="agent"))

root.effect(lambda: hooks.register(root.scope, "root"))
agent.effect(lambda: agent.get(R.resolve("hooks")).register(agent.scope, "agent"))
assert hooks.resolve(agent.scope) == ("agent", "root")

agent.dispose()
assert hooks.resolve(root.scope) == ("root",)
root.dispose()
```

`Compose(*, factory, query=None)` creates one application-defined layer per active Identity. The `ComposeLayer` protocol requires a synchronous `register(token, /, *args, **kwargs)` method returning a synchronous disposer; inheriting the protocol is optional. Compose injects only the token and forwards business arguments unchanged. Compose invokes each layer disposer at most once and owns registration bookkeeping and empty-layer cleanup. `resolve(scope, query=None)` uses a default or per-call query; `layers(scope=None, local=False)` lazily exposes layers, with no Scope selecting all active layers. Registration lifetimes determine layer cleanup, independently of its contents. The default Tree/Eval layers reject duplicate class registrations within the same Identity; use another layer to override an inherited handler. See [Compose](docs/src/guide/essentials/context.md#compose).

Scope viewers and shared Context-binding identities track their owners directly in sets. Schema entries map declaration IDs to their original configs and cache the merged config; withdrawal invalidates this cache, and the next config read recomputes it from remaining declarations. Internal registration and cleanup methods remove empty ownership records and their data; Compose removes registrations by unique token and drops a layer when its final registration is withdrawn. Only operations exposed for explicit undo return disposers. A later registration can reuse the Scope or identity, but cleared data does not return.

`Identity(label=None, blocked=False)` fixes shared storage identity and its fallback policy; equal labels do not share storage. `ScopeBinding(identity=Identity(), blocked=False)` adds immutable Scope-local policy, creating a private Identity by default. Both blocking flags default to False. `ctx.derive()` is equivalent to `ctx.fork(scope=ctx.scope.fork())`; omitted, `None`, or empty `bindings` create no explicit bindings. `ctx.derive(bindings={target: config})` creates an owned child with one fresh Scope for all configured targets: declared leaf paths or Compose objects. Each config is a ScopeBinding or an Identity; an Identity is shorthand for `ScopeBinding(identity=identity)`. Individual binding values cannot be `None`: `ScopeBinding()` selects private storage with fallback, `ScopeBinding(identity=shared)` shares storage, and `blocked=True` explicitly stops fallback. `compose.derive(parents=..., binding=...)` and `Compose.derive_many(parents=..., bindings=...)` create a Scope without lifecycle ownership and require explicit parents. Scope construction and derive APIs are keyword-only; parents accepts one Scope or a tuple, stored as a tuple. Context.derive defaults parents to the caller's Scope, but accepts another parent set or `()` independently of the child Context's lifecycle parent. Public APIs do not reconfigure existing Scopes. Both barriers survive payload cleanup. Clean plugin reloads require owned contributions and a clean base Scope; disposal does not undo arbitrary shared assignments or external effects.

Disposal forbids mutations throughout the owned Context subtree before any cleanup runs. Each Context remains readable until its own release; Contexts outside that subtree are unaffected even when they share a Scope.

A Scope usage record holds its viewers and participating Schema leaf entries.
Container deletion validates write modes, then intersects those entries with this index;
deleting a value preserves its identity ownership and isolation barrier.
The last viewer's release removes the Scope index, and final Schema withdrawal
removes the path from every application's index. Reusing a previously released
Scope scans current declarations to restore its surviving identity bindings;
new Scopes do not require that scan.

## Core Advantages

**Native Python Development Experience:** Eliminates heavy object-oriented boilerplate code. You only need to master basic Python functions and native data structures (dictionaries, lists, tuples) to get started quickly.

**Unlimited Composability:** Build arbitrarily complex execution flows with complete decoupling. Thanks to Tree augmentation, Node containment relationships can be represented directly through native Python structures.

**Context-owned Tree Rules:** `TreeEngine` is stateless and receives `TreeRules` explicitly. Configure runtime traversal and evaluation through `DATA_TREE_REF`, `NODE_TREE_REF`, and `EVALUATORS_REF` from `slyme.context`; contributions follow Scope visibility and Context-owned disposal. See the [Tree guide](docs/src/guide/slyme-in-depth/tree-in-slyme.md).

**Explicit Lifetime and Visibility:** Context provides single-parent lifetime ownership, while Scope provides independent C3 visibility. Every Context path, structural role, and write mode is declared by a shared Schema; `flatten()` exposes the visible Ref-to-value mapping, and `Compose` provides ordered, reversible values across Scope hierarchies.

**Seamless Collaboration:** Highly decoupled Nodes communicate through explicit Context paths and Compose objects. This allows teams to independently develop features and write unit tests, reducing "glue code" and deep system coupling.

## Documentation

To dive deeper into Slyme's architecture, including Context management, dependency injection, and the Node lifecycle, please refer to our official documentation site.

**👉 [Read the Official Slyme Documentation](https://slymelab.github.io/slyme/)**

## License

This project is licensed under the Apache-2.0 License.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) for the locked development environment,
tests, quality gates, and pull request workflow. Please report suspected
vulnerabilities privately as described in [SECURITY.md](SECURITY.md).
