# Node

`@node` adapts a native Python function into a callable, bindable Node. Use
`inject_ctx=True` when the function needs the execution Context. Calling either kind of
Node always requires a Context:

```python
from slyme.context import Context, Schema, ref
from slyme.node import node


@node
def add(x: int, y: int = 2) -> int:
    return x + y


ctx = Context()
try:
    ctx.declare({"input": Schema.leaf(), "total": Schema.leaf()})
    ctx.set("input", 3)
    task = add.auto_partial(x=ref("input")).to(ref("total"))
    assert task(ctx) == 5
    assert ctx.get("total") == 5
    assert add.func(x=3) == 5
finally:
    ctx.dispose()
```

The original function has ordinary arguments and returns an ordinary value.
Use a normal signature such as `def add(x, y=2)`; keyword-only parameters are
optional, not a requirement of Slyme. The Node currently supplies business
arguments by keyword.
`.func` exposes that function; direct calls bypass binding, Auto and result binding.
Slyme does not inspect signatures or infer Context injection
from argument names or annotations. `inject_ctx=False` is the default.

## Explicit Context access

Use `@node(inject_ctx=True)` for resource setup, child orchestration, visibility changes
or intermediate state writes:

```python
@node(inject_ctx=True)
def increment(ctx: Context, counter):
    value = ctx.get(counter) + 1
    ctx.set(counter, value)
    return value
```

`inject_ctx` configures the **underlying function**, not the Node's invocation interface.
Execution passes Context positionally only when enabled and business parameters
by keyword. Node's execution Context argument is positional-only, so a business
keyword named `ctx` can be bound independently. No Context is created implicitly.

Recommended factories are `node`, `auto` and `ref`. `Node` and `Ref` remain
available for annotations and explicit construction. The Auto marker is internal.
`field: Ref[int] = ref("count")` supplies the expected type; use annotations rather
than `ref[int](...)`. `ref` neither declares nor reads a field.

## Binding and configuration

`@node`, `@node()` and `node(func)` return a Node. The factory accepts `inject_ctx`,
`params`, `result_to` and `metadata` as keyword-only configuration. The class accepts
the same fields with a keyword-only `func`.

| Operation | Meaning |
| --- | --- |
| `task.partial(**params)` | Merge business bindings into a new Node |
| `task.auto_partial(**params)` | Wrap each supplied value in `auto`, then merge bindings |
| `task(ctx, **overrides)` | Execute; call-time bindings override saved ones |
| `task.get(name, default=...)` | Read an unevaluated binding |
| `task.replace(params=..., result_to=..., metadata=...)` | Replace entire supplied fields |
| `task.to(result_to)` | Shorthand for `replace(result_to=result_to)` |

Omitted replace fields retain their values; None clears them. Function and Context
mode stay fixed. Configuration changes never execute the function or evaluate
Auto. Function defaults remain native defaults; only explicit bindings are saved.

`task.auto_partial(x=value)` is exactly `task.partial(x=auto(value))`.
It reevaluates on each invocation. Later partials and call-time overrides replace
bindings before evaluation, so overridden sources do not run. Strings remain
literal values; use `ref("path")` to read Context data.

`func`, `inject_ctx`, `params`, `result_to` and `metadata` are read-only properties. Params
and metadata are shallow mapping snapshots taken when the Node is created.
A configured decorator such as `decorate = node(params=bindings)` retains the
configuration references until application. Changes to `bindings` before
`decorate(func)` affect that new Node; subsequent changes leave it unchanged.
Reusing the decorator takes a fresh snapshot for each Node.
Nested application values and result destination trees retain identity; they are not frozen or
copied recursively. Use fresh data for invocation-local mutation. Nodes compare by
identity. No parameter set/delete API is provided.

Metadata is a mapping from names to `Metadata` instances; it is neither evaluated
nor merged with business parameters. A business argument named `metadata` can still
be bound through `partial(metadata=value)`.

## Optional result binding

A single destination stores the completed result; a tree selects matching paths:

```python
@node
def summarize(text: str):
    return {"summary": text[:80], "length": len(text)}


whole = summarize.to(ref("result"))
selected = summarize.to({"summary": ref("answer")})
plain = selected.to(None)
```

Declare destinations before execution. Ref leaves store the entire value at their
selected position, including dicts, lists and None. A single Ref selects the whole
result. String paths are also accepted. Unselected result fields are ignored;
missing selected fields raise rather than silently skip. Tree behavior uses the
execution Context's data Tree rules.

Publication happens after this Node's successful completion, including awaiting
its result, and before returning the **same result object**. Function failure or
cancellation does not publish this Node's result. Earlier child writes remain.
Each destination's declared mode chooses the assignment or registration group; one tree
may mix both. Registrations belong to the execution Context and disappear when
it is disposed. The returned object's cleanup methods are not called automatically,
and an Auto child's resource lifetime is not extended. Use explicit `ctx.register`
if you need an early disposer.

Selected result paths and destination declarations are all checked before writing.
Duplicate register targets (including string/Ref aliases) fail before any writes;
duplicate assign targets keep only the last value. `result_to` is a mapping, not an
interleaved write sequence: Node calls `register_many` first, then `set_many`,
skipping empty groups. Registration failure withdraws this batch's installations
and prevents assignments. Assignment failure does not undo successful registrations
or already applied assignments; the two groups are not one transaction.
Repeated registration in the same identity still conflicts. Use explicit Context
operations when intermediate writes or a particular interleaving are required.

For conditional return shapes, store the whole result or use a small `inject_ctx=True`
Node that branches and writes explicitly. Intermediate state, rollback information
and writes needed on failure also belong in ordinary Context operations.

## Higher-order composition

A Node can take another Node as an ordinary parameter. The conventional name for
its immediate inner Node is `wrapped`:

```python
from slyme.execution import continuation
from slyme.node import AnyNode, wrap


@node(inject_ctx=True)
@continuation
def trace(ctx: Context, wrapped: AnyNode, name: str):
    print(name, "start")
    try:
        return (yield wrapped(ctx))
    finally:
        print(name, "end")


traced = wrap(trace.partial(name="add"), task)
```

`wrap(outer, inner, task)` is exactly
`outer.partial(wrapped=inner.partial(wrapped=task))`. All arguments are ordered
outermost to innermost, with the target last. At least one Node is required;
`wrap(task)` returns the original task. Flat and nested assembly agree:
`wrap(outer, inner, task)` matches `wrap(outer, wrap(inner, task))`.
Each layer decides whether and how often to
call its immediate child, which Context to pass and what value to return. There is
no separate Wrapper class or execution protocol. Other compositions can use
parameters such as `primary`, `fallback` or `children`.

Bind target business arguments **before** wrapping. Calls and partials act on the
outer Node; nothing automatically forwards arguments, derives Contexts or moves
result bindings. To change a nested target, reassemble or replace the explicit
`wrapped` binding. `.to(...)` on the inner Node publishes its completion; `.to(...)`
on the outer Node publishes the outer result, including a short-circuit result.

## Synchronous and asynchronous composition

A Node returns `R | Awaitable[R]`, depending on its function, Auto inputs and
cleanup. Synchronous calls execute immediately. Use `await await_result(task(ctx))`
from `slyme.execution` when either mode is possible. It does not schedule tasks or
offload blocking work. Ordinary async applications use `async def` and release
the Context in `finally` with `await await_result(ctx.dispose())`.

`@continuation` adapts a generator function: `value = yield operation()` accepts
an immediate or asynchronous result and throws failures back at the yield.
Normal loops, branches and try/finally handle both modes. Place `@node(inject_ctx=True)`
outside it when orchestrating Context-aware calls. A regular generator without
`@continuation` remains ordinary return data.

Use `return (yield child(ctx))` when completion must happen before cleanup;
`return child(ctx)` leaves the generator before the returned awaitable completes.
`yield child.__call__.flat_call(ctx)` executes a child Node on the driver's explicit
stack. For continuation functions, `.flat_call(...)` and `.flat_start(...)` offer
call and start modes; neither owns Context lifetimes. See the
[lifecycle guide](./lifecycle.md) for cleanup and [advanced execution guidance](../slyme-in-depth/functional-programming-basics.md)
for composition.

Node calls propagate original exceptions unchanged. A layer catching
`wrapped(ctx)` receives the child's original exception. Auto may group multiple
failures; Node calls preserve those groups. Cancellation also propagates unchanged.

## Parameters and Auto

Plain Node and Ref parameters remain objects. Bind `auto(value)` explicitly to
resolve a Tree of registered evaluator leaves. Each Auto child Node executes in
an owned derived Context, which is disposed before its value is delivered to the
parent. Ref reads use the supplied Context. Call a child explicitly when its
writes or resources must remain visible in that Context.

Defaults are not scanned for Auto. An Auto marker inside an ordinary unmarked
parameter remains data. Non-Auto containers preserve identity; Auto containers
are reconstructed according to Tree rules while ordinary leaves and evaluator
results preserve identity. Auto reevaluates on every invocation, including retry
calls through `wrapped`. Auto uses all-settled evaluation and groups failures;
it does not expose partial successful results.

`sequential_exec(ctx, nodes)` executes stages in order in the same Context and
discards results. The bindable equivalent is `sequential.partial(nodes=stages)`.
Use normal Python for other control flow; there is no required pipeline builder.

## Static types and migration

`Node[P, R]` retains the **full original function signature** P and completed result
R. P includes Context when `inject_ctx=True`. `AnyNode[R]` means `Node[..., R]` and erases
only parameters. Usually let decorators infer P. `.func` is checked with its full
original signature, including keyword-only names; partials and replacements
preserve it. Binding and execution kwargs remain dynamic, since ParamSpec cannot
represent an arbitrary subset of keyword bindings or Auto substitutions. The
inject_ctx flag/signature relationship is explicit configuration, not statically checked.

When migrating older code:

- Add `inject_ctx=True` to functions that still accept Context; remove unused Context
  parameters from native business functions.
- Replace Wrapper callbacks with `@node(inject_ctx=True)` and keyword `wrapped`;
  replace `call_next(ctx)` with `wrapped(ctx)` and assemble with `wrap`.
- Bind business arguments before wrapping. There is no `wrappers` field,
  `Wrapper.compose` or wrapper-specific exception record.
- Include Context in explicit `Node[P, R]` annotations where applicable.
