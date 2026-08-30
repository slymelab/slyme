# Context

`Context` is Slyme's hierarchical mutable data store. Its outer shell is frozen—attributes such as the internal data object cannot be replaced—while data operations mutate that object in place and return `None`.

## Ref and R {#ref}

`Ref` identifies a semantic data path:

```python
from slyme.context import R, Ref

assert R.user.name == Ref("user.name")
```

`R` is an immutable `RefFactory` that supports attribute syntax and optional metadata:

```python
from slyme.context import ARG, Arg, R

name = R.user.name(metadata={ARG: Arg(type=str, required=True, help="User name")})
```

The path remains a stable semantic name independent of the physical Node graph.

## Read and write

```python
from slyme.context import Context, R

ctx = Context()
ctx.set(R.user.name, "Ada")
ctx.update({R.user.age: 36, R.status: "active"})

assert ctx.get(R.user.name) == "Ada"
assert ctx.exists(R.user.age)
assert ctx.extract({"name": R.user.name, "age": R.user.age}) == {
    "name": "Ada",
    "age": 36,
}
```

Mutation methods such as `set`, `update`, `mutate`, `drop`, `delete`, and `clear` update the same Context and return `None`. Do not write `ctx = ctx.set(...)`.

## Structured operations

Context accepts Ref PyTrees for batch reads and writes. `extract` preserves the requested Python structure; `mutate` applies a function over selected paths. See the API reference for the exact accepted schemas.

## Isolation

Because Context data is mutable, concurrent branches must not silently share it when independent state is required. The caller must create an explicitly copied Context before branching. This makes the isolation boundary visible and leaves sequential execution free to share one Context efficiently.

Node parameter snapshots and Context data serve different purposes: Node calls freeze their parameter containers locally, while values retrieved from Context retain their original Python types.
