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

Calls inside user functions still need explicit handling: synchronous code cannot compute with an unknown `child(ctx)` result. Use `async def` and `await child.acall(ctx)`, or drive a generator as below. A directly returned awaitable denotes execution; wrap it in an ordinary container to pass it as data.

### Generator-based composition

`run(generator)` from `slyme.utils.continuation` drives ordinary generator control flow. A yielded ordinary value is immediately sent back; a yielded awaitable returns an unscheduled asynchronous remainder. Awaited failures are thrown at the suspended `yield`, so one set of loops, branches, and `try/except/finally` handles both execution modes:

```python
from slyme.node import Node
from slyme.utils.continuation import run


@node
def increment_child(ctx, *, child: Node[int]):
    def execute():
        value = yield child(ctx)
        return value + 1

    return run(execute())
```

Creating the generator does not execute its body. Calling `run()` executes its synchronous prefix immediately, including synchronous errors. Await the returned remainder to continue asynchronous work: `await await_result(run(execute()))`. Use `asyncio.run(await_result(run(execute())))` only at a synchronous application entry point. The driver neither starts an event loop nor schedules tasks.

Only values explicitly handed to `yield` are inspected. Containers and bare generators remain ordinary data. Each yield awaits only its outer result; the generator's final return value is not implicitly awaited. Use `return (yield operation())` when completion belongs inside the generator's exception handling. Delegate another generator with `yield from`, or explicitly yield its `run()` result. Node does not automatically drive a returned generator.

Hand exclusive driving of the generator to `run()`; generator exhaustion and reentrancy follow Python's protocol. The driver does not cache results, shield cancellation, aggregate errors, or own resources. Cancellation is thrown at the suspended yield and follows the generator's exception handling. A `finally` block may yield asynchronous cleanup while the remainder is being awaited, but discarding a remainder does not finish that cleanup.

Execution order, concurrency, and result collection belong to the caller. A loop that yields each call waits for that call before proceeding. A concurrent implementation can first call its items, retain the returned awaitables, and yield one asynchronous aggregation operation. Create `gather()` or Tasks inside that asynchronous operation when the caller may not yet have a running event loop. Synchronous exceptions during enumeration or calls follow the caller's `try/except/finally`; the driver invents no batch policy.

Auto independently owns its all-settled evaluation policy. It calls every evaluator group and child inline before awaiting asynchronous results; only asynchronous results are scheduled as Tasks. Failed items do not cancel siblings. It reports nested `BatchError` objects from `slyme.utils.exception`, with input-ordered `Result(value=..., error=...)` records retaining successful values and raised errors separately. Cancelling a batch follows asyncio propagation without aggregating partial results; owned child Context cleanup completes before evaluation exits. Context owns its separate recursive LIFO cleanup policy; both consumers use the generator driver without sharing an execution-policy API.

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

Wrappers use onion ordering and read their live parameters when invoked. The example above is synchronous-only: when `call_next(ctx)` returns an awaitable, its following statements run before that completion. A forwarding wrapper may return it unchanged. Use a generator driven by `run()` with `yield call_next(ctx)`, or `async def` with `await await_result(call_next(ctx))`, for result-dependent work and completion-time `try/finally` cleanup. Slyme does not rewrite a wrapper's `try/finally`.

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` assembles the same onion chain without executing it. It snapshots wrapper order, with the first wrapper outermost, and returns a callable accepting a Context. Wrapper parameters remain live. Each wrapper controls whether and how often it invokes the next layer, which Context it passes, and the result type. An empty wrapper iterable returns `terminal` unchanged. Node execution uses this method internally; when composing externally around a Node, using `call_next=task` also runs any wrappers already attached to that Node.

## Composition structure

Node and Wrapper parameters may contain arbitrary values and nested Trees, including other Nodes or Wrappers. Slyme imposes no global legality check on the object graph. Both use the same immediate-or-awaitable execution protocol.

## Sequential composition

Use `sequential(nodes=[...])` for a declarative sequence or `sequential_exec(ctx, nodes)` for an existing iterable. Both return `None` after waiting for each completion before starting the next Node, and remain synchronous when every step is synchronous. A failure stops execution and raises `BatchError` with the attempted prefix. They share the supplied Context, so later steps observe earlier local writes, unlike Auto child evaluation.
