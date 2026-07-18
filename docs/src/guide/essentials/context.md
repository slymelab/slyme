# Context

[Context](/guide/essentials/context) is Slyme's core data structure for passing state between [Nodes](/guide/essentials/node). It behaves very similarly to Python's dictionary (`dict`), but with two key differences: it is **hierarchical** and **structurally immutable**.

To facilitate type-safe access to deeply nested data in Context, Slyme introduces the concept of [Ref](#ref), which is similar to a dictionary "key" but supports multi-level paths.

## Ref {#ref}

`Ref` is an immutable reference object used to locate data within Context. You can think of it as a path pointing to a specific location inside Context.

### Creating Ref

You can create a `Ref` by passing a dotted string:

```python
from slyme.context import Ref

# Points to 'user' at root level
user_ref = Ref("user")

# Points to 'user.profile.name' in a nested dictionary
name_ref = Ref("user.profile.name")
```

### Deriving Ref

`Ref` provides an `.at()` method that allows you to quickly derive sub-paths based on the current path:

```python
profile_ref = Ref("user.profile")
name_ref = profile_ref.at("name")  # Equivalent to Ref("user.profile.name")
```

::: info
Internally, `Ref` caches the hash value and split path segments (`parts`), providing extremely high performance when frequently using `Ref` for lookups during execution time. Additionally, `Ref` can carry `metadata` to support features like command-line argument configuration.
:::

::: warning Deprecated
The `key_path` parameter of `Ref` is **deprecated** since slyme 0.1.1 and will be removed in 0.2.0. Use [`@expression`](/guide/essentials/node#at-expression) + [`Auto`](/guide/essentials/node#spec) for dynamic value resolution instead. See the [Key Path](#key-path) section below for migration guidance.
:::

### Key Path (Deprecated) {#key-path}

::: warning Deprecated
`Ref.key_path`, along with `CallKey`, `KeyPathExpr`, and the `P` proxy object from `slyme.utils.pytree`, are **deprecated** since slyme 0.1.1 and will be removed in 0.2.0. Use [`@expression`](/guide/essentials/node#at-expression) + [`Auto`](/guide/essentials/node#spec) for dynamic value resolution instead.
:::

**Old pattern (deprecated):**

```python
from slyme.utils.pytree import P

# Get model from Context, then drill into model.config.hidden_size
ctx.get(Ref("model", key_path=tuple(P.config.hidden_size)))
```

**New pattern — use `@expression` + `Auto`:**

```python
from slyme.node import expression, Auto
from slyme.context import Context, Ref

@expression
def get_hidden_size(ctx: Context, /, *, model: Auto[object]) -> int:
    return model.config.hidden_size

# Then in a @node that needs hidden_size, pass the expression:
@node
def my_node(ctx: Context, /, *, hidden_size: Auto[int]) -> Context:
    # hidden_size is already resolved to the actual int value
    return ctx

# Wire them together:
my_node(hidden_size=get_hidden_size(model=Ref("model")))
```

This approach keeps Nodes fully decoupled — a Node only needs to declare it needs a `hidden_size` parameter, without caring about how that value is computed.

### RefFactory

::: tip New in 0.1.1
`RefFactory` is the recommended way to create `Ref` objects using concise attribute-access syntax.
:::

Slyme provides a global `R` instance of `RefFactory`. Using `R`, you can create `Ref` objects with dot-notation:

```python
from slyme.context import R

# Attribute access records a dotted path:
# R.user.profile.name  records "user.profile.name"

# Call it to create the Ref:
name_ref = R.user.profile.name()  # Equivalent to Ref("user.profile.name")
```

`RefFactory` is **immutable** — each attribute access returns a new `RefFactory` instance with the extended path. It can be used anywhere a `Ref` is expected (such as `Context.get()`, `Context.set()`, or Node keyword arguments):

```python
from slyme.context import Context, R

ctx = Context().set(R.status(), "active")

# Pass additional metadata when creating the Ref:
ref_with_meta = R.user.profile.name(metadata={"desc": "User's display name"})
```

`RefFactory` integrates seamlessly with Node instantiation, providing a clean alternative to the deprecated Scope pattern:

```python
from slyme.node import node
from slyme.context import Context, R

@node
def greet(ctx: Context, /, *, name: str, title: str) -> Context:
    return ctx.set(R.greeting(), f"{title} {ctx.get(name)}")

# Use R directly in keyword arguments
node_def = greet(name=R.user.name, title=R.user.title)
```

::: info
Internally, `RefFactory` is resolved to `Ref` transparently — any API that accepts `Ref` also accepts `RefFactory` via the `RefLike` type alias.
:::

## Context

`Context` is the runtime state container for Nodes. Based on **Copy-On-Write** mechanism, each modification to Context does not change the original object but returns a brand new Context instance. This design fundamentally guarantees concurrency safety and state traceability in functional programming.

### Creating Context

You can initialize a `Context` using the `update()` method:

```python
from slyme.context import Context, Ref

ctx = Context().update({
    Ref("user.profile.name"): "Alice",
    Ref("user.profile.age"): 25,
    Ref("status"): "active",
})
```

Printing `ctx` gives you:

```text
Context({
    'user': ContextView({
        'profile': ContextView({
            'name': 'Alice',
            'age': 25,
        }),
    }),
    'status': 'active',
})
```

Here, `ContextView` refers to Context's internal structured view, used to represent hierarchical `Context` structures.

### Reading Data

Use the `get()` method with a `Ref` to retrieve data. If the path doesn't exist, you can provide a default value, otherwise a `ContextPathError` exception is raised:

```python
# Get top-level data
status = ctx.get(Ref("status"))  # 'active'

# Get deeply nested data
name = ctx.get(Ref("user.profile.name"))  # 'Alice'

# Provide a default value when getting non-existent data
email = ctx.get(Ref("user.profile.email"), default="unknown")
```

Additionally, you can use the `extract()` method for more advanced structured reading, supporting arbitrarily nested Python dictionaries, lists, and tuples:

```python
profiles = ctx.extract([
    {
        "age": Ref("user.profile.age"),
        "name": Ref("user.profile.name"),
        "status": Ref("status"),
    }
])
```

In the above example, thanks to the PyTree engine, Slyme resolves all Refs in nested dict/list/tuple structures to their corresponding values while maintaining the original structure. The `profiles` value should be:

```text
[{'age': 25, 'name': 'Alice', 'status': 'active'}]
```

::: warning
Note that the `ctx.extract()` method requires every leaf value to be a `Ref` object and does not allow mixing with regular values. For example, `ctx.extract([Ref("status"), 123])` is not allowed. If you want to parse mixed structures, you should use the more advanced eval API (see [Dependency Injection](/guide/slyme-in-depth/dependency-injection)):

```python
from slyme.node.eval import eval_tree

# NOTE: The value 123 will not be parsed and remains as-is
eval_tree(ctx, [Ref("status"), 123])  # ['active', 123]
```
:::

Other commonly used reading methods:

```python
# Check if a path exists.
ctx.exists(Ref("user.profile"))  # True
ctx.exists(Ref("user.profile.email"))  # False

# List all keys at a specified level (similar to dict's `keys()`).
ctx.keys(Ref("user.profile"))  # dict_keys(['name', 'age'])

# Convert Context recursively to a plain Python dictionary.
ctx.to_dict()  # {'user': {'profile': {'name': 'Alice', 'age': 25}}, 'status': 'active'}
```

### Modifying Data (Copy-On-Write)

Since Context is structurally immutable, all modification methods **return a new Context instance**. The underlying Copy-On-Write algorithm intelligently reuses unmodified subtree memory, ensuring modifications are both safe and efficient.

```python
# Single setting (set)
new_ctx = ctx.set(Ref("status"), "inactive")
# ctx remains unchanged, new_ctx's status becomes inactive

# Batch update (update)
new_ctx = ctx.update({
    Ref("user.profile.age"): 26,
    Ref("user.profile.email"): "alice@example.com"
})

# Delete (delete)
new_ctx = ctx.delete(Ref("user.profile.age"))
```

### Atomic Transactions (mutate)

If you need to perform complex updates and deletions simultaneously, you can use the lower-level `mutate()` method. It ensures all modifications are completed atomically in a single traversal:

```python
new_ctx = ctx.mutate(
    updates={
        Ref("user.profile.status"): "verified"
    },
    drops=[
        Ref("status") # Delete top-level status
    ]
)
```

### Comparing Context (diff)

You can use the `.diff()` method to compare differences between two Context objects. It returns a `ContextDiff` object containing detailed information about additions, deletions, and modifications. This is very useful for debugging, state monitoring, or writing test cases:

```python
new_ctx = ctx.mutate(
    updates={
        Ref("user.profile.status"): "verified",
        Ref("user.profile.name"): "Bob",
    },
    drops=[
        Ref("status")
    ]
)
diff = new_ctx.diff(ctx)
# You can use diff.flatten() to flatten differences into a dictionary
print(diff.flatten())  # {'status': (<DiffMissing.MARK: 1>, 'active'), 'user.profile.status': ('verified', <DiffMissing.MARK: 1>), 'user.profile.name': ('Bob', 'Alice')}
```

Here, `slyme.context.DIFF_MISSING` represents a missing value. In the dictionary returned by `diff.flatten()`, the first element of the tuple is the new value, and the second is the old value. This means `DIFF_MISSING` appearing in the first position indicates deletion; appearing in the second position indicates addition; otherwise, it indicates modification.

## Async Support (Deprecated) {#async-support}

::: warning Deprecated
All `async_*` methods on `Context` and `ContextView` (`async_get`, `async_set`, `async_update`, `async_mutate`, `async_to_dict`, `async_extract`) are **deprecated** since slyme 0.1.1 and will be removed in 0.2.0. Context is **locally stored and synchronous** — use the synchronous methods directly instead.
:::

**Old pattern (deprecated):**

```python
value = await ctx.async_get(Ref("path"))
data = await ctx.async_to_dict()
new_ctx = await ctx.async_mutate(updates={...}, drops=[...])
```

**New pattern — use synchronous methods everywhere:**

```python
value = ctx.get(Ref("path"))
data = ctx.to_dict()
new_ctx = ctx.mutate(updates={...}, drops=[...])
```

These synchronous methods work correctly in both sync and async Nodes — no `await` is needed.

## Context Hook (Deprecated)

::: warning Deprecated
`Hook`, `HookChain`, and the `hook=` parameter on `Context.__init__()` are **deprecated** since slyme 0.1.1 and will be removed in 0.2.0. Context no longer supports hooks. If you need data transformation or interception, use [`@wrapper`](/guide/essentials/node#at-wrapper) nodes to intercept execution at the Node level instead.
:::
