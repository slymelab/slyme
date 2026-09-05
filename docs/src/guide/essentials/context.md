# Context

`Context` is Slyme's hierarchical mutable data store. Each instance has a local structured tree and an immutable ordered list of parents. Reads use the Context's C3-linearized hierarchy by default, while writes change only the current Context.

## Ref and Schema {#ref}

`Ref` identifies a semantic data path:

```python
from slyme.context import Ref

name = Ref("user.name")
assert name.path == "user.name"
```

For an application, describe its available paths with `Schema`:

```python
from slyme.context import ARG, Arg, Ref, Schema

R = Schema(
    {
        "user": {
            "name": Ref(metadata={ARG: Arg(type=str, required=True, help="User name")}),
            "age": ...,
        },
        "status": ...,
    }
)

name = R.user.name()
```

`Schema` requires a declaration mapping. Slyme does not export a global `R`
object. Composition code may use `R` as a short local name for its application
Schema, while `ctx.schema` exposes the complete live Schema owned by that
application.

Schema validates path declarations and their metadata. It does not validate the
runtime type of values stored at those paths.

The path remains a stable semantic name independent of the physical Node graph.

`...` is shorthand for an unbound `Ref()` declaration. Construction binds each
declaration to its complete path and freezes a private copy of the declaration
tree.

```python
from slyme.context import ARG, Arg, Ref, Schema

R = Schema(
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

assert R.input().path == "input"
assert R.input.name().path == "input.name"
R.input.naem  # raises AttributeError and suggests "name"
```

Every attribute access returns a `Schema` view, so it can be passed directly
anywhere a `RefLike` is accepted. Calling the view returns its `Ref`. A mapping
branch receives a default Ref when the empty key is omitted; the empty key
customizes that branch's own Ref.

Declaration trees combine recursively without mutation. A Ref or `...` entry is a leaf;
a mapping entry is a container, including an empty mapping. Merging a leaf with
a container is always a structural error. Two leaves conflict under the default
`conflict="error"`; `conflict="replace"` selects the right leaf. Containers
merge recursively.

For a container's own empty-key Ref, an omitted declaration is distinct from an
explicit `Ref()`. One explicit declaration wins over an omitted default; two
explicit declarations conflict under `"error"` and select the right declaration
under `"replace"`. Ref declarations have no deletion operation.

```python
extended = R | {"output": {"score": ...}}
```

Schema keys must be Python identifiers. Names beginning with `_`, plus `merge`
and `from_refs`, are reserved by the API. `Schema.from_refs(...)` can form a
declaration tree from existing bound `Ref` objects. Unbounded dynamic names belong inside
a value such as `Compose`, rather than becoming Context paths.

## Read and write

```python
from slyme.context import Context, Schema

R = Schema(
    {
        "user": {"name": ..., "age": ...},
        "status": ...,
        "settings": {"": ...},
    }
)

ctx = Context({R.user.name: "Ada"}, schema=R)
ctx.update({R.user.age: 36, R.status: "active"})

assert ctx.get(R.user.name) == "Ada"
assert ctx.exists(R.user.age)
assert ctx.extract({"name": R.user.name, "age": R.user.age}) == {
    "name": "Ada",
    "age": 36,
}
```

The keyword-only `schema` argument initializes an application root. Every Context path must be declared there, including reads with a default and `exists()` checks. The optional data mapping is then equivalent to calling `update()`. A child inherits the exact same `ctx.schema` object from its parents and cannot provide another one.

Plugins may add an immutable declaration tree through any Context in the application. The addition is immediately visible to existing forks, is idempotent for the same `Schema` object, and cannot be removed. Plugin teardown removes values and Compose entries, not path declarations:

```python
plugin_schema = Schema({"plugin": {"enabled": ...}})
child = ctx.fork()
ctx.declare(plugin_schema)

child.set(plugin_schema.plugin.enabled, True)
assert child.schema.plugin.enabled().path == "plugin.enabled"
```

A declaration fragment is not a plugin's private lookup space. A plugin that
uses paths declared elsewhere takes the complete live Schema from its Context
and may keep the conventional short alias for graph assembly:

```python
R = ctx.schema
```

The fragment remains useful for declaring and exporting the paths owned by the
plugin; `ctx.schema` is the application-wide union.

`set`, `update`, `mutate`, `drop`, and `delete` update the same Context and return `None`. Batch mutations validate every declared local path before applying changes, so a conflict produces no partial writes.

Within one Context, an existing path keeps its structural role. A leaf cannot implicitly become a container, and a non-empty container cannot become a leaf. Delete the exact local path before changing that role; one atomic `mutate()` may drop and recreate it. Containers are only indexes derived from their leaves, so every mutation removes containers left empty by the transaction.

A user mapping remains a leaf. Context branches are created only by writing a deeper Ref:

```python
ctx.set(R.settings, {"theme": "dark"})  # one mapping-valued leaf
ctx.set(R.user.name, "Ada")              # a structural branch and leaf
```

Every read operation accepts `local=True` when only the current Context should be inspected. The default is the effective view across the C3 hierarchy.

## Fork and lookup

`fork()` is shorthand for constructing an empty Context whose first parent is the receiver. Additional mixins become later direct parents:

```python
R = Schema({"settings": {"timeout": ..., "mode": ...}})
root = Context(schema=R)
root.set(R.settings.timeout, 30)

feature = root.fork()
feature.set(R.settings.mode, "fast")

mixin = root.fork()
agent = feature.fork(mixin)
assert agent.root is root
assert agent.mro == (agent, feature, mixin, root)
assert agent.to_dict() == {
    "settings": {"mode": "fast", "timeout": 30},
}
```

`mro` is the immutable linearization computed when a Context is constructed,
and `root` is its final entry. Only that root stores the application Schema;
every descendant's `schema` property returns the same object through `root`.

All direct parents in a C3 hierarchy must descend from the same application root and therefore share one `schema` object. Parent changes remain visible until a child writes a more specific value. Sibling writes are isolated. Branches at the same path merge in C3 order, but the first visible leaf blocks less-specific branches below it. For example, a child value at `a.b` hides a parent value at `a.b.c`. Conversely, a child may define `a.b.c` even when a parent stores a leaf at `a.b`, because the child's local branch has higher precedence.

Deleting a local value removes any now-empty local path prefixes and reveals inherited values that they previously overrode.

## Reversible local bindings

`add()` installs a value only when the path is absent locally and returns an idempotent disposer for that exact installation:

```python
request_schema = Schema({"request": {"abort": ...}})
agent.declare(request_schema)
remove = agent.add(request_schema.request.abort, abort_controller)
try:
    run_request()
finally:
    remove()
```

An inherited value does not prevent a child from adding its own local value. A value installed by `add()` cannot be replaced with `set()` in the same Context; remove it first or write in a fork. If another value has already replaced or removed that exact entry, its disposer does nothing.

## Compose

`Compose` stores ordered values by Context identity and resolves the entries visible through that Context's C3 order. Store a Compose object in Context when other Nodes need to discover it through a Ref:

```python
from slyme.context import Compose, Context, Schema

R = Schema({"tools": ...})
root = Context(schema=R)
tools = Compose[str, tuple[str, ...]].collect()
root.add(R.tools, tools)

remove_base = tools.add(root, "read")
agent = root.fork()
remove_agent = tools.add(agent, "shell", metadata={"plugin": "shell"})

assert agent.get(R.tools) is tools
assert tools.resolve(agent) == ("shell", "read")
remove_agent()
remove_base()
```

`Compose.one()` selects the first visible value, `Compose.collect()` returns all visible values as a tuple, and `Compose.merge()` combines mappings while preserving the first visible value for each key. Passing a synchronous resolver to `Compose(...)` defines another result rule. Within one Context, `position="prepend"` places an entry before existing entries; the default is `"append"`.

`values(ctx, local=True)` inspects one Context's entries without resolving them, while `resolve(ctx, local=True)` applies the resolver to that same local set. `entries(ctx)` returns immutable records with each entry's id, Context, value, and metadata; omitting `ctx` inspects all currently live Contexts. Compose uses weak Context keys, so a key alone does not keep its Context alive. A stored value or metadata may still refer back to that Context, so the returned disposer remains the deterministic teardown mechanism.

A child can replace an inherited Compose object at its Ref with a new Compose object to create an independent set. Compose remains an ordinary Context leaf.

## Structured operations and projections

Context accepts Ref PyTrees for batch reads and writes. `extract` preserves the requested Python structure, and `update_tree` assigns values from a matching tree.

`to_dict()` projects Context paths into nested ordinary dictionaries for display or serialization. This projection cannot distinguish a mapping-valued leaf from equivalent nested Context paths. `flatten()` instead returns the exact visible `dict[Ref, Any]` leaf mapping:

```python
R = Schema({"settings": {"": ..., "theme": ...}})
mapping_leaf = Context({R.settings: {"theme": "dark"}}, schema=R)
nested_path = Context({R.settings.theme: "dark"}, schema=R)

assert mapping_leaf.to_dict() == nested_path.to_dict()
assert mapping_leaf.flatten() == {R.settings(): {"theme": "dark"}}
assert nested_path.flatten() == {R.settings.theme(): "dark"}
```

Both methods resolve the effective C3 view by default and accept `local=True`. A `ContextView` returns paths relative to that view. Neither method copies leaf values. `Context(ctx.flatten(), schema=ctx.schema)` explicitly materializes a new application root with the same declarations and visible leaf objects; it has a new Context identity, so Compose entries registered for the source Context are not transferred.
