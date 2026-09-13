# Node

`@node` turns a function into a factory for mutable, directly callable `Node` objects. A Node may update `Context`, perform side effects, coordinate child Nodes, or return a derived value for `Auto` injection.

## Define and create a Node

A Node function has exactly one non-keyword-only runtime parameter. Every build parameter must be keyword-only:

```python
from slyme.context import Context, Ref, Schema
from slyme.node import Auto, node

R = Schema({"input": {"x": Schema.leaf()}, "output": {"total": Schema.leaf()}})


@node
def add(ctx: Context, *, x: Auto[int], y: Auto[int], output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add(x=R.resolve("input.x"), y=2, output=R.resolve("output.total"))
```

Runtime parameter names and annotations are optional; Slyme identifies runtime and build parameters by parameter count and keyword-only placement.
Variadic `*args` and `**kwargs` parameters are not supported: runtime arity and
build parameter names must remain explicit.

## Execute

Call a Node directly when managing Context yourself:

```python
ctx = Context(schema=R)
ctx.set(R.resolve("input.x"), 3)
result = task(ctx)  # 5
```

The caller owns Context construction, external input handling, and output extraction. Core Node execution neither infers a Schema nor creates a Context implicitly.

There is no Def/Exec conversion or `prepare()` step. Each call binds and resolves the current parameters and wrappers.

## Synchronous and asynchronous composition

A Node returns `T | Awaitable[T]` according to the actual Auto, Wrapper, user-function, and temporary Context cleanup results. Purely synchronous calls return directly. A regular `def` can return an awaitable without declaring a mode; a synchronous parent can consume async Auto inputs after they complete.

```python
async def execute(ctx):
    try:
        return await task.acall(ctx)
    finally:
        await ctx.adispose()
```

`task.acall(ctx)` and `ctx.adispose()` always return awaitables. They delegate to the ordinary call and disposal methods through `await_result()` from `slyme.utils.continuation`; synchronous work and errors still occur immediately when called. They do not create tasks or schedule asynchronous work.

`await_result()` awaits only the outer execution result, not values inside containers. Async continuations run when awaited or scheduled, although their synchronous prefix may already have run. Synchronous applications can use `asyncio.run(await_result(task(ctx)))` at their entry point; await within an existing loop instead of nesting loops. Slyme never automatically offloads blocking functions to threads.

Calls inside user functions still need explicit handling: synchronous code cannot compute with an unknown `child(ctx)` result. Use `async def` and `await child.acall(ctx)`, or return a Continuation composition as below. A directly returned awaitable denotes execution; wrap it in an ordinary container to pass it as data.

### Lazy result chains

`Continuation` lets an ordinary function compose immediate and asynchronous results without defining an async continuation:

```python
from slyme.node import Node
from slyme.utils.continuation import Continuation, await_result


@node
def increment_child(ctx, *, child: Node[int]):
    return Continuation.call(lambda: child(ctx)).then(lambda value: value + 1).unwrap()
```

`Continuation.call(operation)` defers the operation itself. `Continuation.resolve(value)` wraps a result that already exists; in `Continuation.resolve(operation())`, Python calls `operation()` before constructing the chain. `then(success, failure)` handles upstream completion; a paired failure callback does not catch errors from its success callback. A later `catch(recover)` handles those errors. Both kinds of callback may return immediate or asynchronous results.

Constructing or extending a chain runs no callbacks. `unwrap()` executes its synchronous prefix immediately and returns either the final value or an unscheduled awaitable remainder. `aunwrap()` preserves that immediate work and its errors, but always returns an awaitable; use `await chain.aunwrap()` in asynchronous code. The chain itself is not awaitable. Use `asyncio.run(await_result(chain.unwrap()))` at a synchronous entry point or `asyncio.create_task(await_result(chain.unwrap()))` to schedule the remainder within a running loop. A callback returning another chain must call its `unwrap()` explicitly; a bare chain is ordinary data.

`then()` and `catch()` append to the same mutable operation list and return the same object. Aliases are not independent branches; use the fluent return as the current typed stage. Each chain can be executed only once, and cannot be extended after execution starts. Build another chain for another execution. Continuation has no subscribers or cached settlement to notify, unlike JavaScript Promise. A caller needing shared execution can explicitly create and retain a Task. Continuation does not shield cancellation or own cleanup; Context retains its separate repeatable, cancellation-safe disposal behavior.

`catch()` and the failure callback of `then()` handle `Exception` by default, leaving cancellation and other `BaseException` subclasses untouched. Pass `exceptions=SomeException` to select an exception class, or explicitly select `BaseException` for lifecycle cleanup that must observe cancellation. Recovery then determines whether to propagate it again.

`Continuation.sequential(values, call)` builds a `Continuation[None]` that executes calls in order and discards their results. Iteration starts on `unwrap()` or `aunwrap()`. It consumes the next input only after the preceding call completes; failure or cancellation stops iteration. Use `then()` or `catch()` before unwrapping to handle completion.

`Continuation.batch(values, call)` builds a `Continuation[list[T]]` of independent calls. Execution attempts every input despite failures and stays synchronous until a call returns an awaitable. Awaiting the remainder schedules that call and the remaining inputs concurrently. When all calls finish, the batch returns results in input order or raises `BatchError`, exported from `slyme.utils.continuation`. `then()` receives the result list; `catch()` receives one exception, as on any other chain:

```python
from slyme.utils.continuation import BatchError


def recover(error: BatchError) -> list[str]:
    return [f"input {index}: {failure}" for index, failure in error.errors.items()]


result = (
    Continuation.batch(["1", "invalid", "3"], int)
    .then(lambda values: [str(value * 2) for value in values])
    .catch(recover, exceptions=BatchError)
    .unwrap()
)
```

`BatchError.errors` maps zero-based input indices to failures, including when only one call fails. Nested batches retain their own local indices. An iterator failure stops enumeration and is recorded at the next input index; started calls still finish. Returned exception objects remain data, and failed batches do not return partial results. A failed or cancelled item does not cancel siblings. Cancelling the batch waiter propagates through asyncio to pending calls; if other failures also occur, `BatchError` preserves them with cancellation as its cause, otherwise cancellation propagates directly. Batch provides no timeout or resource cleanup policy.

## Parameters and Auto

Every build parameter has a `Spec`. `Auto[T]` is shorthand for enabling automatic evaluation:

```python
@node
def parent(ctx, *, child: Auto[int]):
    return child + 1


@node
def child(ctx, *, value: int):
    return value


root = parent(child=child(value=4))
assert root(Context()) == 5
```

`Auto` recursively resolves registered evaluator leaves such as `Ref` and `Node`. A Ref reads the supplied Context directly. Each value-producing child Node receives an owned child Context bound to a distinct `ctx.scope.fork()`. Slyme disposes that Context and its effects before parent execution continues, so child-Scope writes and Context-owned registrations do not leak into the parent or a sibling Auto child. Returned values, mutations to shared leaf objects, direct registrations whose disposers were not adopted by the child Context, and external side effects without registered cleanup are not isolated. Without `Auto`, Ref and Node objects are passed through unchanged.

Missing required build parameters are represented by `UNDEFINED` and rejected when the Node is called. Use `UNSET` to request a declared default explicitly.

## Dynamic modification

Node and Wrapper parameters remain mutable between calls through an explicit parameter API:

```python
root.get("child").set("value", 10)
assert root(Context()) == 11
```

Use `get(name)` to read, `set(name, value)` to replace, and `reset(name)` to restore a parameter's declared default or `UNDEFINED`. The read-only `params` mapping exposes all current parameters. Parameter names may overlap framework attributes such as `func`, `get`, or `wrappers` because parameters are not projected as object attributes.

At call time, non-Auto parameters are passed directly to the user function. Mutating their containers therefore updates the live Node or Wrapper parameter. Every Auto parameter is traversed and its containers reconstructed according to Tree rules, including subtrees containing only ordinary values. Ordinary leaves and evaluator results retain their original identities; this is not a deep copy.

Call the Node factory or an assembly function again when another independently configurable graph is required. Copy mutable application values explicitly according to their own semantics; Slyme does not guess which shared references should be duplicated.

## Wrappers

`@wrapper` functions have exactly three non-keyword-only runtime parameters: Context, the wrapped Node, and the next callable. Build parameters remain keyword-only.

```python
from collections.abc import Callable
from slyme.node import Node, wrapper


@wrapper
def trace(ctx, wrapped: Node, call_next: Callable, *, name: str):
    print(name, "start")
    result = call_next(ctx)
    print(name, "end")
    return result


task.add_wrappers(trace(name="add"))
```

Wrappers use onion ordering and read their live parameters when invoked. The example above is synchronous-only: when `call_next(ctx)` returns an awaitable, its following statements run before that completion. A forwarding wrapper may return it unchanged. Use `Continuation.call(lambda: call_next(ctx)).then(transform).unwrap()` for result-dependent work in an ordinary function, or `async def` and `await await_result(call_next(ctx))` when using native `try/finally` for completion-time cleanup. Slyme does not rewrite a wrapper's `try/finally`.

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` assembles the same onion chain without executing it. It snapshots wrapper order, with the first wrapper outermost, and returns a callable accepting a Context. Wrapper parameters remain live. Each wrapper controls whether and how often it invokes the next layer, which Context it passes, and the result type. An empty wrapper iterable returns `terminal` unchanged. Node execution uses this method internally; when composing externally around a Node, using `call_next=task` also runs any wrappers already attached to that Node.

## Composition structure

Node and Wrapper parameters may contain arbitrary values and nested Trees, including other Nodes or Wrappers. Slyme imposes no global legality check on the object graph. Both use the same immediate-or-awaitable execution protocol.

## Sequential composition

Use `sequential(nodes=[...])` for a declarative sequence or `sequential_exec(ctx, nodes)` for an existing iterable. Both wait for each completion before starting the next Node and remain synchronous when every step is synchronous. They share the supplied Context, so later steps observe earlier local writes, unlike Auto child evaluation.
