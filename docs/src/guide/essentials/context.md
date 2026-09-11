# Context

`Context` combines a declared mutable data view with lifetime ownership. Each Context has at most one parent and is bound to one immutable `Scope`. The application root keeps flat bindings keyed by active Schema leaf entries, with values indexed by Compose-local identities bound to Scopes. Reads follow the bound Scope's C3 order by default, while writes change the identity bound to that exact Scope.

A Context tree and its mutable Schema and Compose objects are single-thread-owned. Synchronous workflows use them on that thread; asynchronous workflows use them on one event loop. This is a usage requirement rather than a runtime thread-identity check. Worker threads and processes should receive ordinary values and return results for Context mutation on the owner thread.

## Ref and Schema {#ref}

`Ref` is an immutable path value identifying a Context dependency. A Schema
records the paths available to an application, and `resolve()` returns their
canonical Refs:

```python
from slyme.context import Ref, Schema

schema = Schema({"user": {"name": Schema.leaf(str)}})
name = schema.resolve("user.name")
assert name.path == "user.name"
assert Ref("user.name") == name
```

Constructing a `Ref` does not alter the Schema. Context operations resolve its
path against `ctx.schema`, which remains the source of path roles, declared
value types, and replacement policies. A Ref itself contains only its path and
cached path parts.

For an application, describe its available paths with `Schema`:

```python
from slyme.context import Schema

R = Schema(
    {
        "user": {
            "name": Schema.leaf(str),
            "age": Schema.leaf(),
        },
        "status": Schema.leaf(),
    }
)

name = R.resolve("user.name")
```

`Schema()` may start empty or receive an initial declaration mapping. Slyme does
not export a global `R` object. Composition code may use `R` as a short local
name for its application Schema, while `ctx.schema` exposes the complete live
Schema used by that application.

Schema validates path declarations and rejects conflicting declared value types
or replacement policies. It does not validate the runtime type of stored values.

The path remains a stable semantic name independent of the physical Node graph.

`Schema.leaf()` creates a path-free leaf configuration with an optional value
type and a `replaceable` policy. Construction binds every configuration to its
complete path. Other values are invalid inside a declaration tree. Schema
declarations remain mutable through the reversible `declare()` operation.

```python
from slyme.context import Schema

R = Schema(
    {
        "input": {
            "": Schema.container(),
            "name": Schema.leaf(str),
            "age": Schema.leaf(),
        },
        "output": {
            "message": Schema.leaf(),
        },
    }
)

assert R.resolve("input").path == "input"
assert R.resolve("input.name").path == "input.name"
R.resolve("input.naem")  # raises KeyError and suggests "name"
```

Every named `Schema.leaf()` entry declares a leaf. Every mapping entry declares
a container, including an empty mapping. An optional empty-key
`Schema.container()` supplies that container's configuration; Schema uses the
default container configuration when it is omitted. Schema exposes declared
paths through `resolve()`, so application paths cannot collide with future
Schema methods.

`declare()` extends the same Schema object recursively and atomically, then
returns an idempotent disposer for that exact declaration. Equivalent active
declarations coexist independently. A different configuration for an existing
Ref, or a leaf/container structural conflict, raises without changing the
Schema. After the final declaration of a path is disposed, that path and its
runtime bindings disappear; a later declaration may therefore reuse it with a
different definition without reviving old values.

```python
remove_score = R.declare({"output": {"score": Schema.leaf()}})
remove_score()
```

Schema keys must be non-empty strings without dots. Names such as `declare`,
Python keywords, and names beginning with `_` are valid because Schema does not
project paths as attributes. Unbounded dynamic names belong inside a value such
as `Compose`, rather than becoming Context paths.

## Read and write

```python
from slyme.context import Context, Schema, Scope

R = Schema(
    {
        "user": {"name": Schema.leaf(), "age": Schema.leaf()},
        "status": Schema.leaf(replaceable=False),
        "settings": Schema.leaf(),
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

The keyword-only `schema` argument installs that exact Schema object on a new Context root. Every Context path must be declared there, including reads with a default and `exists()` checks. The optional data mapping is then equivalent to calling `update()`. A child inherits the exact same `ctx.schema` and application data store from its single parent and cannot provide another Schema.

Applications can therefore build declarations before creating a Context, or start with a Context and declare paths through it. Both forms mutate the same Schema object, and existing forks see additions immediately:

```python
schema = Schema()
remove_core = schema.declare({"core": {"ready": Schema.leaf()}})
ctx = Context(schema=schema)

plugin_schema = Schema({"plugin": {"enabled": Schema.leaf()}})
remove_plugin = ctx.declare(plugin_schema)
child = ctx.fork()
assert child.parent is ctx
assert child.scope is ctx.scope

child.set(plugin_schema.resolve("plugin.enabled"), True)
assert child.schema.resolve("plugin.enabled").path == "plugin.enabled"

remove_plugin()
remove_core()
```

A declaration fragment is not a plugin's private lookup space. A plugin that
uses paths declared elsewhere takes the complete live Schema from its Context
and may keep the conventional short alias for graph assembly:

```python
R = ctx.schema
```

The fragment remains useful for declaring and exporting the paths owned by the
plugin; `ctx.schema` is the application-wide union. `Schema.declare()` returns a
caller-managed disposer. `ctx.declare()` additionally makes that declaration an
effect owned by `ctx`, while preserving the same exact early disposer.

Each declared leaf and ancestor container has a Retainer for its independent declaration owners. The disposer releases child paths before parents and removes a definition only after its last owner leaves. Cleanup continues after a failure and subsequent calls reproduce the first failure. Pending and successfully released disposers do not keep the Schema or its old definitions alive; failure tracebacks can retain cleanup state.

`set`, `update`, and the update side of `mutate` accept only paths declared as leaves. `keys` and `to_dict(ref)` accept only paths declared as containers. `get`, `exists`, `delete`, and the drop side of `mutate` accept either role. Batch mutations validate every path before applying changes, so a conflict produces no partial writes.

Schema fixes every actively declared path as exactly one leaf or container for the application. Context data cannot change that role: deleting a value does not turn its path into a container, and deleting a container's local values does not make its path writable as a leaf. Applications may extend the Schema with new paths, but a definition cannot change while any matching declaration remains active. Context stores only flat leaf bindings. Container access traverses Schema first and then reads the corresponding bindings.

A user mapping remains an atomic leaf. A deeper Ref belongs to a Schema-defined container rather than creating runtime structure:

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # one mapping-valued leaf
ctx.set(R.resolve("user.name"), "Ada")              # a structural branch and leaf
```

Every read operation accepts `local=True` to inspect only `ctx.scope`. The default is the effective view across `ctx.scope.mro`. Context CRUD never accepts a separate Scope argument; use a Context bound to the target Scope when data must be read or written elsewhere.

## Context lifetime and Scope lookup {#scope}

`fork()` creates an owned child Context whose `parent` is the receiver. It shares the receiver's Scope by default, so Contexts in the same application root and bound to that Scope observe the same local values. Pass an explicit child Scope when a distinct data layer is required:

```python
from slyme.context import Context, Schema

R = Schema({"settings": {"timeout": Schema.leaf(), "mode": Schema.leaf()}})
root = Context(schema=R)
root.set(R.resolve("settings.timeout"), 30)

plugin = root.fork()
assert plugin.scope is root.scope

feature_scope = root.scope.fork(name="feature")
mixin_scope = root.scope.fork(name="mixin")
agent_scope = Scope(name="agent", parents=(feature_scope, mixin_scope))

feature = root.fork(scope=feature_scope)
mixin = root.fork(scope=mixin_scope)
agent = feature.fork(scope=agent_scope)
feature.set(R.resolve("settings.mode"), "fast")
mixin.set(R.resolve("settings.timeout"), 45)

assert agent.parent is feature
assert agent.root is root
assert agent.scope.mro == (agent_scope, feature_scope, mixin_scope, root.scope)
assert agent.scope.find("mixin") is mixin_scope
assert agent.to_dict() == {
    "settings": {"mode": "fast", "timeout": 45},
}
```

Context parentage and Scope ancestry are independent. The parent determines lifetime ownership and the application root that holds Schema and data; the Scope determines lookup. `scope.fork()` always creates a single-parent child. Multiple parents require explicit `Scope(parents=(...))` construction and a consistent C3 linearization; those parents may come from otherwise unrelated Scope roots. Separate Context roots never share Context data, even when they use the same Scope object.

Deleting a local value normally reveals the next value in the Scope MRO.

`isolate()` creates an owned child with a child Scope and blocks selected leaf values from crossing into it. The private barrier participates in Scope C3 lookup, so a later Scope parent cannot bypass it. A value written in the isolated child appears normally, and deleting that value exposes the barrier again rather than an ancestor's value. Passing the same `identity=` to multiple calls makes those isolated children share the selected leaf storage while keeping it separate from the parent:

```python
service_schema = Schema({"service": Schema.leaf(replaceable=False)})
root = Context({"service": "default"}, schema=service_schema)
service = service_schema.resolve("service")
isolated = root.isolate(service)
assert not isolated.exists(service)

isolated.set(service, "ready")
assert isolated.get(service) == "ready"
isolated.delete(service)
assert not isolated.exists(service)
```

## Reversible local bindings

`add()` installs a value only when the path is absent at the bound Scope. The calling Context owns the installation and removes it during disposal; the returned idempotent disposer can remove it early:

```python
request_schema = Schema({"request": {"abort": Schema.leaf()}})
remove_schema = agent.declare(request_schema)
remove = agent.add(request_schema.resolve("request.abort"), abort_controller)
try:
    run_request()
finally:
    remove()
    remove_schema()
```

An inherited Scope value does not prevent adding a value at a more specific Scope. `add()` itself does not decide whether later replacement is allowed: `Schema.leaf(replaceable=False)` rejects `set()` while a normal value exists at the same Scope, whereas the default permits replacement. Deletion and child-Scope shadowing remain allowed. If another operation has already replaced or removed the exact entry created by `add()`, its disposer does nothing. Removing the final Schema declaration for a path also releases its hidden bindings; an owned stale `add()` disposer does not retain the removed value.

## Effects and disposal

`ctx.effect(setup)` runs synchronous setup immediately and owns the synchronous cleanup callable it returns; the method returns an exact synchronous early disposer. `ctx.async_effect(setup)` also runs setup synchronously, but owns an asynchronous cleanup callable and returns an early disposer that must be awaited. Effect setup cannot dispose its owner Context or an ancestor before the returned cleanup has been registered. As with any resource-acquisition callback, setup remains responsible for undoing partial acquisition if it raises before returning cleanup.

A parent strongly owns its child Contexts. Each Context processes its directly owned effects and child Contexts in last-in-first-out order, recursively. `dispose()` handles a wholly synchronous subtree. If any descendant owns asynchronous cleanup, it rejects the entire operation before teardown begins; use `await async_dispose()` to process both synchronous and asynchronous cleanup. Disposal continues after a cleanup failure, finishes releasing the subtree, and then raises the first failure. Idempotence means cleanup runs at most once; later calls to the same effect disposer or Context disposal method reproduce its terminal failure. A disposed Context rejects further Context data access, mutations, forks, effects, and registrations.

Effect cleanup cannot dispose its owning Context, an ancestor, or itself while it is running. These reentrant operations could invalidate resources still used by that cleanup or depend on their own completion, so Slyme rejects them with `RuntimeError`.

Disposing a Context removes it from the viewer sets for every Scope in `ctx.scope.mro`; it does not detach or destroy `ctx.scope`. A value installed by `set()` remains stored while another active Context in the same application root can view its Context-binding identity. A value installed by `add()` is additionally removed when its owning Context or exact disposer runs. Compose entries likewise remain until their exact disposers run. Data visibility never guarantees that an external resource inside a value is still open: its effect owner may have already closed it. Align resource ownership with every Context that may use it, and dispose child Contexts deterministically rather than relying on garbage collection.

Context bindings track the observed Scopes for each identity and remove its values when the last viewer leaves, without scanning unrelated Scope bindings. Reusing a retained Scope, directly or as an ancestor, restores its viewer registration and preserves its original identity binding; values already cleared are not restored.

Scope viewers and binding identities use Retainers whose callbacks own the membership sets. Each release removes its saved handle as well as its membership. The final viewer's release removes the Scope from its index and releases that Scope in each binding; the final bound Scope's release removes the identity and its data. Cleanup continues across bindings and Scopes after a failure, and Context disposal reproduces its first failure on subsequent calls. A Scope reacquired during value finalization keeps the data still visible to its new viewers.

## Compose

`Compose` stores ordered values under Compose-local identities and resolves them through Scope C3 order. An unbound Scope receives a private identity on its first write. `compose.bind(scope_a, scope_b, identity=key)` binds several Scopes once to a shared identity; repeated binding to the same identity is idempotent, while rebinding fails. Binding is structural and has no disposer. Store a Compose object in Context when Nodes need to discover it through a Ref, then use `Context.effect()` to own the disposer returned by `Compose.add()`:

```python
from slyme.context import Compose, Context, Schema

R = Schema({"tools": Schema.leaf(replaceable=False)})
root = Context(schema=R)
tools = Compose[str, tuple[str, ...]].collect()
root.add(R.resolve("tools"), tools)

root.effect(lambda: tools.add(root.scope, "read"))
agent = root.fork(scope=root.scope.fork(name="agent"))
remove_agent = agent.effect(
    lambda: agent.get(R.resolve("tools")).add(
        agent.scope, "shell", metadata={"plugin": "shell"}
    )
)

assert agent.get(R.resolve("tools")) is tools
assert tools.resolve(agent.scope) == ("shell", "read")
remove_agent()
agent.dispose()
root.dispose()
```

`ctx.effect(lambda: ctx.get(ref).add(target_scope, value))` looks up the Compose through `ctx.scope` and explicitly selects `target_scope` for the contribution. The Context owns cleanup even when the target Scope is elsewhere. The returned disposer removes the original entry, even if the Context leaf is later replaced with another Compose. Direct `compose.add(scope, value)` leaves disposer management to the caller.

`Compose.one()` selects the first visible value, `Compose.collect()` returns all visible values as a tuple, and `Compose.merge()` combines mappings while preserving the first visible value for each key. Passing a synchronous resolver to `Compose(...)` defines another result rule. Within one Scope, `position="prepend"` places an entry before existing entries; the default is `"append"`.

`values(scope, local=True)` inspects the entries under that Scope's identity without resolving them, while `resolve(scope, local=True)` applies the resolver to the same set. If several Scopes share an identity, this local set includes entries contributed through all of them. C3 lookup visits a shared identity only once. `entries(scope)` returns immutable records with each entry's id, contributing Scope, identity, value, and metadata; omitting the Scope inspects every current entry. Compose retains those entries until their exact disposer runs, so lifecycle-owned contributions are the preferred cleanup mechanism.

A Context bound to a child Scope can replace an inherited Compose object at its Ref with a new Compose object to create an independent set. Compose remains an ordinary Context leaf.

## Structured operations and projections

Context accepts Ref PyTrees for batch reads and writes. `extract` preserves the requested Python structure, and `update_tree` assigns values from a matching tree.

`keys()`, `ContextView`, and `to_dict()` traverse Schema structure before reading the flat leaf cells. Empty containers therefore have a stable declared role but do not appear in the effective data view. `to_dict()` projects visible Context leaves into nested ordinary dictionaries for display or serialization. Across different Schemas, this projection cannot distinguish a mapping-valued leaf from equivalent nested Context paths. `flatten()` instead returns the exact visible `dict[Ref, Any]` leaf mapping:

```python
leaf_schema = Schema({"settings": Schema.leaf()})
tree_schema = Schema({"settings": {"theme": Schema.leaf()}})
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

Both methods resolve the bound Scope's effective C3 view by default and accept `local=True`. A `ContextView` accepts relative string paths for subtree access, while resolved Ref objects remain absolute; its `flatten()` result therefore contains absolute Schema refs. Neither method copies leaf values. `Context(ctx.flatten(), schema=ctx.schema)` explicitly materializes a new application root with the same declarations and visible leaf objects. It receives a fresh Scope by default, so contributions targeting the source Scope are not visible; explicitly reusing that Scope shares Compose visibility but still does not share Context data between roots.
