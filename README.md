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

Slyme adds reusable, bindable functions and scoped data to ordinary Python.
Start with `@node` and `Context`; add higher-order composition, resource ownership and dynamic
contributions when your application needs them. Python 3.10+ is supported.

```sh
pip install slyme
```

## Call a function, bind a configuration

```python
from __future__ import annotations

from slyme.context import Context, Ref, Schema, ref
from slyme.node import auto, node, wrap


@node
def greet(name: str, prefix: str = "Hello") -> str:
    return f"{prefix}, {name}"


name: Ref[str] = ref("example.name")
ctx = Context()
try:
    ctx.declare({"example": {"name": Schema.leaf()}})
    ctx.set(name, "Ada")
    welcome = greet.auto_partial(name=name)
    print(welcome(ctx))  # Hello, Ada
    print(welcome(ctx, prefix="Welcome"))  # Welcome, Ada
finally:
    ctx.dispose()
```

The original function uses ordinary business arguments; `inject_ctx=True` opts into receiving Context.
Calling a Node always receives
a Context followed by keyword business arguments. Calling it executes;
`.partial(**params)` returns a new Node with merged keyword bindings. Call-time
keywords override bindings for one invocation. Python defaults still work.
This example is fully synchronous. In async applications, release the Context
in `finally` with `await await_result(ctx.dispose())` to await cleanup.

## Everyday API

| Need | API and contract |
| --- | --- |
| A reusable execution boundary | `@node`, `@node(params=..., metadata=...)`, or `node(func)` |
| A different configuration | `task.partial(**params)`; original task is unchanged |
| Replace whole configuration fields | `task.replace(params=..., result_to=..., metadata=...)`; omitted fields stay unchanged |
| Access the original callback | `task.func`; call the Node to include Auto evaluation and result binding |
| Name a field | `ref("path")`, usually annotated `Ref[T]`; does not declare or read it |
| Require a declared field now | `ctx.resolve("path")`; does not require a stored value |
| Declare data | `ctx.declare({"value": Schema.leaf()})` |
| Read/write/delete data | `ctx.get(field)`, `ctx.set(field, value)`, `ctx.delete(field)`; fields may be strings or Refs |
| Read a field at invocation | Bind `auto(ref("path"))`; plain Refs remain field handles |
| Run stages in sequence | `sequential_exec(ctx, stages)` from `slyme.node`; same Context, results discarded |
| Surround a Node | A `@node(inject_ctx=True)` receives `wrapped` as a keyword; assemble with `wrap(outer, inner, task)` |
| Publish a result | `task.to(ref("path"))` stores its successful result and returns the same object |

Use callback annotations to retain Node parameter and result types.
`AnyNode[Result]` intentionally erases the parameter contract; use it for
heterogeneous collections, not to avoid checking a known signature.
Bindings and metadata are shallowly read-only. Nested application
objects retain identity; use fresh invocation-local objects for mutable work.
Schema declares paths and write modes, not runtime value types. Validate external
input at the application boundary.

## Choose boundaries by responsibility

Use a Node for a complete capability or phase that callers should be able to
bind, wrap, replace or compose. Its dependencies belong in its parameters. Bind
paths and implementations where the application is assembled; a reusable Node
need not know its caller's field names. Use `inject_ctx=True` only when the function needs Context.

Keep parsing, formatting, arithmetic and other local algorithms as ordinary
functions. Use ordinary classes for state or resource invariants. A Node can
contain multiple statements and call these helpers. Do not add a Node for every
helper or create a second orchestration API inside a service class.

Assemble with ordinary functions, Node sequences and normal loops/branches.
Accept named child Nodes when callers need to replace a role, and sequences for
ordered extensible regions. Reuse the default assembly when changing one role.
Use Compose when independent owners must add/remove contributions at runtime.

## Test the public behavior

Test application contracts with focused cases and reusable fixtures. Parametrize
equivalent inputs; avoid repeating the framework test suite for every helper.
Test ordinary helpers directly. Test a Node with a fresh Context and explicit
arguments. Use `try/finally` with `dispose()` in synchronous tests or
`await await_result(ctx.dispose())` in async tests. Node calls propagate original
exceptions unchanged, including through higher-order Nodes calling `wrapped(ctx)`:

```python
import pytest

# operation, ctx and expected_error belong to your test.
with pytest.raises(type(expected_error)) as caught:
    operation(ctx)
assert caught.value is expected_error
```

Auto evaluation may group sibling
failures; do not assume every failure has one cause. In concurrent tests, observe
an event where it occurs, use explicit gates rather than timing guesses, and
release held work even if an assertion fails.

## Add async work, ownership and visibility when needed

- For callbacks or cleanup that can be sync or async, use
  `await await_result(task(ctx))` and `await await_result(ctx.dispose())` from
  `slyme.execution`. Use ordinary `async`/`await` for an async application;
  `continuation` is for implementations that must preserve both execution modes.
- `ctx.effect(setup)` owns cleanup returned by setup, including asynchronous
  setup. Await asynchronous registration with `await_result`.
- Use `try/finally` to release Contexts on normal completion, exceptions and early
  returns. Await asynchronous cleanup with `await await_result(ctx.dispose())`.
- `ctx.fork()` creates an owned child sharing the same visibility Scope;
  `ctx.derive()` also creates a new Scope for local overrides. Dispose children
  in `finally`. Declared fields inherit values unless a visibility barrier stops them.
- Stop and join application tasks **before** disposing resources they use.
  Context owns effects and child Contexts, not arbitrary running tasks.
- `auto(child_node)` uses a temporary derived Context and disposes it before
  returning the value. Use an explicit child call when writes or resource
  ownership must survive that temporary evaluation.

Consult the [Node guide](docs/src/guide/essentials/node.md) for calls, binding and
composition. The [Context guide](docs/src/guide/essentials/context.md),
[lifecycle guide](docs/src/guide/essentials/lifecycle.md) and
[inspection guide](docs/src/guide/slyme-in-depth/inspection.md) cover advanced
capabilities independently. [CONTRIBUTING.md](CONTRIBUTING.md) describes the
locked development environment and checks. Report vulnerabilities as described in
[SECURITY.md](SECURITY.md). Licensed under Apache-2.0.
