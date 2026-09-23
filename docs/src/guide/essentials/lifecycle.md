# Lifecycle

Each Context creates one private `Lifecycle(ctx, dispose_mode=...)` to manage its effects, disposal mode, and disposal state. `ctx.dispose_mode` is a read-only property forwarding to Lifecycle without storing another copy. Context alone stores the parent/child tree; Lifecycle follows its `ctx` to find ancestors and children. Child disposal is registered as an internal parent effect and follows the parent's disposal mode. After all effects finish, including on failure, Lifecycle calls `Context._finalize()` for internal ownership and data cleanup; an application root also detaches its Store from Schema. Lifecycle has no independent parent or finalizer configuration. Register application cleanup with `ctx.effect()` rather than overriding `Context.dispose()`.

Effect `setup`, `dispose`, and `finalize`, Lifecycle `dispose`, and Context `_finalize` are wrapped with `once` on each instance, shadowing the class methods under the same names. Each instance retains its own result or failure. Finalization only performs internal bookkeeping; application code must call `dispose()` to run owned cleanup first.

Lifecycle state progresses through `ACTIVE → DISPOSE_PENDING → DISPOSING → DISPOSED`. `DISPOSE_PENDING` forbids mutations before that Context's cleanup starts, including while ancestor cleanup is running. Preparation visits only active branches: pending branches are already prepared, and branches being disposed retain their current state.

Finalization runs even when effect cleanup fails. If finalization also fails, its exception propagates with the cleanup exception group retained as its `__context__`, following Python's `finally` semantics without rewriting `__cause__`. Lifecycle reaches `DISPOSED` even on failure; repeated disposal observes the same terminal exception.

`ctx.children` returns a tuple snapshot of direct children in creation order. A child remains attached during asynchronous cleanup and is removed when release finishes, even on failure. Disposed Contexts have no children but retain their original `parent` reference. Scope inheritance is independent of this ownership tree.

Construction registers child ownership before acquiring Scope visibility. Acquisition registers every MRO viewer before restoring binding data. Restoration and default-installation failures use Context disposal for rollback; Store acquisition does not roll itself back. Each viewer registration must be released exactly once, with repeated Context disposal and finalization handled by their instance-local `once` wrappers.

Slyme uses one live `Node` graph rather than separate definition and execution trees. Creating a decorated function builds a mutable Node; calling it executes that same Node with its current parameters.

## Context facets

`Context[A]` associates one business object with a Context through the ordinary read-only `facet: A` attribute. Bare `Context` and construction without a factory default to `Context[Any]`; an explicit `Context[None]` is available but not required. The facet's contents may be mutable. The framework does not require a base class or protocol, proxy facet methods, or automatically call its setup or disposal methods.

`Context()`, `fork()`, and `derive()` accept `facet_factory(ctx)`. Each factory runs once and synchronously receives the new Context after its ownership, Scope viewers, root defaults, and any derive bindings are ready. The result is stored unchanged; factory results are not awaited. The facet is unavailable until the factory returns, and the factory itself is not retained. Prefer registering effects after construction. Effect registration inside factories is not prohibited or specially checked, but a factory failure cannot rely on synchronous construction rollback to await asynchronous setup or cleanup.

```python
from slyme.context import Context


class Plugin:
    def __init__(self, ctx: "Context[Plugin]"):
        self.ctx = ctx
        self.active_ctx: Context | None = None

    def unload(self):
        if self.active_ctx is not None:
            return self.active_ctx.dispose()


root = Context()
instance = root.fork(facet_factory=Plugin)  # Context[Plugin]
plugin = instance.facet                   # Plugin, not Plugin | None
plugin.active_ctx = instance.fork()       # No inherited facet
plugin.unload()                           # This example has synchronous cleanup
plugin.active_ctx = instance.fork()       # A fresh activation
root.dispose()
```

Facets belong to Context instances, not Scope identities. A child receives `None` unless its own factory provides a value, even when it shares its parent's Scope. A child's facet type is independent of its parent's type. Existing objects can be shared explicitly with a factory such as `lambda ctx: existing_plugin`. Disposal retains the facet for inspection; Context and facet references follow ordinary Python object lifetime. Resource cleanup still belongs in effects, not object collection. Plugin discovery, dependency management, and active-context bookkeeping remain extension responsibilities.

## Build and modify

```python
from slyme.context import Context, Schema
from slyme.node import Auto, node

R = Schema(
    {
        "user": {"age": Schema.leaf(), "name": Schema.leaf()},
        "a": Schema.leaf(),
        "b": Schema.leaf(),
        "items": Schema.leaf(),
    }
)


@node
def process(ctx: Context, /, *, timeout: int = 30, data: list):
    return timeout, data


task = process(data=Auto([R.resolve("user.age"), R.resolve("user.name")]))
task.set("timeout", 60)
```

Node parameters and wrappers may be changed between calls. A change never requires recompiling the whole graph and becomes visible on the next call.

## Live calls and explicit branches

At the start of each Node or Wrapper call, Slyme:

1. shallowly snapshots saved bindings and applies this call's keyword overrides;
2. separates ordinary values from explicitly wrapped Auto trees;
3. builds the wrapper chain and evaluates Auto parameters before each user function invocation;
4. passes static parameter containers directly to the user function.

Parameter bindings are shallow-snapshotted, not deep-copied. Mutating a non-Auto `list`, `dict`, or other leaf from inside a Node or Wrapper mutates the live parameter and is visible to later calls. Every Auto parameter is traversed and its containers reconstructed according to Tree rules, whether or not they contain evaluatable leaves. Ordinary leaves and evaluator results remain shared.

Call the relevant Node factory or assembly function again when another independently configurable graph is required. `context.fork()` creates an owned lifetime child and shares `context.scope` by default. Use `context.fork(scope=context.scope.fork())` when that child needs a separate local data layer with live Scope C3 lookup. To materialize `assign` fields into a new root, create it, declare the copied paths, then call `snapshot.update(context.get("app").flatten())`; `register` fields require explicit `register()` calls on the new owner. None of these operations copies application values.

A Context owns its children and cleanup registered through `effect()`, `register()`, and `declare()`. Its `dispose_mode` selects sequential or batch cleanup of these directly owned items. `dispose()` returns `None` on synchronous completion or an awaitable when cleanup is asynchronous; `await await_result(ctx.dispose())` handles either. Context does not own arbitrary tasks using it: stop and await those tasks before disposal.

Calling `dispose()` immediately marks the Context as disposing and runs its synchronous portion; await its asynchronous portion to schedule it. Early cleanup stays owned until completion, so owner disposal joins it. Removing an early registration preserves the remaining release order.

Sequential cleanup waits for each item before calling the next. Batch cleanup calls every disposer before awaiting their asynchronous results together. Both visit items in reverse registration order; synchronous work runs inline without requiring an event loop. A failure or cleanup cancellation does not skip remaining ownership or Scope release. Context reports failures in reverse registration order, independently of completion order; subsequent disposal calls observe that same terminal result without repeating cleanup. Each effect's disposer waits for its own setup before cleanup. Lifecycle uses `once` and `SharedAwaitable` to share execution and results. Setup and cleanup must not reenter disposal of themselves, their owner, or an ancestor, or await disposal containing themselves; Lifecycle does not detect these unsupported calls or wait cycles.

Cancelling a setup or disposal waiter does not cancel the shared operation. Its caller must still await completion and handle failures. Slyme does not retrieve background failures just to suppress asyncio's unobserved-exception diagnostics; those diagnostics are not a substitute for application error handling.

## Cleanup groups

`Context()`, `fork()`, and `derive()` accept `dispose_mode="sequential" | "batch"`, defaulting to `"sequential"` on every new Context. The mode is immutable and is not inherited from the parent. It affects cleanup only, not setup or Node execution. A parent waits for a child's complete cleanup regardless of the child's internal mode.

```python
root = Context()
root.declare(R)
plugins = root.fork(dispose_mode="batch")
plugin_a = plugins.fork()
plugin_b = plugins.fork()
```

Here plugin_a and plugin_b clean up as a batch, awaiting their asynchronous work concurrently, each in local LIFO order. Root declarations and framework defaults remain installed until both finish. Additional forks allow arbitrary nesting of cleanup modes without adding Scope inheritance layers. Each operation belongs to the Context on which it is called; no implicit current group or disposer transfer is involved.

Siblings in a batch must tolerate overlapping cleanup. Register shared dependencies on a sequential parent before creating their consumers, or establish explicit waits. Read access during cleanup does not prevent a concurrent effect from withdrawing a field. Plugin dependency ordering is not inferred from Context visibility.

Different cleanup functions can wait on the same early disposer; it executes once and all waiters join its completion. Cross-group waits must be acyclic. Put an operation's prerequisites inside its shared cleanup, not separately at each caller, and avoid an independent cleanup entry point that bypasses them. Shared waits do not count users or wait for every caller to arrive. Cleanup failures remain observable along each waiting branch and may appear in multiple nested failure groups.

## Auto values

Static parameter values and values retrieved from `Context` keep their normal Python mutability:

```python
ctx = Context()
ctx.declare(R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=Auto([R.resolve("a"), R.resolve("b")]))(
    ctx
)  # Auto produces the evaluated list [1, 2]
process(data=Auto(R.resolve("items")))(ctx)  # data is the list stored in Context
```

Auto Ref values are read from `ctx`. Every Auto child Node instead executes with an owned child Context and a distinct child Scope. Child calls and synchronous cleanup run inline; asynchronous results are scheduled concurrently when awaited, retaining input order. Each child disposes its Context in `finally` on success or failure, independently of other siblings. Without caller cancellation, evaluation waits for every child and its cleanup before reporting errors or running the parent function.

Auto attempts every sibling and every evaluator group, even after a synchronous failure. Evaluator groups are independent and may execute concurrently; a Ref lookup failure does not prevent Node evaluation. A child failure or cancellation does not cancel siblings: evaluation waits for all of them. Failures form nested exception groups: the outer group follows evaluator-group order of first appearance, and built-in evaluator groups follow their inputs' order. Only failures are included; each evaluation group's message lists the original failing indices. Partial successful results are not exposed. A child cleanup failure becomes that child's error; if the Node also failed, Python's `finally` semantics retain the Node exception as the cleanup error's `__context__`. Node and Wrapper record ordinary groups through `__cause__`; groups containing non-`Exception` control failures propagate unchanged. Exception objects returned as ordinary values remain data. A child that never finishes keeps evaluation pending; application code owns timeouts and abort policies.

Cancelling the outer evaluation Task propagates to its pending children through asyncio, without aggregating partial evaluation results. Each child enters its `finally` cleanup. Cancellation during the cleanup wait can let evaluation exit while Context-owned cleanup continues in the background. A synchronous child runs inline on the event-loop thread and cannot be interrupted while executing. Task cancellation does not guarantee that underlying network, thread, or process work has stopped; application adapters own that behavior.

Values returned by an Auto child must not depend on resources owned by its child Context: those resources are closed before the parent function runs. Transfer ownership explicitly or use a longer-lived Context when returning such a value. The child still shares mutable leaf objects and cannot undo files, network requests, or other external side effects that did not register cleanup.

Explicit orchestration has different semantics. Calling a Node directly or using `sequential_exec(ctx, children)` passes the selected Context itself, so those steps intentionally observe one another's local writes.

Cancelling a direct `await await_result(ctx.dispose())` waiter does not cancel scheduled cleanup, but that waiter may exit first. Await the same disposal again before shutdown to observe its retained result. A parent joins child cleanup only while that child remains owned. Auto does not return its temporary child Contexts; after cancelled evaluation, a background cleanup failure may therefore reach neither the caller nor a later parent disposal.

Application code owns Context construction, external input validation, and output extraction. Invoke the graph with `node(ctx)` and dispose with `ctx.dispose()`. Handle either immediate or awaitable results with `await await_result(...)`, or yield these calls inside a `@continuation` function; both helpers are in `slyme.utils.execution`.
