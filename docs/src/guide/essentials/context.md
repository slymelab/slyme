# Context

`Context` is Slyme's hierarchical mutable data store. Each instance has a local structured tree and an immutable ordered list of parents. Reads use the Context's C3-linearized hierarchy by default, while writes change only the current Context.

## Ref and Schema {#ref}

`Ref` is an immutable path value identifying a Context dependency. A Schema
records the paths available to an application, and `resolve()` returns their
canonical Refs:

```python
from slyme.context import Ref, Schema, ref

schema = Schema({"user": {"name": ref(str)}})
name = schema.resolve("user.name")
assert name.path == "user.name"
assert Ref("user.name") == name
```

Constructing a `Ref` does not alter the Schema. Context operations resolve its
path against `ctx.schema`, which remains the source of path roles and declared
value types.

For an application, describe its available paths with `Schema`:

```python
from slyme.context import Schema, ref

R = Schema(
    {
        "user": {
            "name": ref(str),
            "age": ref(),
        },
        "status": ref(),
    }
)

name = R.resolve("user.name")
```

`Schema()` may start empty or receive an initial declaration mapping. Slyme does
not export a global `R` object. Composition code may use `R` as a short local
name for its application Schema, while `ctx.schema` exposes the complete live
Schema used by that application.

Schema validates path declarations and rejects conflicting declared value
types. It does not validate the runtime type of stored values.

The path remains a stable semantic name independent of the physical Node graph.

`ref()` creates a path-free declaration with an optional value type.
Construction binds every declaration to its complete path. Other values are
invalid inside a declaration tree. The Schema itself remains mutable through
the monotonic `declare()` operation.

```python
from slyme.context import Schema, ref

R = Schema(
    {
        "input": {
            "": ref(),
            "name": ref(str),
            "age": ref(),
        },
        "output": {
            "message": ref(),
        },
    }
)

assert R.resolve("input").path == "input"
assert R.resolve("input.name").path == "input.name"
R.resolve("input.naem")  # raises KeyError and suggests "name"
```

Every named `ref()` entry declares a leaf. Every mapping entry declares a
container, including an empty mapping. An optional empty-key `ref()` explicitly
declares that container's own Ref instead of making it a leaf; Schema generates
the container Ref when it is omitted. Schema exposes declared paths through
`resolve()`, so application paths cannot collide with future Schema methods.

`declare()` extends the same Schema object recursively and atomically. An
equivalent declaration is idempotent. A different declaration for an existing
Ref, or a leaf/container structural conflict, raises without changing the
Schema. Declarations cannot be replaced or deleted.

```python
R.declare({"output": {"score": ref()}})
```

Schema keys must be non-empty strings without dots. Names such as `declare`,
Python keywords, and names beginning with `_` are valid because Schema does not
project paths as attributes. Unbounded dynamic names belong inside a value such
as `Compose`, rather than becoming Context paths.

## Read and write

```python
from slyme.context import Context, Schema, ref

R = Schema(
    {
        "user": {"name": ref(), "age": ref()},
        "status": ref(),
        "settings": ref(),
    }
)

ctx = Context({R.resolve("user.name"): "Ada"}, schema=R)
ctx.update({R.resolve("user.age"): 36, R.resolve("status"): "active"})

assert ctx.get(R.resolve("user.name")) == "Ada"
assert ctx.exists(R.resolve("user.age"))
assert ctx.get("user.name") == "Ada"
assert ctx.extract({"name": R.resolve("user.name"), "age": R.resolve("user.age")}) == {
    "name": "Ada",
    "age": 36,
}
```

The keyword-only `schema` argument installs that exact Schema object on a new Context root. Every Context path must be declared there, including reads with a default and `exists()` checks. The optional data mapping is then equivalent to calling `update()`. A child inherits the exact same `ctx.schema` object from its parents and cannot provide another one.

Applications can therefore build declarations before creating a Context, or start with a Context and declare paths through it. Both forms mutate the same Schema object, and existing forks see additions immediately:

```python
schema = Schema()
schema.declare({"core": {"ready": ref()}})
ctx = Context(schema=schema)

plugin_schema = Schema({"plugin": {"enabled": ref()}})
ctx.declare(plugin_schema)
child = ctx.fork()

child.set(plugin_schema.resolve("plugin.enabled"), True)
assert child.schema.resolve("plugin.enabled").path == "plugin.enabled"
```

A declaration fragment is not a plugin's private lookup space. A plugin that
uses paths declared elsewhere takes the complete live Schema from its Context
and may keep the conventional short alias for graph assembly:

```python
R = ctx.schema
```

The fragment remains useful for declaring and exporting the paths owned by the
plugin; `ctx.schema` is the application-wide union. Plugin teardown removes
values and Compose entries, not monotonic path declarations.

`set`, `update`, and the update side of `mutate` accept only paths declared as leaves. `keys` and `to_dict(ref)` accept only paths declared as containers. `get`, `exists`, `delete`, and the drop side of `mutate` accept either role. Batch mutations validate every path before applying changes, so a conflict produces no partial writes.

Schema fixes every declared path as exactly one leaf or container for the application. Context data cannot change that role: deleting a value does not turn its path into a container, and deleting a branch does not make its path writable as a leaf. Applications may extend the Schema with new paths, but an existing declaration cannot change role. Context stores values only at leaf paths and prunes empty data branches after mutation.

A user mapping remains a leaf. Context branches are created only by writing a deeper Ref:

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # one mapping-valued leaf
ctx.set(R.resolve("user.name"), "Ada")              # a structural branch and leaf
```

Every read operation accepts `local=True` when only the current Context should be inspected. The default is the effective view across the C3 hierarchy.

## Fork and lookup

`fork()` is shorthand for constructing an empty Context whose first parent is the receiver. Additional mixins become later direct parents:

```python
R = Schema({"settings": {"timeout": ref(), "mode": ref()}})
root = Context(schema=R)
root.set(R.resolve("settings.timeout"), 30)

feature = root.fork()
feature.set(R.resolve("settings.mode"), "fast")

mixin = root.fork()
agent = feature.fork(mixin)
assert agent.root is root
assert agent.mro == (agent, feature, mixin, root)
assert agent.to_dict() == {
    "settings": {"mode": "fast", "timeout": 30},
}
```

`mro` is the immutable linearization computed when a Context is constructed,
and `root` is its final entry. That root stores the Schema reference; every
descendant's `schema` property returns the same object through `root`. Separate
Context roots may deliberately reuse one Schema without sharing Context data.

All direct parents in a C3 hierarchy must descend from the same application root and therefore share one `schema` object. Parent changes remain visible until a child writes the same leaf, and sibling writes are isolated. Declared container branches merge recursively in C3 order; for each leaf, the first Context defining it supplies the visible value.

Deleting a local value removes any now-empty local path prefixes and reveals inherited values that they previously overrode.

## Reversible local bindings

`add()` installs a value only when the path is absent locally and returns an idempotent disposer for that exact installation:

```python
request_schema = Schema({"request": {"abort": ref()}})
agent.declare(request_schema)
remove = agent.add(request_schema.resolve("request.abort"), abort_controller)
try:
    run_request()
finally:
    remove()
```

An inherited value does not prevent a child from adding its own local value. A value installed by `add()` cannot be replaced with `set()` in the same Context; remove it first or write in a fork. If another value has already replaced or removed that exact entry, its disposer does nothing.

## Compose

`Compose` stores ordered values by Context identity and resolves the entries visible through that Context's C3 order. Store a Compose object in Context when other Nodes need to discover it through a Ref:

```python
from slyme.context import Compose, Context, Schema, ref

R = Schema({"tools": ref()})
root = Context(schema=R)
tools = Compose[str, tuple[str, ...]].collect()
root.add(R.resolve("tools"), tools)

remove_base = tools.add(root, "read")
agent = root.fork()
remove_agent = tools.add(agent, "shell", metadata={"plugin": "shell"})

assert agent.get(R.resolve("tools")) is tools
assert tools.resolve(agent) == ("shell", "read")
remove_agent()
remove_base()
```

`Compose.one()` selects the first visible value, `Compose.collect()` returns all visible values as a tuple, and `Compose.merge()` combines mappings while preserving the first visible value for each key. Passing a synchronous resolver to `Compose(...)` defines another result rule. Within one Context, `position="prepend"` places an entry before existing entries; the default is `"append"`.

`values(ctx, local=True)` inspects one Context's entries without resolving them, while `resolve(ctx, local=True)` applies the resolver to that same local set. `entries(ctx)` returns immutable records with each entry's id, Context, value, and metadata; omitting `ctx` inspects all currently live Contexts. Compose uses weak Context keys, so a key alone does not keep its Context alive. A stored value or metadata may still refer back to that Context, so the returned disposer remains the deterministic teardown mechanism.

A child can replace an inherited Compose object at its Ref with a new Compose object to create an independent set. Compose remains an ordinary Context leaf.

## Structured operations and projections

Context accepts Ref PyTrees for batch reads and writes. `extract` preserves the requested Python structure, and `update_tree` assigns values from a matching tree.

`to_dict()` projects Context paths into nested ordinary dictionaries for display or serialization. Across different Schemas, this projection cannot distinguish a mapping-valued leaf from equivalent nested Context paths. `flatten()` instead returns the exact visible `dict[Ref, Any]` leaf mapping:

```python
leaf_schema = Schema({"settings": ref()})
tree_schema = Schema({"settings": {"theme": ref()}})
mapping_leaf = Context(
    {leaf_schema.resolve("settings"): {"theme": "dark"}}, schema=leaf_schema
)
nested_path = Context(
    {tree_schema.resolve("settings.theme"): "dark"}, schema=tree_schema
)

assert mapping_leaf.to_dict() == nested_path.to_dict()
assert mapping_leaf.flatten() == {
    leaf_schema.resolve("settings"): {"theme": "dark"}
}
assert nested_path.flatten() == {tree_schema.resolve("settings.theme"): "dark"}
```

Both methods resolve the effective C3 view by default and accept `local=True`. A `ContextView` accepts relative string paths for subtree access, while resolved Ref objects remain absolute; its `flatten()` result therefore contains absolute Schema refs. Neither method copies leaf values. `Context(ctx.flatten(), schema=ctx.schema)` explicitly materializes a new application root with the same declarations and visible leaf objects; it has a new Context identity, so Compose entries registered for the source Context are not transferred.
