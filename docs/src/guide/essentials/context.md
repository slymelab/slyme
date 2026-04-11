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
Internally, `Ref` caches the hash value and split path segments (`parts`), providing extremely high performance when frequently using `Ref` for lookups during execution time. Additionally, `Ref` can carry `metadata` and `key_path` (used for [PyTree](/guide/slyme-in-depth/pytree-in-slyme) parsing) and other advanced metadata to support features like command-line argument configuration.
:::

### Key Path

The path in `Ref("a.b.c")` can only access Context's own structured data, but cannot further penetrate into leaf nodes. Slyme provides Key Path functionality to enhance access to Context structures. You can understand this through the following example:

```python
from slyme.utils.pytree import P

# If `hidden_size` is directly stored on the Context path, we can directly get it
ctx.get(Ref("hidden_size"))
# If `hidden_size` needs to be obtained from the model config stored in Context, since model itself is a regular user object and does not belong to the Context structure, we can use Key Path
ctx.get(Ref("model", key_path=tuple(P.config.hidden_size)))
```

Note that `P` is a special proxy object supporting dot attribute access (`P.a`), getitem access (`P[...]`), and call operations (`P(*args, **kwargs)`). The final effect of the above example is: first get the value from Context's `"model"` path, then call `.config.hidden_size` on it, and return the final result. Key Path functionality further decouples Nodes — a Node only needs to declare it needs a `hidden_size` parameter, without caring about how that `hidden_size` is computed.

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

## Async Support {#async-support}

In async environments (such as `@async_node`), Slyme provides async versions of Context methods, usually prefixed with `async_`:

- `await ctx.async_get(ref)`
- `await ctx.async_set(ref, value)`
- `await ctx.async_update(updates)`
- `await ctx.async_mutate(updates=..., drops=...)`
- `await ctx.async_to_dict()`

This allows you to seamlessly and safely manipulate Context in both synchronous and asynchronous Nodes. Note that Context itself is **locally stored and synchronous**; async environment operations are fully supported by Context Hook.

## Context Hook

To provide greater extensibility, `Context` supports mounting Hooks. Through Hooks, you can intercept and modify Context's read (Extract) and write (Mutate) behaviors.

This is very useful in many advanced scenarios, such as:
- **Data Transformation**: Automatically deserialize on read, automatically serialize on write.
- **External Storage Mapping**: Map data at specific paths to external storage (like Redis or databases), making Context behave like a virtual filesystem.

### Custom Hook

You can create custom Hooks by inheriting from `slyme.context.Hook` and overriding relevant methods:

```python
from typing import Any
from slyme.context import Hook, Context, Ref, ExtractResult, MutateResult

class MyLoggingHook(Hook):
    def on_extract(
        self,
        *,
        ctx: Context,
        refs: tuple[Ref, ...],
        values: tuple[Any, ...],
        **kwargs,
    ) -> ExtractResult:
        print(f"[Read] Paths: {[r.path for r in refs]}, Values: {values}")
        # Must return ExtractResult, you can modify values to change the actual read value
        return ExtractResult(values=values)

    def on_mutate(
        self,
        *,
        ctx: Context,
        updates: dict[Ref, Any],
        drops: set[Ref],
        **kwargs
    ) -> MutateResult:
        print(f"[Write] Updates: {updates}, Drops: {drops}")
        # Must return MutateResult, you can modify updates or drops to change actual write behavior
        return MutateResult(updates=updates, drops=drops)
```

::: info
The corresponding async methods are `on_async_extract` and `on_async_mutate`. By default, if you only implement the sync version, the async version directly calls the sync version. But if you need to perform true async I/O (like querying a database), you should override the async version methods. The methods in the [Async Support](#async-support) chapter actually call the async Hook methods.
:::

### Mounting Hook

During Context initialization, mount it via the `hook` parameter:

```python
ctx = Context(hook=MyLoggingHook())

# Will trigger on_mutate to print logs
new_ctx = ctx.set(Ref("status"), "active")

# Will trigger on_extract to print logs
status = new_ctx.get(Ref("status"))
```

### HookChain

If you need to apply multiple Hooks simultaneously, you can use `HookChain` to combine them:

```python
from slyme.context import HookChain

chain = HookChain(hooks=(HookA(), HookB(), HookC()))
ctx = Context(hook=chain)
```

In `HookChain`, Hooks are executed sequentially:
- For `extract`, the `values` modified by the previous Hook become the input for the next Hook.
- For `mutate`, the `updates` and `drops` modified by the previous Hook become the input for the next Hook.
