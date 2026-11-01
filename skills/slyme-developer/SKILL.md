---
name: slyme-developer
description: API guidance, architectural best practices, and code-style conventions for projects built with Slyme. Use when designing, developing, reviewing, or debugging applications based on the Slyme framework.
---

# Develop with Slyme

Start with [the everyday API](../../README.md); consult the [Node guide](../../docs/src/guide/essentials/node.md)
for calls, binding, results and errors. Slyme extends ordinary functions
with callable, bindable Nodes and scoped Context data. Most application logic
needs Node calls, normal arguments/results and Context data operations. Add
lifecycle, visibility and dynamic composition only when their semantics are needed.

## Define a contract and choose its boundary

Use a Node for a complete capability or phase that callers should be able to bind,
wrap, replace or compose. Put dependencies in parameters; bind application paths
and implementations at assembly. Use native parameters by default; opt into Context with `@node(inject_ctx=True)`. A Node's
size follows a responsibility, not a line count or an instruction to turn every
function into a Node.

Keep local parsing, formatting and algorithms in ordinary functions. Classes can
own state and resource invariants. Reuse these helpers from Nodes. Keep the
replaceable workflow visible in composition rather than hiding it inside a large
service method or creating another workflow builder.

Use ordinary functions to assemble Nodes. A named child Node expresses a role;
a sequence expresses an ordered extensible region. Normal loops and branches
express control flow. Use `sequential` for a plain linear pipeline. Compose is for
independent owners adding/removing contributions at runtime, not for a fixed list.

## Public API

Use the [Node guide](../../docs/src/guide/essentials/node.md) for calls, binding,
replacement and Auto, and the [Context guide](../../docs/src/guide/essentials/context.md)
for data, lifecycle and Compose contracts.
Bind business inputs before `wrap(outer, inner, task)`; calls and partials apply
only to the outer Node. `.to(ref_or_tree)` publishes a successful result and
returns it unchanged. Keep intermediate/conditional state writes explicit.
Import callable factories from `slyme.node`, data/scope objects from
`slyme.context`, and mixed sync/async helpers from `slyme.execution`.

## Ownership and verification

`ctx.fork()` shares the parent's Scope; `ctx.derive()` creates a new Scope. Both
are owned child lifetimes. Visibility and lifetime ownership are independent.
Use `ctx.effect` for owned setup/cleanup. Release Contexts in `finally`:
use `ctx.dispose()` for fully synchronous cleanup and
`await await_result(ctx.dispose())` when cleanup may be asynchronous.
Stop and join tasks before releasing the Context/resources they use; Context
does not own arbitrary tasks. A Context tree belongs to one thread/event loop;
offloaded work receives ordinary values and returns results to the owner.

For mixed sync/async results and disposal use `await_result` from
`slyme.execution`. Prefer normal `async`/`await` in async applications. Read
[async execution](references/async.md) when preserving both execution modes.

Implement the smallest coherent change satisfying the complete public contract.
Test public Nodes with fresh Contexts and explicit arguments. Reuse fixtures
and parametrized cases; cover public seams without duplicating the framework
suite for every helper. Node calls propagate original exceptions unchanged,
including through `wrapped(ctx)`. Auto may group failures. Assert the expected
exception type, identity and chaining directly. For concurrent tests,
observe events before unrelated awaits, control progress with explicit gates,
and release held work on assertion failure.

Consult [core examples](references/core-api.md), the
[Context guide](../../docs/src/guide/essentials/context.md) and the
[Tree guide](../../docs/src/guide/slyme-in-depth/tree-in-slyme.md) for advanced rules
such as identity sharing, barriers, Compose layers and Tree/evaluator registration. They are references, not mandatory reading before
writing an ordinary business function.
