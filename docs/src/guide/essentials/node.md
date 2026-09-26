# Node

`@node` turns a function into a factory for mutable, directly callable `Node` objects. A Node may update `Context`, perform side effects, coordinate child Nodes, or return a derived value for `Auto` injection.

## Define and create a Node

A Node factory accepts only keyword bindings. Execution passes Context positionally and the merged bindings by keyword; the function's own signature controls how they are received:

```python
from slyme.context import Context, Ref, Schema
from slyme.node import Auto, node

R = Schema({"input": {"x": Schema.leaf()}, "output": {"total": Schema.leaf()}})


@node
def add(ctx: Context, *, x: int, y: int, output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add(x=Auto(R.resolve("input.x")), y=2, output=R.resolve("output.total"))
```

Slyme does not inspect function signatures or resolve type annotations. Functions may declare `*args` and `**kwargs`; Python binds the positional Context and supplied keywords normally. Node's framework `ctx` argument is positional-only, so a business keyword named `ctx` can be forwarded independently.

`@node` and `@node()` both return an ordinary factory function; each call creates a separate Node. Use `create_node()` to construct an instance directly, without defining a factory:

```python
from slyme.node import create_node

inline = create_node(lambda ctx, *, value: value + 1, {"value": 3})
```

`create_node(func, params=None, *, wrappers=None)` accepts business bindings as a mapping and assembly configuration separately. Each instance copies its parameter mapping and wrapper list, but keeps the referenced values and Wrapper objects. Construction does not execute the function or evaluate Auto bindings. Attach wrappers during assembly through `create_node(..., wrappers=[...])` or `task.add_wrappers(...)`; `@node` does not accept wrappers. Decorated factories preserve function metadata and expose the wrapped function through `.__wrapped__`.

## Execute

Call a Node directly when managing Context yourself:

```python
ctx = Context()
ctx.declare(R)
ctx.set(R.resolve("input.x"), 3)
result = task(ctx)  # 5
```

The caller owns Context construction, external input handling, and output extraction. Core Node execution neither infers a Schema nor creates a Context implicitly.

There is no Def/Exec conversion or `prepare()` step. Each call binds and resolves the current parameters and wrappers.

## Synchronous and asynchronous composition

A Node returns `T | Awaitable[T]` according to the actual Auto, Wrapper, user-function, and temporary Context cleanup results. Purely synchronous calls return directly. A regular `def` can return an awaitable without declaring a mode; a synchronous parent can consume async Auto inputs after they complete.

```python
from slyme.utils.execution import await_result


async def execute(ctx):
    try:
        return await await_result(task(ctx))
    finally:
        await await_result(ctx.dispose())
```

`await_result()` accepts either an immediate value or an awaitable. The ordinary call happens before its result is passed to the adapter, so synchronous work and errors still occur immediately. The adapter does not create tasks or schedule asynchronous work.

`await_result()` awaits only the outer execution result, not values inside containers. Async continuations run when awaited or scheduled, although their synchronous prefix may already have run. Synchronous applications can use `asyncio.run(await_result(task(ctx)))` at their entry point; await within an existing loop instead of nesting loops. Slyme never automatically offloads blocking functions to threads.

Calls inside user functions still need explicit handling: synchronous code cannot compute with an unknown `child(ctx)` result. Use `async def` and `await await_result(child(ctx))`, or drive a generator as below. A directly returned awaitable denotes execution; wrap it in an ordinary container to pass it as data.

### Generator-based composition

`@continuation` from `slyme.utils.execution` turns a generator function into a directly callable synchronous-or-asynchronous function. A yielded ordinary value is immediately sent back; a yielded awaitable returns an unscheduled asynchronous remainder. Awaited failures are thrown at the suspended `yield`, so one set of loops, branches, and `try/except/finally` handles both execution modes:

```python
from slyme.node import Node
from slyme.utils.execution import continuation


@node
@continuation
def increment_child(ctx, *, child: Node[int]):
    value = yield child(ctx)
    return value + 1
```

`@continuation()` is equivalent to `@continuation`. The decorator preserves function metadata and argument types, and each call creates a fresh generator without sharing execution state. Place it inside `@node` or `@wrapper`: first adapt execution, then define the factory. A decorated function can also be called directly without either graph decorator.

Calling the decorated function executes its synchronous prefix immediately, including synchronous errors. Await the returned remainder to continue asynchronous work: `await await_result(execute(...))`. Use `asyncio.run(await_result(execute(...)))` only at a synchronous application entry point. The driver neither starts an event loop nor schedules tasks. `execute.flat_call(...)` creates a request holding a fresh generator without advancing it; `run(execute.flat_call(...).generate())` has the same behavior as calling `execute(...)`.

`Flatten` interprets its generator's return value as one final yield. All `@continuation` entry points use this same behavior. Awaitables are awaited once and `Flatten` requests are expanded; the result is passed through without interpreting it again. This keeps the result independent of whether earlier yields completed synchronously or asynchronously. Containers and bare generators remain ordinary data; put an awaitable or a `Flatten` request inside a container to return it as data. A returned awaitable runs after the generator has finished, including its `finally` blocks. Use `return (yield operation())` when completion belongs inside the generator's exception handling or must precede cleanup. Node does not automatically drive a returned generator.

Use `yield child.flat_call(...)` to execute another continuation on the same explicit stack. `yield child.flat_start(...)` runs its synchronous prefix and returns either the completed value or an unscheduled awaitable for the remainder. The caller can collect these awaitables for concurrent execution, or yield one to wait for its result. `Flatten(generator, mode="call" | "start")` provides the same stack operations and return-value handling for a raw generator. Its `generate()` method adds the final yield; use `run(Flatten(generator).generate())` to apply the same rule at the root. Raw `run(generator)` leaves the generator's return value untouched. Ordinary calls and `yield from` retain Python's nested call stack.

Hand exclusive driving of the generator to `run()`; generator exhaustion and reentrancy follow Python's protocol. The driver does not cache results, shield cancellation, aggregate errors, or own resources. Cancellation is thrown at the suspended yield and follows the generator's exception handling. A `finally` block may yield asynchronous cleanup while the remainder is being awaited, but discarding a remainder does not finish that cleanup.

Execution order, concurrency, and result collection belong to the caller. A loop that yields each call waits for that call before proceeding. A concurrent implementation can first call its items, retain the returned awaitables, and yield one asynchronous aggregation operation. Create `gather()` or Tasks inside that asynchronous operation when the caller may not yet have a running event loop. Synchronous exceptions during enumeration or calls follow the caller's `try/except/finally`; the driver invents no batch policy.

Auto independently owns its all-settled evaluation policy. It calls every evaluator group and child inline before awaiting asynchronous results; only asynchronous results are scheduled as Tasks. Failed items do not cancel siblings. Each child disposes its Context in `finally`, on success or failure. Failures form nested exception groups in input order, with failed input indices in each group's message; successful results are returned only when the entire batch succeeds. Node and Wrapper wrap ordinary failures, including ordinary exception groups, in records whose `__cause__` retains the original error. Existing Node records and non-`Exception` control failures propagate unchanged. Cancelling a batch follows asyncio propagation without aggregating partial results and may return before Context-owned cleanup finishes; see [Auto lifetimes](./lifecycle.md#auto-values). Context owns its separate recursive LIFO cleanup policy; both consumers use the generator driver without sharing an execution-policy API.

## Parameters and Auto

Function parameters describe the values the function receives. Wrap a bound parameter tree in `Auto(...)` at construction or invocation to request evaluation:

```python
@node
def parent(ctx, *, child: int):
    return child + 1


@node
def child(ctx, *, value: int):
    return value


root = parent(child=Auto(child(value=4)))
assert root(Context()) == 5
```

`Auto` recursively resolves registered evaluator leaves such as `Ref` and `Node`. A Ref reads the supplied Context directly. Each value-producing child Node receives an owned child Context bound to a distinct `ctx.scope.fork()`. Slyme disposes that Context and its effects before parent execution continues, so child-Scope writes and Context-owned registrations do not leak into the parent or a sibling Auto child. Returned values, mutations to shared leaf objects, direct registrations whose disposers were not adopted by the child Context, and external side effects without registered cleanup are not isolated. Without `Auto`, Ref and Node objects are passed through unchanged.

Only explicitly supplied bindings are stored. Absent parameters use native function defaults; missing required or unexpected arguments raise Python `TypeError` when the function is invoked, retained by the Node or Wrapper exception record. Auto work can therefore run before a parameter error is discovered. Defaults are not scanned or evaluated by Slyme: bind dynamic inputs explicitly rather than placing `Auto(...)` in a function default. An Auto object nested inside an ordinary, unwrapped parameter is ordinary data.

## Dynamic modification

Node and Wrapper parameters remain mutable between calls through an explicit parameter API:

```python
root.get("child").value.set("value", 10)
assert root(Context()) == 11
assert root(Context(), child=20) == 21  # Does not execute the bound child.
```

Use `get(name)` to read a binding, `set(name, value)` to store any binding, and `delete(name)` to remove one. Deleting an absent binding is a no-op. Reading a missing key raises `KeyError` unless `get(name, default)` supplies a fallback, which may be any value including `None`. The fallback is returned without saving a binding; an existing value, including `None`, takes precedence. These operations never read function defaults. The live, read-only `params` mapping contains only saved bindings. `node(ctx, **kwargs)` shallowly overrides those bindings for one call before Auto evaluation, without updating `params` or merging nested containers. Parameter names may overlap framework attributes because parameters are not projected as object attributes.

Factories and mutable bindings accept dynamic keyword names and values, including partial bindings and Auto trees. Their typing preserves execution result types but does not statically validate each binding against the underlying function's parameters.

At call time, non-Auto parameters are passed directly to the user function. Mutating their containers therefore updates the live Node or Wrapper parameter. Every Auto parameter is traversed and its containers reconstructed according to Tree rules, including subtrees containing only ordinary values. Ordinary leaves and evaluator results retain their original identities; this is not a deep copy.

Call the Node factory or an assembly function again when another independently configurable graph is required. Copy mutable application values explicitly according to their own semantics; Slyme does not guess which shared references should be duplicated.

## Wrappers

`@wrapper` and `@wrapper()` define reusable factories. `create_wrapper(func, params=None)` directly creates a Wrapper with its own shallow-copied bindings.

`@wrapper` factories also accept only keyword bindings. Execution passes Context, the wrapped Node, and the next callable positionally, followed by the merged keyword bindings. User functions may receive them with named parameters or `*args`/`**kwargs`. The framework's `Wrapper.__call__` parameters are positional-only; direct calls may independently override business keywords named `ctx`, `wrapped`, or `call_next`.

```python
from collections.abc import Callable
from slyme.node import Node, wrapper
from slyme.utils.execution import continuation


@wrapper
@continuation
def trace(ctx, wrapped: Node, call_next: Callable, *, name: str):
    print(name, "start")
    try:
        return (yield call_next(ctx))
    finally:
        print(name, "end")


task.add_wrappers(trace(name="add"))
```

Wrappers use onion ordering and read their live parameters when invoked. The example waits for synchronous or asynchronous completion and runs its `finally` block on success or failure. A forwarding wrapper may return `call_next(ctx)` unchanged without `@continuation`. An `async def` wrapper can instead use `await await_result(call_next(ctx))`. Slyme does not rewrite a wrapper's `try/finally`.

`Node[R]` accepts `Wrapper[R]`: wrappers preserve the resolved result type, including when short-circuiting, but may introduce asynchronous work. This is a static typing requirement, not a runtime value check. Express a change of result type through another Node that composes the original.

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` assembles the same onion chain without executing it. It snapshots wrapper order, with the first wrapper outermost, and returns a callable accepting a Context with the same resolved result type. Wrapper parameters remain live. Each wrapper controls whether and how often it invokes the next layer and which Context it passes. An empty wrapper iterable returns `terminal` unchanged. Node execution uses this method internally; when composing externally around a Node, using `call_next=task` also runs any wrappers already attached to that Node.

## Composition structure

Node and Wrapper parameters may contain arbitrary values and nested Trees, including other Nodes or Wrappers. Slyme imposes no global legality check on the object graph. Both use the same immediate-or-awaitable execution protocol.

## Sequential composition

Use `sequential(nodes=[...])` for a declarative sequence or `sequential_exec(ctx, nodes)` for an existing iterable. Both return `None` after waiting for each completion before starting the next Node, and remain synchronous when every step is synchronous. A failure stops execution and propagates the failing Node's exception without collecting earlier results. They share the supplied Context, so later steps observe earlier local writes, unlike Auto child evaluation.
