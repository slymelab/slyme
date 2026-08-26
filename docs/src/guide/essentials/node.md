# Node

Node is the core logical execution unit in Slyme, embodying the concepts of functional programming. Slyme is based on a rigorous two-stage architecture from Definition (Def) to Execution (Exec), which gives Node extremely high flexibility while ensuring runtime structural safety and efficiency.

::: info
In subsequent chapters (including throughout Slyme documentation), unless otherwise specified, we use "Node" to broadly refer to @node / @expression / @wrapper, while specific types will be explicitly indicated as @node / @expression / @wrapper.
:::

## Node System Design {#design}

All Node classes in Slyme follow these core design principles:

1. **Separation of Def and Exec**: The declaration period and execution period of a Node are strictly separated. When calling a @node-decorated function, it returns a mutable **definition object (Def)**; only after calling `.prepare()` does it transform into an immutable **execution object (Exec)**. This design allows you to dynamically mount @wrappers, modify parameters, and finally "freeze" them into a safe execution instance.
2. **Positional-Only and Keyword-Only**: To distinguish "execution-time parameters" (automatically passed by the framework at runtime) from "build-time parameters" (user-defined configuration when writing Node) in function signatures, Slyme strictly requires all execution-time parameters to be **positional-only parameters** (before `/`), while build-time parameters (user-defined configuration) must be **keyword-only parameters** (after `*`). When instantiating Node objects, users need to pass custom configuration parameters.
3. **Return Value Validation**: Except for lightweight @expression, both @node and @wrapper should return a Context. This ensures the immutable state chain based on Copy-On-Write mechanism can be completely passed along.

::: tip
This chapter introduces basic Node series usage and notes. If you want to fully master Node behavior, it is highly recommended to continue reading the [Lifecycle](/guide/essentials/lifecycle) chapter after this one.
:::

## @node {#at-node}

The @node decorator creates a standard @node node. Its core responsibility is to receive a Context, execute business logic, and return an updated Context (or the original Context).

### Creating @node

You can define a @node as follows:

```python
from slyme.node import node, Auto
from slyme.context import ARG, Arg, Context, Ref, R

@node
def to_upper(ctx: Context, /, *, value: Ref[str]) -> Context:
    return ctx.set(value, ctx.get(value).upper())
```

Note the `/` and `*` in the parameter signature — they are essential. `ctx` must be before `/`, and the configuration parameter `value` must be after `*`.

### Executing Node

Declare every Context value required before execution with `Arg` metadata, then use `run()` as the application-level entry point:

```python
node_def = to_upper(
    value=R.name(metadata={ARG: Arg(type=str, required=True)})
)
value = node_def.run(
    inputs={R.name: "Alice"},
    outputs=R.name,
)
print(value)  # Output: ALICE
```

`run()` prepares a Def automatically, creates or extends a Context, validates the inputs declared by `Arg`, executes the Node, and extracts the requested output Ref PyTree. Omit `outputs` to receive the final Context. With an output schema, set `return_context=True` to receive `(output, context)`.

For lower-level execution, or when reusing one immutable executable while managing Context explicitly, call `node_def.prepare()` once and invoke the resulting Exec directly.

## @expression {#at-expression}

@expression defines a lightweight derived node. Unlike standard Node, Expression **does not require returning a Context**. It is typically used to read and compute a specific value from Context, then pass that value as a parameter to other Nodes.

### Creating and Executing @expression

Defining an @expression is very similar to @node:

```python
from slyme.node import expression
from slyme.context import Context, Ref, R

@expression
def get_greeting(ctx: Context, /, *, prefix: str, name: Ref[str]) -> str:
    name_ = ctx.get(name, default="Guest")
    return f"{prefix} {name_}"

# Create @expression instance
expr = get_greeting(prefix="Hello", name=R.not_exist_path)

# Execute @expression
greeting = expr.prepare()(Context())  # Returns "Hello Guest"
```

::: tip Style Guide
In Slyme, our recommended code style is: do not distinguish Ref types from actual values by function parameter naming. Instead, distinguish them by type annotations. That is, use `name: Ref[str]` instead of `name_ref` or `ref_name`. If both Ref and the value it points to exist inside a function, use a single trailing underscore (like: `name_`) to avoid naming conflicts. This keeps code cleaner and more readable in complex Node structures. Examples in documentation and officially maintained code follow this style.
:::

## @wrapper {#at-wrapper}

@wrapper is the middleware mechanism provided by Slyme, allowing you to intercept and enhance Node execution. Slyme's @wrapper follows the classic "onion model".

### Creating @wrapper

When defining @wrapper, its positional-only parameters must strictly follow the order `(ctx, wrapped, call_next, /)`:

```python
from slyme.node import wrapper
from slyme.context import Context

@wrapper
def logging_wrapper(ctx: Context, wrapped, call_next, /, *, level: str) -> Context:
    print(f"[{level}] Starting execution - Node:\n{wrapped}")

    # Pass control to the next @wrapper or target @node
    ctx = call_next(ctx)

    print(f"[{level}] Execution complete - Node:\n{wrapped}")
    return ctx
```

Here, `wrapped` is the @node instance wrapped by the current @wrapper, and `call_next` is a function to call the next @wrapper or target @node.

### Mounting @wrapper

You can mount one or more @wrappers to a @node instance using the `.add_wrappers()` method:

```python
node_def = to_upper(value=R.name).add_wrappers(
    logging_wrapper(level="INFO")
)

# During execution, logs will be automatically printed
node_exec = node_def.prepare()
new_ctx = node_exec(ctx)
```

## Node Structure Validation {#node-struct}

In Slyme, the legal structure relationships between @node, @expression, and @wrapper are as follows:

- @expression can be held by @node / @wrapper or another @expression — that is, any Node type can use @expression for evaluation computation.
- @wrapper can only be mounted to @node via the `.add_wrappers()` method and cannot be held by other Node types (including @expression and @wrapper). That is, @wrapper can only be used as middleware for @node.
- @node can only be held by other @nodes and cannot be held by @expression or @wrapper. @node is the most core type in the Node system; @expression and @wrapper serve as functional enhancements.
- The meaning of "hold" above is: @node / @expression / @wrapper passes Node type instances through custom parameters, including arbitrarily nested list/tuple/dict, such as a @node's `nodes` parameter passing a list of @nodes, or a @wrapper's `expression_dict` parameter passing a `dict[str, Expression]`, etc.

Under these constraints, we can build arbitrarily complex Node structures, ultimately forming a Node tree.

## Declarative Dependencies (Spec) {#spec}

In Slyme, you may encounter complex configuration parameters that need default values, dynamically generated default values (like empty lists), or even need to be dynamically evaluated at execution time based on Context. For this, Slyme provides `Spec` objects and the convenient `spec` function.

### Spec Resolution Logic

Slyme checks user function parameter signatures and type annotations, finds all legally positioned Spec objects, and merges them. Currently allowed Spec definition locations:

- Specify Spec via function default values
- Specify Spec via **outermost** `Annotated`

Examples (@node is used here; @expression and @wrapper are the same):

```python
from typing import Annotated

@node
def my_node(
    ctx: Context,
    /,
    *,
    param1: str = "123",  # Equivalent to `param1: str = spec(default="123")`
    param2: tuple = spec(default_factory=tuple),  # Creates a new tuple each time default value logic is triggered
    param3: Annotated[int, spec(default=0)],  # Can use Annotated to specify Spec
    param4: Annotated[int, spec(auto_eval=True), spec(default=0)],  # Multiple Specs can be merged, equivalent to `param4: int = spec(auto_eval=True, default=0)`. Here `auto_eval=True` means the parameter can be automatically evaluated at execution time, which is the `Auto` type annotation mentioned earlier — they are equivalent.
    param5: Annotated[int, spec(auto_eval=True)] = 0,  # Similarly, `spec(auto_eval=True)` and default value 0 (i.e., `spec(default=0)`) will be merged
    # param_bad: Annotated[int, spec(default=0)] = 0,  # Error: `spec(default=0)` here conflicts with default value 0, field collision.
    # param_bad2: tuple[Annotated[int, spec(auto_eval=True)], ...]  # Error: When using Annotated to specify Spec, it must be at the outermost layer.
    param6: Annotated[Annotated[int, spec(auto_eval=True)], spec(default=0)]  # Correct: multiple Annotated nesting can be automatically expanded, so it satisfies the outermost constraint.
): ...
```

::: tip
The above example lists legal Spec definition methods to meet some developers' advanced needs and understand Slyme's internal workings. However, most usage scenarios are relatively simple. You can read the following chapters.
:::

### Default Values and Factory Functions

You can define parameter specifications via `spec(default=...)` or `spec(default_factory=...)`, which will fill in user-provided parameters at build-time:

```python
from slyme.node import node, spec
from slyme.context import Context

@node
def process_data(
    ctx: Context,
    /,
    *,
    timeout: int = 30,  # Or `timeout: int = spec(default=30)`
    tags: tuple = spec(default_factory=tuple),
) -> Context:
    # Business logic
    return ctx

process_data()  # timeout=30, tags=()
process_data(timeout=60)  # timeout=60, tags=()
```

### Auto Eval

This is one of Slyme's most powerful features. By setting `auto_eval=True` (or using the built-in `Auto` type hint annotation), you can allow a parameter to be a Ref or @expression at input. Slyme will automatically resolve the actual value from Context before Node execution and inject it into the function parameter:

```python
from slyme.node import node, Auto
from slyme.context import Context, Ref, R

@node
def dynamic_greet(
    ctx: Context,
    /,
    *,
    # Auto[str] is syntactic sugar for Annotated[str, spec(auto_eval=True)]
    # Or you can write `target: str = spec(auto_eval=True)`
    # Auto[str] is the simplest and recommended写法. Note that Auto is always at the outermost layer of type annotation.
    target: Auto[str],
) -> Context:
    # At this point, target has been resolved to the actual string
    print(f"Hello, {target}!")
    return ctx

# Pass a Ref, then at runtime the framework will automatically call `ctx.get(R.user.name)`, and use the returned result as the `target` value.
node_def = dynamic_greet(target=R.user.name)
```

This design makes Node logic extremely pure, completely decoupling "where to get data" from "how to process data". Additionally, `Auto` supports Python standard data structure sniffing, as follows:

```python
from slyme.node import node, Auto
from slyme.context import Context, Ref, R

@node
def process_data(
    ctx: Context,
    /,
    *,
    data: Auto[dict],
): ...

process_data(data={
    "id": R.user.id,  # Will be resolved to actual value at runtime
    "name": some_expression(...),  # Will be resolved to actual value at runtime
    "value": 3,  # Remains unchanged
    "tags": (
        R.user.status,  # Will be resolved to actual value at runtime
        R.user.membership,  # Will be resolved to actual value at runtime
        some_expression2(...),  # Will be resolved to actual value at runtime
    ),
})
```

::: tip
At execution time, the `data` parameter annotated with `Auto` is automatically deeply evaluated by the Slyme framework, automatically calling all contained Refs and @expressions (the evaluation object is the Context parameter input to `process_data`), and finally injecting the obtained actual values into the original structure. This feature enables the Node series to be extremely decoupled. We can combine different Context paths into an expected structure as shown above; we can also directly store this structure in a specific Context path (like `R.data`), then use `process_data(data=R.data)` at creation; we can also define a `data_expression` whose return value is a dict conforming to this structure. And none of this needs to be concerned by the `process_data` @node itself — it only needs to know that the `data` parameter will be passed a dict conforming to a specific structure.
:::

::: tip
If you want to understand how `Auto` works in depth, you can refer to the [Dependency Injection](/guide/slyme-in-depth/dependency-injection) chapter.
:::

## Using Scope to Initialize Node (Deprecated)

::: warning Deprecated
Passing positional `dict` arguments (Scope) to Node factories is **deprecated** since slyme 0.1.1 and will be removed in 0.2.0. Use [`RefFactory`](/guide/essentials/context#reffactory) (`R.x.y.z`) in keyword arguments instead for cleaner, more explicit code.
:::

**Old pattern — positional Scope dicts (deprecated):**

Scope is a standard Python dictionary for injecting function parameters by name. Imagine many @nodes that all need the `user_data` custom configuration parameter. Normally, you would need to assign values like this:

```python
user_data_ref = Ref("user.data")
node1(user_data=user_data_ref)
node2(user_data=user_data_ref)
node3(user_data=user_data_ref)
...
```

To reduce repetitive code at build-time, Slyme provides Scope auto-injection. Now you can directly use a Scope dictionary to initialize all Nodes (dictionary key (`str`) corresponds to function parameter name, value corresponds to the specific value to fill in at build-time):

```python
shared_scope = {"user_data": user_data_ref}
node1(shared_scope)
node2(shared_scope)
node3(shared_scope)
...
```

This is very useful in complex Node structures. When developing new Nodes, you can reference existing Scopes and use parameter names consistently, so instantiating this Node can directly pass the Scope dictionary to automatically bind to corresponding parameters. To prevent parameter name conflicts (for example, two Nodes both have a `value` parameter but with completely different meanings), Slyme supports lookup by priority order:

```python
scope1 = {"value": Ref("value1")}
scope2 = {"value": Ref("value2")}
my_node(scope1, scope2, value=Ref("value3"))
```

The priority order is: keyword arguments > last Scope positional argument > ... > first Scope positional argument. Therefore, in the above example, the `value` parameter resolution priority is `Ref("value3")` > `Ref("value2")` > `Ref("value1")`, ultimately using `Ref("value3")` as the `value` parameter value.

This layered design increases Node reusability. For example, for a @node `create_dataset(dataset=...)`, we can use `train_scope` and `eval_scope` to let the same `create_dataset` function instantiate into a training set @node and an evaluation set @node according to configuration, without modifying `create_dataset` itself at all:

```python
common_scope = {"device": Ref("device"), "max_tokens": Ref("max_tokens")}
train_scope = {"dataset": Ref("train.data")}
eval_scope = {"dataset": Ref("eval.data")}

create_train = create_dataset(common_scope, train_scope)
create_eval = create_dataset(common_scope, eval_scope)
```

**New pattern — use `R` (RefFactory) in keyword arguments:**

```python
from slyme.context import R

# Use R.x.y.z directly in keyword arguments — no Scope dicts needed
create_train = create_dataset(
    device=R.device, max_tokens=R.max_tokens, dataset=R.train.data
)
create_eval = create_dataset(
    device=R.device, max_tokens=R.max_tokens, dataset=R.eval.data
)
```

This approach is more explicit, type-safe, and readable. Since `RefFactory` is resolved transparently to `Ref`, it works anywhere a `Ref` is expected.

## Dynamically Modifying Node

In Slyme, Nodes (@node, @expression, @wrapper) can be dynamically modified before `.prepare()` is called. For example:

```python
my_node = dynamic_greet(target=R.user.name)
my_node["target"] = R.user.another_name  # my_node's target parameter becomes R.user.another_name

# NOTE: Node supports deep modification
another_node["nodes"][0]["value"] = ...
```

In Slyme, we can use the `[]` operator to access/modify Node's build-time parameters. For example, if we want to fine-tune the Node tree returned by a [@builder](/guide/essentials/builder), we can call that @builder in another @builder, modify its return value, and return the modified result — this avoids rewriting most of the same @builder code:

```python
@builder
def my_builder():
    my_node = another_builder()
    my_node["nodes"][0]["abc"] = 123
    return my_node
```

## Marker Constants in Node

There are some constants in Node used for special marking during Node instantiation:

- `UNSET`: Explicitly indicates a Node's parameter is not set, triggering default value logic.
- `UNDEFINED`: Indicates a Node's parameter does not have a valid value set and needs to be set before execution. This means some parameters can be specified later, but when `.prepare()` is called, all parameters cannot be `UNDEFINED`.

Examples:

```python
from slyme.node import node, UNSET, UNDEFINED
from slyme.context import Context, Ref, R

@node
def process_data(
    ctx: Context,
    /,
    *,
    timeout: int = 30,
    tags: tuple,
    data: Ref[dict],
) -> Context:
    # Business logic
    return ctx

process_data(tags=())  # timeout=30, tags=(), data=UNDEFINED

my_node = process_data(timeout=60)  # timeout=60, tags=UNDEFINED, data=UNDEFINED
my_node["tags"] = (1, 2)  # timeout=60, tags=(1, 2), data=UNDEFINED
my_node["timeout"] = UNSET  # NOTE: Triggers default value logic, now timeout=30
my_node["timeout"] = UNDEFINED  # NOTE: Now timeout is explicitly set to UNDEFINED, needs a valid value before `.prepare()`

my_node2 = process_data(timeout=UNSET, tags=(2, 3))  # NOTE: timeout is set to UNSET which triggers default value logic, final result: timeout=30, tags=(2, 3), data=UNDEFINED
# my_node2.prepare()  # Calling `.prepare()` at this point will raise an error because data is not set
my_node2["data"] = R.user.data
my_node2_exec = my_node2.prepare()  # timeout=30, tags=(2, 3), data=R.user.data
# my_node2_exec can now be called for execution
```

::: tip
If you want to understand the complete behavior of Node (@node, @expression, @wrapper) throughout the process, please read the [Lifecycle](/guide/essentials/lifecycle) chapter.
:::

## Async Support (Async Node) {#async-node}

To handle modern I/O-intensive tasks, Slyme provides complete async support. `@node`, `@expression`, and `@wrapper` inspect the decorated function and automatically distinguish regular functions from `async def` functions, so synchronous and asynchronous code use the same decorators.

### Creating an async @node

```python
from slyme.node import node
from slyme.context import Context, Ref, R

@node
async def fetch_user_data(ctx: Context, /, *, url: str, user_data: Ref[dict]) -> Context:
    # Assume do_fetch is some async request function
    # data = await do_fetch(url)
    data = {"id": 1, "name": "Alice"}
    return ctx.set(user_data, data)
```

### Executing an async @node

Async Nodes expose the same high-level contract; await `run()` at the application boundary:

```python
node_def = fetch_user_data(url="https://api.example.com/user", user_data=R.user.data)
user_data = await node_def.run(outputs=R.user.data)
print(user_data)  # {'id': 1, 'name': 'Alice'}
```

::: info
Automatic detection only checks whether the function itself was declared with `async def`; it does not use return type annotations. If a regular `def` returns an Awaitable, use `@node(mode="async")` explicitly. `@expression` and `@wrapper` support the same `mode` parameter. You can also use `mode="sync"` when an explicit synchronous constraint is useful.
:::

::: warning Deprecation
`@async_node`, `@async_expression`, and `@async_wrapper` are deprecated and will be removed in Slyme 0.2.0. Migrate to the unified `@node`, `@expression`, and `@wrapper` decorators respectively; use `mode="async"` for the special case where a regular `def` returns an Awaitable.
:::

## Built-in Common Nodes {#common-nodes}

To simplify development, Slyme provides some commonly used built-in Nodes (continuously updated).

### Sequential Execution

If you want to chain multiple Nodes for sequential execution, you can use `sequential` or `async_sequential`:

```python
from slyme.node import sequential, async_sequential

# Synchronous execution chain
seq_node = sequential(nodes=[node1, node2, node3])
ctx = seq_node.prepare()(ctx)

# Async execution chain (can mix Sync and Async Nodes)
async_seq_node = async_sequential(nodes=[async_node1, sync_node2, async_node3])
ctx = await async_seq_node.prepare()(ctx)
```

Additionally, you can use `sequential_exec` or `async_sequential_exec` to directly execute a list of NodeExec:

```python
from slyme.node import sequential_exec, async_sequential_exec

# In synchronous environment
ctx = sequential_exec(ctx, [node_exec1, node_exec2, node_exec3])
# In async environment
ctx = await async_sequential_exec(ctx, [async_node_exec1, sync_node_exec2, async_node_exec3])
```

This means when a @node accepts a list of @nodes as a parameter, you can more conveniently use `sequential_exec` or `async_sequential_exec` to execute it.
