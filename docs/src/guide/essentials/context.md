# Context

`Context` is Slyme's hierarchical mutable data store. Its outer shell is frozen—attributes such as the internal data object cannot be replaced—while data operations mutate that object in place and return `None`.

## Ref and RefFactory {#ref}

`Ref` identifies a semantic data path:

```python
from slyme.context import Ref

name = Ref("user.name")
assert name.path == "user.name"
```

For an application, declare its available paths once with an immutable
`RefFactory`. Attribute access is then limited to that schema:

```python
from slyme.context import ARG, Arg, Ref, RefFactory

refs = RefFactory(
    {
        "user": {
            "name": Ref(metadata={ARG: Arg(type=str, required=True, help="User name")}),
            "age": ...,
        },
        "status": ...,
    }
)

name = refs.user.name()
```

`RefFactory` always requires a schema mapping, and Slyme does not export a
global factory. This keeps each application's reference namespace explicit.

The path remains a stable semantic name independent of the physical Node graph.

`...` is shorthand for an unbound `Ref()` declaration. Construction binds each
declaration to its complete path and freezes a private copy of the schema.

```python
from slyme.context import ARG, Arg, Ref, RefFactory

refs = RefFactory(
    {
        "input": {
            "": Ref(metadata={"description": "Application inputs"}),
            "name": Ref(metadata={ARG: Arg(type=str, required=True)}),
            "age": ...,
        },
        "output": {
            "message": ...,
        },
    }
)

assert refs.input().path == "input"
assert refs.input.name().path == "input.name"
refs.input.naem  # raises AttributeError and suggests "name"
```

Every attribute access returns a `RefFactory`, so it can be passed
directly anywhere a `RefLike` is accepted. Calling it without arguments returns
the real `Ref` created while parsing the schema. A mapping branch receives a
default Ref when the empty key is omitted; the empty key customizes that
branch's own Ref. Schema definitions are changed by producing a merged factory,
not by mutating an existing declaration.

Schemas combine recursively without mutation. A Ref or `...` entry is a leaf;
a mapping entry is a container, including an empty mapping. Merging a leaf with
a container is always a structural error. Two leaves conflict under the default
`conflict="error"`; `conflict="replace"` selects the right leaf. Containers
merge recursively.

For a container's own empty-key Ref, an omitted declaration is distinct from an
explicit `Ref()`. One explicit declaration wins over an omitted default; two
explicit declarations conflict under `"error"` and select the right declaration
under `"replace"`. A schema is a declaration, so it has no deletion operation.

```python
extended = refs | {"output": {"score": ...}}
```

Schema keys must be Python identifiers. Names beginning with `_` and `merge`
are reserved for the factory API. Use an explicit `Ref("...")` only where paths
are intentionally dynamic or when working with `Ref` directly.

## Read and write

```python
from slyme.context import Context, RefFactory

refs = RefFactory({"user": {"name": ..., "age": ...}, "status": ...})

ctx = Context()
ctx.set(refs.user.name, "Ada")
ctx.update({refs.user.age: 36, refs.status: "active"})

assert ctx.get(refs.user.name) == "Ada"
assert ctx.exists(refs.user.age)
assert ctx.extract({"name": refs.user.name, "age": refs.user.age}) == {
    "name": "Ada",
    "age": 36,
}
```

Mutation methods such as `set`, `update`, `mutate`, `drop`, `delete`, and `clear` update the same Context and return `None`. Do not write `ctx = ctx.set(...)`.
Batch mutations validate every path before applying changes in place, so a
conflict causes no partial writes and surviving containers retain their object
identity.

An existing path keeps its current structural role. A leaf cannot implicitly
become a container, and a container—including an empty one—cannot be replaced
with a leaf. `clear()` keeps the target as an empty container. To redefine a
path's role, first remove that exact path with `delete()` or `drop()`; an atomic
`mutate()` may drop and recreate the same path in one operation.

## Structured operations

Context accepts Ref PyTrees for batch reads and writes. `extract` preserves the requested Python structure; `mutate` applies a function over selected paths. See the API reference for the exact accepted schemas.

## Isolation

Because Context data is mutable, concurrent branches must not silently share it when independent state is required. The caller must create an explicitly copied Context before branching. This makes the isolation boundary visible and leaves sequential execution free to share one Context efficiently.

`Context.clone()` creates an independent hierarchy of internal `ContextData` containers while preserving every stored leaf object. Mutating paths on the clone does not change the source Context, but mutating a shared leaf is visible through both. A `ContextView` can also be cloned into a standalone Context containing that subtree.

```python
branch = ctx.clone()
branch.set(refs.user.age, 37)
assert ctx.get(refs.user.age) == 36
```
