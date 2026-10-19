# Context

`Context` combines a declared mutable data view with lifetime ownership. Each Context has at most one parent and is bound to one immutable `Scope`. The application root keeps flat bindings keyed by active Schema leaf entries, with values indexed by leaf-local identities bound to Scopes. Reads follow the bound Scope's C3 order by default, while writes change the identity bound to that exact Scope.

Context privately holds three cooperating objects: `Schema` defines paths and metadata, `ContextStore` stores scoped values and tracks their viewers, and `Lifecycle` owns effects and disposal state. Context alone maintains the parent/child tree, exposed through `parent` and a read-only `children` tuple snapshot. Contexts in one application share Schema and Store, but each owns a distinct Lifecycle. Context exposes `declare()`, `resolve()`, `resolve_entry()`, and `entries`; callers do not need access to those private objects. `entries` includes declared containers and unset leaves, whereas `keys()` and `flatten()` describe visible values.

Context resolves paths and leaf/container roles through Schema before passing `RefEntry` objects to Store. Store handles scoped values and write modes; Context builds views, keys, and dictionaries from Store's visible items. ContextView adjusts relative paths without resolving them a second time. Binding and Store use `get/set/delete` for values; their getters return an internal missing sentinel when no value is visible. Existence checks and item traversal handle missing values without exceptions. Context applies user defaults or raises `ContextPathError`; defaults never hide undeclared paths.

Context coordinates component ownership; ContextView delegates access through Context. Store alone maintains viewer and reverse indexes, while each private binding owns its identities, values, and registration tokens without retaining Store state. Field withdrawal detaches Store indexes before clearing binding data, so value finalizers can redeclare the same path without the old cleanup removing its replacement. Schema registers and detaches its own Store consumers and only notifies them of withdrawn fields; Lifecycle retains its Context and calls its data release method after effect cleanup. Scope holds visibility information, not mutable data or cleanup ownership.

Normal Context access checks its Lifecycle before delegating. During disposal, reads remain allowed until that Context finishes releasing, but writes, declarations, effects, and child creation are forbidden. Internal withdrawal and Scope release use exact ownership records and remain allowed during cleanup. Shared Schema and Store do not adopt one caller's lifecycle state: other active Contexts may continue using them.

Each Context binding stores an immutable `ScopeBinding` per Scope. It selects an `Identity` and a local inheritance barrier; the Identity defines its own shared barrier. Mutable data records hold the current value, registration token, and retaining Scopes. Binding owns all operations on these records; writes do not expose them. Context storage is independent of Compose, which manages reversible registrations in application-defined layers.

Store enforces Schema write modes and the requirement that registration cannot overwrite a local value. Binding provides token-aware writes and deletions: a stored `None` token is unprotected, while a non-`None` token requires that exact token, including when the caller supplies `None`. Store registration creates a unique token and a `once()` disposer that calls Binding's `matches()` and `delete()`. `matches()` requires a local value and an exact token match, so an old disposer cannot delete a replacement, even an unprotected one.

Reads visit the complete Scope MRO, skip unbound Scopes, and select the first value or inheritance barrier. They do not create bindings or cache their results, so later writes and removals are visible on the next read.

A Context tree and its mutable Schema and Compose objects are single-thread-owned. Synchronous workflows use them on that thread; asynchronous workflows use them on one event loop. This is a usage requirement rather than a runtime thread-identity check. Worker threads and processes should receive ordinary values and return results for Context mutation on the owner thread.

## Ref and Schema {#ref}

Schema maintains a complete-path index and a structural tree referencing the same entries. Context leaf reads and writes use the path index; container traversal uses the tree. Private per-path setters and deleters update both indexes. Structural ancestor dicts can exist temporarily without declarations; installing or removing a container entry preserves its children, and deletion prunes empty dicts. These operations do not require parents before children or children before parents. Complete declarations still own every ancestor. Reusing a removed path creates a new definition without restoring its old values.

`Ref` is an immutable path value identifying a Context dependency. A Schema
records the paths available to an application, and `resolve()` returns their
canonical Refs:

```python
from slyme.context import Ref, Schema

schema = Schema({"user": {"name": Schema.leaf()}})
name = schema.resolve("user.name")
assert name.path == "user.name"
assert Ref("user.name") == name
```

Constructing a `Ref` does not alter the Schema. Context operations resolve its
path against its application's Schema, which determines path roles and write
modes. A Ref itself contains only its path and cached path parts.

`Ref[T]` preserves `T` through `resolve()`, `resolve_entry()`, and `Context.get()`, and constrains values passed to `set()` or `register()` in static checks. A `get()` default adds its type to the result. String paths remain dynamically typed; a container Ref describes a `ContextView`, not a dict. These annotations provide static typing without runtime type constraints.

`Ref("")` identifies the root container and has `parts == ()`; `schema.resolve("")` returns its canonical Ref. The root is permanently declared by Schema itself. Empty segments inside other paths, such as `".user"`, `"user."`, or `"user..name"`, remain invalid.

For an application, describe its available paths with `Schema`:

```python
from slyme.context import Schema

R = Schema(
    {
        "user": {
            "name": Schema.leaf(),
            "age": Schema.leaf(),
        },
        "status": Schema.leaf(),
    }
)

name = R.resolve("user.name")
```

`Schema()` starts with only the root container or receives an initial declaration dict or another Schema. Slyme does
not export a global `R` object. Composition code may use `R` as a short local
name for its application Schema. `ctx.resolve()` and `ctx.resolve_entry()` query the application's complete live declarations, including paths declared by other plugins.

Schema is a frozen dataclass with identity equality and weak-reference support. Its attribute bindings cannot be reassigned or deleted, but `declare()` and declaration disposers still mutate its contents. Initial declarations are an initialization-only input. Declaration dicts are validated and flattened into path/config records without changing the input; importing another Schema copies its definitions in `entries` registration order without sharing declaration owners. Every Schema has its own declaration indexes and registered application stores.

A Schema strongly retains each application Store; Store viewer registrations retain its active Contexts. Child Contexts share their root's Store. Root disposal detaches the Store after releasing viewers, even if cleanup fails; failed root construction also detaches it. Dropping the last external Context reference does not release an application while its Schema remains reachable; explicitly call `dispose()` and await any asynchronous cleanup. Withdrawing a field removes its bindings from all registered stores without disposing those Contexts. The order of field cleanup between stores is unspecified.

Schema validates path roles, write modes and metadata compatibility. It does not declare or validate runtime value types. Use `Ref[T]` for static read/write types; type parameters on independently constructed Refs do not check each other at runtime.

The path remains a stable semantic name independent of the physical Node graph.

`Schema.leaf()` creates a path-free leaf configuration with auxiliary metadata
and a write `mode`. The default `"assign"` permits `set()` and `delete()`;
`"register"` permits only `register()` and its disposer. Construction binds every configuration to its
complete path. Other values are invalid inside a declaration tree. Schema
declarations remain mutable through the reversible `declare()` operation.

```python
from slyme.context import Schema

R = Schema(
    {
        "input": {
            "": Schema.container(),
            "name": Schema.leaf(),
            "age": Schema.leaf(),
        },
        "output": {
            "message": Schema.leaf(),
        },
    }
)

assert R.resolve("input").path == "input"
assert R.resolve("input.name").path == "input.name"
R.resolve("input.naem")  # raises KeyError
```

Every named `Schema.leaf()` entry declares a leaf. Every dict entry declares
a container, including an empty dict. An optional empty-key
`Schema.container()` supplies that container's configuration; Schema uses the
default container configuration when it is omitted. Schema exposes declared
paths through `resolve()`, so application paths cannot collide with future
Schema methods.

The root dict follows the same rule: `Schema({"": Schema.container()})` explicitly supplies its container configuration. Declaration dicts must form a finite tree; cycles are unsupported and fail through Python's recursive execution. After normalizing the declaration structure, registration validates and updates paths individually, preserving existing entries. A failure reverses that declaration's successful registrations, removing new paths and restoring previous owners and effective configs. Registration is not isolated from synchronous callbacks; metadata comparisons must depend only on their inputs, not inspect or modify Schema. Rollback does not compensate external side effects.

`declare()` extends the same Schema object with rollback on failure, then
returns an idempotent disposer for that exact declaration. Compatible active
declarations coexist independently. Incompatible behavior settings or metadata
for an existing Ref, or a leaf/container structural conflict, raise after undoing the
current declaration's registrations. After the final declaration of a non-root path is disposed, that path and its
runtime bindings disappear; a later declaration may therefore reuse it with a
different definition without reviving old values.

```python
remove_score = R.declare({"output": {"score": Schema.leaf()}})
remove_score()
```

Path segment names must be non-empty strings without dots; the empty dict key is reserved for the containing entry's configuration. Names such as `declare`,
Python keywords, and names beginning with `_` are valid because Schema does not
project paths as attributes. Unbounded dynamic names belong inside a value such
as `Compose`, rather than becoming Context paths.

### Metadata

`Schema.resolve_entry(path)` returns the current `RefEntry`; `Schema.entries` returns a tuple of registered entries, snapshotting membership only. Each entry exposes `ref`, current `config`, cached read-only `metadata`, and `alive`. `RefLeafConfig` and `RefContainerConfig` describe only roles and write modes, with fixed `equals` methods; `RefConfig` is their union type, not a base class. `Schema.leaf()` and `Schema.container()` return a `RefDeclaration` carrying config and metadata separately. These types are exported by `slyme.context`.

```python
entry = schema.resolve_entry("user.name")
metadata = entry.metadata
for entry in schema.entries:
    print(entry.ref.path, entry.metadata)
```

`entry.config` returns the earliest active declaration's original configuration without merging. `entry.metadata` lazily builds and caches a read-only snapshot of current representatives. Ownership-only changes reuse the snapshot; visible key or representative identity changes invalidate it. Previously obtained snapshots remain unchanged. After the final declaration is withdrawn, `alive` is false and config or metadata access raises `LookupError`; previously obtained metadata snapshots remain readable and unchanged. Resolving a missing path raises `KeyError`; redeclaring it creates a new entry.

Both `Schema.leaf(metadata=...)` and `Schema.container(metadata=...)` accept a mapping from string keys to `Metadata` instances. Use application-qualified keys such as `cli.option` or `docs.description`. Metadata does not change a path's leaf/container role or write mode.

`Metadata` is a frozen dataclass with an explicit `equals(other) -> bool` method. The default compares object identity. Schema accepts identical objects without calling `equals`; for distinct objects at the same key, it calls the current representative's `equals(incoming)` once. Python `__eq__` and dataclass equality are not used. Override `equals` to accept equivalent content:

```python
from dataclasses import dataclass
from slyme.context import Metadata, Schema


@dataclass(frozen=True)
class Description(Metadata):
    text: str

    def equals(self, other: Metadata) -> bool:
        return type(other) is Description and self.text == other.text


first = Description("Prompt text")
second = Description("Prompt text")
schema = Schema()
remove_first = schema.declare(
    {"prompt": Schema.leaf(metadata={"docs.description": first})}
)
remove_second = schema.declare(
    {"prompt": Schema.leaf(metadata={"docs.description": second})}
)
entry = schema.resolve_entry("prompt")
snapshot = entry.metadata
assert snapshot["docs.description"] is first
remove_first()
assert entry.metadata["docs.description"] is second
assert snapshot["docs.description"] is first
remove_second()
```

Comparisons must be synchronous, pure and stable, and define an equivalence relation: reflexive, symmetric and transitive. Equivalent objects must be interchangeable for consumers. Compare only immutable input data, without accessing Schema or external state. Schema trusts this contract; it does not perform reverse comparisons or compare every pair. False means a declaration conflict; a raised exception also fails registration. An entry validates and records metadata in one pass. Failure removes partial contributions and may invalidate the snapshot cache; previously obtained snapshots remain unchanged, and a later path failure withdraws that declaration's earlier registrations.

Different metadata keys coexist. Each key retains the original objects supplied by its active declarations. The earliest active declaration supplies the representative; withdrawing it selects the next original without comparison or cloning. Repeated registrations of the same object still have independent owners. Withdrawal, rollback and config reads never call `equals`. Registration and withdrawal update ownership only for the incoming declaration's keys and invalidate the metadata cache when necessary. For one entry with k contributed keys, bookkeeping costs O(k), plus custom `equals` costs on registration; an invalidated snapshot costs O(m) to rebuild for m visible keys, while cached reads cost O(1). A whole Schema operation also involves path traversal and final-withdrawal data cleanup. Imports copy the current metadata mapping with independent ownership, so later source changes do not propagate. No stable representative identity is promised across withdrawals of equivalent distinct objects. `RefEntry` and the metadata key identify the live marker position.

`RefDeclaration` copies and freezes the input metadata mapping; items retain their identities. Entries expose independently cached read-only snapshots. Frozen dataclasses prevent attribute reassignment, not mutation of nested lists or dicts; implementations must keep their payloads immutable. Python and JS use the same string keys, declaration ownership, identity shortcut and explicit `equals` protocol. Leaf/container configurations use fixed `equals` rules with no custom merge extension; business annotations belong in Metadata.

## Read and write

```python
from slyme.context import Context, Schema, Scope

R = Schema(
    {
        "user": {"name": Schema.leaf(), "age": Schema.leaf()},
        "status": Schema.leaf(),
        "settings": Schema.leaf(),
    }
)

ctx = Context()
ctx.declare(R)
ctx.update({R.resolve("user.name"): "Ada"})
ctx.update({R.resolve("user.age"): 36, R.resolve("status"): "active"})

assert ctx.get(R.resolve("user.name")) == "Ada"
assert ctx.exists(R.resolve("user.age"))
assert ctx.get("user.name") == "Ada"
assert ctx.extract({"name": R.resolve("user.name"), "age": R.resolve("user.age")}) == {
    "name": "Ada",
    "age": 36,
}
```

`Context(*, parent=None, scope=None, dispose_mode="sequential")` establishes ownership and visibility. A root creates its Schema and Store and installs independent framework configuration under `$`; a child shares its parent's Schema and Store without reinstalling defaults. `root` is a fixed field, pointing to itself for a root and to the same application root for every child. Context attribute bindings cannot be reassigned, while declared data remains mutable. Call `declare()` to add paths, then `update()` or `set()` to assign values. Every data access requires a declared path, including reads with a default and `exists()` checks. `update()` accepts only `assign` fields.

Applications can build a Schema before creating a Context and import it with `ctx.declare(schema)`, or declare a dict directly. Import copies the current definitions into the application's Schema with independent declaration ownership; later source-Schema additions or withdrawals do not propagate. Separate roots have independent schemas even when importing the same source. Existing forks see changes declared through any Context in their application immediately:

```python
schema = Schema({"core": {"ready": Schema.leaf()}})
ctx = Context()
remove_core = ctx.declare(schema)

plugin_schema = Schema({"plugin": {"enabled": Schema.leaf()}})
remove_plugin = ctx.declare(plugin_schema)
child = ctx.fork()
assert child.parent is ctx
assert child.scope is ctx.scope

child.set(plugin_schema.resolve("plugin.enabled"), True)
assert child.resolve("plugin.enabled").path == "plugin.enabled"
```

A declaration fragment is not a plugin's private lookup space. A plugin that uses paths declared elsewhere resolves them through Context:

```python
ready = ctx.resolve("core.ready")
metadata = ctx.resolve_entry("core.ready").metadata

remove_plugin()
remove_core()
```

The fragment remains useful for declaring and exporting the paths owned by the
plugin; `ctx.entries` lists the application-wide union. `Schema.declare()` returns a
caller-managed disposer. `ctx.declare()` registers definitions in the application's Schema and makes their cleanup an effect owned by `ctx`, returning an exact early disposer. Failure of a subsequent `declare()` or `update()` leaves the already-created Context alive; the caller decides whether to retry or dispose it.

The `declaration` argument of `Schema(...)`, `Schema.declare(...)`, and `Context.declare(...)` describes one registration tree, which may contain multiple paths. Each declared leaf and ancestor container tracks declaration IDs and per-key metadata contributions. Role and write-mode conflicts are checked before metadata equivalence. Ownership changes preserve cached configs unless visible metadata changes; the next config read snapshots the current representatives without comparing them. Framework withdrawal, rollback and Context disposal do not read config or call `equals`. Each declare call returns one disposer, which reverses registration order and removes a definition only after its last owner leaves. Cleanup continues across entries after a failure and raises all failures as an exception group; subsequent calls reproduce the same group without repeating cleanup. A pending disposer retains the Schema and its entries. Its first call releases those references even on failure, although failure tracebacks can retain cleanup state.

`set` and `update` accept only paths declared as leaves. `keys` and `to_dict(ref)` accept only paths declared as containers. `get`, `exists`, `delete`, and `drop` accept either role. Deleting a container removes its local descendant values, not its Schema declarations or inherited values.

### Framework configuration

`context/default.py` installs three register-mode Composes at the root: `$.tree.data.rules`, `$.tree.node.rules`, and `$.eval.handlers`. Access them through `DATA_TREE_REF`, `NODE_TREE_REF`, and `EVALUATORS_REF`, exported by `slyme.context`. Root lifetime owns these values and their default contributions; there is no mutable process-global registry.

These paths follow ordinary Context/Scope rules. Child Scopes inherit them; unrelated Scopes must explicitly acquire configuration, with no implicit fallback to root. Contribute rules through Compose, or call `ctx.derive(bindings={ref: ScopeBinding(blocked=True)})` and register an independent Compose in the returned child. See [Tree rules](../slyme-in-depth/tree-in-slyme.md).

### Root container

While Context is readable, `ctx.get("")` returns its live root `ContextView` and `ctx.exists("")` is true, including when no values are visible. `ctx.keys("")` and `ctx.to_dict("")` are equivalent to their argument-free forms. A fresh root includes `$` in `keys()`, `to_dict()`, and `flatten()`; these methods do not hide framework fields. Non-root containers without visible leaves still count as absent.

```python
from slyme.context import Context, Ref, Schema

root_ctx = Context()
root_ctx.declare(Schema({"value": Schema.leaf()}))
root_ctx.update({"value": 1})
child_ctx = root_ctx.fork(scope=root_ctx.scope.fork())
root_view = child_ctx.get(Ref(""))
child_ctx.set("value", 2)
assert root_view.get("value") == 2

child_ctx.delete("value")
assert root_view.to_dict(local=True) == {}
assert root_view.get("value") == 1
assert root_ctx.get("value") == 1
root_ctx.dispose()
```

`delete("")` and `drop([""])` reject the register-mode framework descendants before deleting any values. Delete an assign-only business subtree instead. Container deletion preserves declarations, inherited values, isolation barriers, and effects. Root assignment through `set`, `register`, or `update` is rejected because the root is a container.

### Bulk updates

`update` validates every path and write mode before writing; `drop` consumes and validates all input paths and collects their descendant leaves before deleting. Preflight failures leave bindings unchanged. Errors or reentrant side effects during application of the changes do not trigger rollback. Deleting and then updating are separate calls, not a combined transaction.

Schema fixes every actively declared path as exactly one leaf or container for the application. Context data cannot change that role: deleting a value does not turn its path into a container, and deleting a container's local values does not make its path writable as a leaf. Applications may extend the Schema with new paths, but a definition cannot change while any matching declaration remains active. Context stores only flat leaf bindings. Container access traverses Schema first and then reads the corresponding bindings.

A user mapping remains an atomic leaf. A deeper Ref belongs to a Schema-defined container rather than creating runtime structure:

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # one mapping-valued leaf
ctx.set(R.resolve("user.name"), "Ada")  # a structural branch and leaf
```

Every read operation accepts `local=True` to inspect only `ctx.scope`. The default is the effective view across `ctx.scope.mro`. Context CRUD never accepts a separate Scope argument; use a Context bound to the target Scope when data must be read or written elsewhere.

## Context lifetime and Scope lookup {#scope}

`Context.fork()` and `Context.derive()` create base `Context` instances; `Scope.fork()` creates a base `Scope`. These methods do not propagate the receiver's Python subclass. Use facets and composition for business extensions.

`fork()` creates an owned child Context whose `parent` is the receiver. It shares the receiver's Scope by default, so Contexts in the same application root and bound to that Scope observe the same local values. Pass an explicit child Scope when a distinct data layer is required:

```python
from slyme.context import Context, Schema

R = Schema({"settings": {"timeout": Schema.leaf(), "mode": Schema.leaf()}})
root = Context()
root.declare(R)
root.set(R.resolve("settings.timeout"), 30)

plugin = root.fork()
assert plugin.scope is root.scope

feature_scope = root.scope.fork(label="feature")
mixin_scope = root.scope.fork(label="mixin")
agent_scope = Scope(label="agent", parents=(feature_scope, mixin_scope))

feature = root.fork(scope=feature_scope)
mixin = root.fork(scope=mixin_scope)
agent = feature.fork(scope=agent_scope)
feature.set(R.resolve("settings.mode"), "fast")
mixin.set(R.resolve("settings.timeout"), 45)

assert agent.parent is feature
assert agent.root is root
assert agent.scope.mro == (agent_scope, feature_scope, mixin_scope, root.scope)
assert agent.scope.find("mixin") is mixin_scope
assert agent.to_dict("settings") == {"mode": "fast", "timeout": 45}
```

Context parentage and Scope ancestry are independent. The parent determines lifetime ownership and the application root that holds Schema and data; the Scope determines lookup. `Scope(*, label=None, parents=())` accepts one Scope or a tuple, normalizing the stored `parents` attribute to a tuple before C3 linearization. Parents may come from otherwise unrelated Scope roots. `scope.fork()` creates a single-parent child. Separate Context roots never share Context data, even when they use the same Scope object.

A single-parent Scope prepends itself to its parent's existing MRO, even when the parent itself uses multiple inheritance. Its construction is linear in the length of that MRO.

`scope.find(label)` returns the first equal label in C3 order and raises `LookupError` when none matches. Pass `scope.find(label, default)` to return an explicit Scope or `None` instead; passing that `None` to `ctx.fork(scope=...)` shares the current Scope. `scope.find_all(label)` returns all matches in C3 order, or an empty tuple. Labels need not be hashable or unique and do not determine Scope identity.

Deleting a local value normally reveals the next value in the Scope MRO.

`fork()` creates an owned child Context sharing the current Scope; `fork(scope=...)` selects an existing Scope. `Identity(label=None, blocked=False)` is an immutable storage identity: sharing requires the same object, not the same label. Data remains local to each field in one Context store, or to one Compose. `blocked=True` fixes a fallback barrier for every Scope using that Identity.

`ScopeBinding(identity=Identity(), blocked=False)` describes a storage identity and Scope-local barrier. Each instance creates a private Identity by default; both blocking flags default to False. Public APIs install explicit configurations only on new Scopes; existing bindings cannot be changed. An unbound Scope's first write fixes a private, unblocked binding. A present value remains visible; otherwise either barrier stops C3 lookup, including later parents. Context and Compose expose no in-place binding or blocking changes.

Payload cleanup does not alter configuration. Reusing a saved Scope preserves its field binding, including the local barrier; reusing an Identity preserves its identity-level barrier. Cleared values are never restored. Withdrawing the final Schema declaration removes that field's bindings, but cannot change an externally retained Identity. These rules control lookup, not authorization between plugins.

`ctx.derive(*, label=None, parents=None, bindings=None, dispose_mode="sequential")` creates an owned child Context and one new Scope for all targets. `parents=None` inherits `ctx.scope`; an explicit Scope or tuple selects the new Scope's direct parents, and `()` creates an independent Scope. Lifecycle ownership remains with `ctx`. Path or Ref keys configure declared leaves; Compose object keys configure contributions without replacing Context values. Each value is a ScopeBinding or an Identity. A direct Identity is shorthand for `ScopeBinding(identity=identity)`; individual binding values cannot be `None`. `ScopeBinding()` creates a private Identity without blocking; `ScopeBinding(identity=shared)` selects shared storage. Enable either barrier explicitly with `blocked=True`. Unselected targets inherit through the selected parents. Omitted, None, or empty bindings create a new Scope without explicit bindings. Calling `ctx.derive()` is equivalent to `ctx.fork(scope=ctx.scope.fork())`. Identity sharing does not make the new Scope's barrier apply to unrelated Scopes:

```python
from slyme.context import Identity

service_schema = Schema({"service": Schema.leaf()})
root = Context()
root.declare(service_schema)
root.update({"service": "default"})
service = service_schema.resolve("service")
isolated = root.derive(bindings={service: ScopeBinding(blocked=True)})
assert not isolated.exists(service)

isolated.set(service, "ready")
assert isolated.get(service) == "ready"
isolated.delete(service)
assert not isolated.exists(service)
assert root.get(service) == "default"
isolated.dispose()

shared = Identity("shared")
left = root.derive(bindings={service: ScopeBinding(identity=shared)})
right = root.derive(bindings={service: ScopeBinding(identity=shared)})
left.set(service, "shared value")
assert right.get(service) == "shared value"
root.dispose()
```

For plugin reloads, use a plugin-owned child Context with a fresh Scope derived from a clean shared base, and own declarations, registrations, Compose contributions, and cleanup through that Context. After successful disposal and task shutdown, another plugin can start without those contributions. Reusing a Scope or Identity explicitly retains its configuration and any still-owned shared data; new Scopes still inherit their ancestors unless blocked. Disposal does not undo arbitrary writes to shared assign-mode values, mutations of stored objects, or file/network effects. Immutable configuration and exact cleanup therefore support clean reloads, but are not a general transaction rollback.

## Reversible local bindings

Declare a field with `mode="register"` to use `register()`. It installs a value only when the bound identity has no value. The calling Context owns the installation and removes it during disposal; the returned idempotent disposer can remove it early:

```python
request_schema = Schema({"request": {"abort": Schema.leaf(mode="register")}})
remove_schema = agent.declare(request_schema)
remove = agent.register(request_schema.resolve("request.abort"), abort_controller)
try:
    run_request()
finally:
    remove()
    remove_schema()
```

An inherited value can be shadowed by a registration in a child Scope. Contexts sharing the same local identity cannot register competing values: remove the existing registration before installing another. `set()`, `update()`, `update_tree()`, `delete()`, and `drop()` reject `register` fields, including fields with no local value. Container deletion rejects the entire operation if any descendant uses `register` mode. `assign` fields reject `register()` instead. These modes govern the binding, not the mutability of the stored object.

Context strongly owns its binding table. A registration disposer captures its Binding, Scope, and installation token, not the Store or an internal value record. Its closure drops the Binding reference in `finally` when called, even if cleanup fails; an exception traceback may separately retain method frames. Until called, it retains the Binding and its remaining identity data. Final Schema withdrawal clears the entire Binding, and Scope release clears identity data when its final viewer leaves, regardless of retained disposers.

## Registered capabilities {#capabilities}

Functions, Nodes and service objects can be stored in register-mode fields. Retrieve them through an explicit Ref and call them with their ordinary arguments. Lookup follows the accessing Context's Scope; values retain their identity and are not automatically bound or executed. A sequential child Context can own both the declarations and registrations, withdrawing values before declarations when disposed.

```python
from collections.abc import Callable

from slyme.context import Context, Ref, Schema

root = Context()
owner = root.fork()
owner.declare({"services": {"greet": Schema.leaf(mode="register")}})
greet: Ref[Callable[[str], str]] = owner.resolve("services.greet")
owner.register(greet, lambda name: f"Hello, {name}")
child = root.derive()
assert child.get(greet)("Slyme") == "Hello, Slyme"
owner.dispose()
root.dispose()
```

Use ordinary functions for local operations and Nodes when parameter binding, Auto or Wrapper composition is needed. To call a registered Node, retrieve it with `task = child.get(task_ref)`, then call `task(child, **overrides)`. Context fields are accessed through `get()`; they do not become object attributes.

## Effects and disposal

`Context.state` and `Lifecycle.state` are read-only strings: `active`, `dispose_pending` (waiting for cleanup after an ancestor begins disposal), `disposing`, or `disposed`. Reading state never starts cleanup and remains available after disposal.

`ctx.effect(setup)` owns one setup and its cleanup. A synchronous setup runs immediately and returns an early disposer. If setup returns an awaitable, `effect()` returns an awaitable resolving to that disposer; use `await await_result(ctx.effect(setup))` when either form is possible. Async setup is owned before it starts: owner disposal waits for it and then runs its cleanup, even if its caller never awaited registration. Await setup before using the resource it acquires. Setup remains responsible for undoing partial acquisition if it raises before returning cleanup.

Each asynchronous call returns a single-use coroutine. Repeated calls to `ctx.dispose()` or an early disposer wait for the same underlying operation without repeating cleanup. To await one returned coroutine multiple times, explicitly share it with a Task or `SharedAwaitable`; setup registration follows the same single-use rule.

A parent strongly owns its child Contexts. Each Context calls directly owned disposers in reverse registration order. With `dispose_mode="sequential"`, it waits for each before calling the next; `"batch"` calls all before joining their asynchronous results. Construction, `fork()`, and `derive()` default independently to sequential mode. Nested groups and shared waits are described in [Lifecycle](./lifecycle.md#cleanup-groups). `dispose()` runs synchronous cleanup immediately and returns `None` when complete, or an awaitable for the unfinished asynchronous cleanup. Use `await await_result(ctx.dispose())` for either case, importing `await_result` from `slyme.execution`. An async continuation is not scheduled until awaited; merely discarding it leaves disposal unfinished. Once scheduled, its task survives waiter cancellation. Early effect disposers follow the same completion protocol. Cleanup continues after failure, then raises an exception group containing only failures in reverse registration order, regardless of completion order; repeated calls share the completion and reproduce its terminal failure without repeating cleanup. A disposed Context rejects further data and lifecycle operations.

Before running any cleanup, `dispose()` synchronously forbids mutations throughout its owned Context subtree, including new effects and child Contexts. Mutation checks inspect only the receiving Context's state, independent of lifetime depth. Each Context remains readable until its own release. A child awaiting its turn may still be disposed early; cleanup already in progress keeps its shared completion. Contexts outside the ownership subtree remain mutable even when they share or inherit its Scopes. This does not cancel running Node tasks or freeze the objects stored in Context values.

Setup and cleanup must not reenter disposal of themselves, their owning Context, or an ancestor, or wait for disposal that includes themselves. These calls are unsupported; Lifecycle does not detect such reentry or wait cycles. Signal an external coordinator to dispose instead. An independent task may dispose the owner while setup or cleanup is pending: disposal waits for that operation, which must not wait back on disposal. During disposal, pending setup can finish acquisition and return cleanup, but cannot make new Context mutations or registrations.

Disposing a Context removes it from the viewer sets for every Scope in `ctx.scope.mro`; it does not detach or destroy `ctx.scope`. A value installed by `set()` remains stored while another active Context in the same application root can view its Context-binding identity. A value installed by `register()` is additionally removed when its owning Context or exact disposer runs. Compose entries likewise remain until their exact disposers run. Data visibility never guarantees that an external resource inside a value is still open: its effect owner may have already closed it. Align resource ownership with every Context that may use it, and dispose child Contexts deterministically rather than relying on garbage collection.

Context bindings track the observed Scopes for each identity and remove its values when the last viewer leaves, without scanning unrelated Scope bindings. Reusing a retained Scope, directly or as an ancestor, restores its viewer registration and preserves its original identity binding; values already cleared are not restored.

Each application keeps weak-key usage records containing Context viewers and participating Schema leaf entries. Writes and isolation index entries; ordinary inherited reads do not. Container deletion validates descendant modes, then intersects those entries with this index before accessing bindings. Deleting a value preserves its index entry, identity ownership, and isolation barrier. The last viewer's release removes identity ownership but retains the Scope's binding history. Final Schema withdrawal removes the exact binding from every application using that Schema and prunes its active binding indexes.

Reusing a released Scope, including as an ancestor, visits only its recorded entries to restore surviving identity ownership, without scanning Schema or enumerating weak keys. Restoration and release prune obsolete history entries; redeclaring the same path creates a different entry and does not inherit an old binding. An externally retained inactive Scope can retain withdrawn entries until it is reused or collected. Cleared values are never restored. Data bindings have no cleanup order guarantee; dependencies requiring ordered cleanup belong in effects.

Scope viewers and binding identities store their owners directly in sets. Context disposal removes its viewer registrations and releases each unobserved Scope in the bindings; the final bound Scope's release removes the identity and its data. These internal registrations do not allocate per-member disposal callbacks. Cleanup continues across bindings and Scopes after a failure, and Context disposal reproduces its terminal failure on subsequent calls. If Scope release also fails after owned cleanup failures, its first error is retained as the aggregate's cause. A Scope reacquired during value finalization keeps the data still visible to its new viewers.

## Compose

`Compose` owns registration tokens, exact withdrawal, and Scope visibility. `Compose[L, R]` describes the application-defined layer and default query result types. Its constructor accepts `factory` and an optional `query`; each active Identity receives a fresh layer from the factory.

The public `ComposeLayer` protocol requires one synchronous method: `register(token, /, *args, **kwargs) -> Callable[[], None]`. Its returned disposer removes exactly that registration's business data. Inheriting the protocol is optional. `compose.register(scope, /, *args, **kwargs)` selects the Identity and injects a unique token, forwarding all business arguments unchanged. A layer receives no implicit Compose, Scope, or Identity. Its registration signature determines the accepted arguments, including keyword options such as prepend/append or metadata. Capture fixed dependencies in the factory; perform cross-layer validation in an outer operation that explicitly receives Compose and Scope.

```python
from slyme.context import Compose, Context, Schema


class ValueLayer(dict):
    def register(self, token, /, value):
        self[token] = value

        def dispose():
            del self[token]

        return dispose


root = Context()
root.declare({"tools": Schema.leaf(mode="register")})
tools = Compose(
    factory=ValueLayer,
    query=lambda layers: tuple(value for layer in layers for value in layer.values()),
)
root.register("tools", tools)
root.effect(lambda: tools.register(root.scope, "read"))

agent = root.derive(label="agent")
remove_agent = agent.effect(
    lambda: agent.get("tools").register(agent.scope, value="shell")
)
assert tools.resolve(agent.scope) == ("shell", "read")
assert tools.resolve(agent.scope, local=True) == ("shell",)
assert (
    tools.resolve(agent.scope, lambda layers: sum(len(layer) for layer in layers)) == 2
)

remove_agent()
agent.dispose()
root.dispose()
```

`resolve(scope)` applies the constructor's query; `resolve(scope, query)` overrides it for that call and may return another type. With no default, callers must supply a query; otherwise resolve raises ValueError. Queries combine visible layers; each layer controls its own storage, indexes, and local ordering.

`layers(scope)` lazily yields live layer objects, not flattened contributions or copies. Each shared Identity appears once in C3 order. `local=True` selects only the current Scope's Identity, including registrations through other Scopes sharing it. Every visited binding applies its Scope-local and Identity barriers after that layer, even if the layer is empty or its Identity was already visited. `layers()` without a Scope enumerates all active layers; `local=True` requires a Scope. Reads never create layers or bindings.

Do not structurally mutate a Compose during query iteration. Snapshot selected handlers before invoking them. Snapshots retain objects but do not extend their external resources' lifetimes. Inspect application data through the layer's own interface; Compose does not reconstruct original values from an aggregate-only layer.

Each successful registration keeps a unique token and its contributing Scope alive until exact disposal. Compose returns a `once()` wrapper that removes its registration record, detaches the layer if this was its final registration, invokes the layer disposer, and releases the Scope. The layer disposer needs no separate `once()` wrapper. Layer cleanup depends on live registrations, not its size, computed result, or truthiness; `len(compose)` counts registrations. A rejected registration must leave layer data unchanged. Register through Compose, not directly on layers returned by queries. Layer registration and disposal must not explicitly reenter their Compose's mutations. Arbitrary layer changes are not transactionally rolled back. Cleanup failures propagate and are replayed by repeated disposer calls without retrying.

`compose.derive(*, label=None, parents=..., binding=...)` creates a configured Scope for one Compose; `Compose.derive_many(*, label=None, parents=..., bindings=...)` configures several Composes on one new Scope. Both require explicit parents, accept ScopeBinding or Identity values, and create no lifecycle owner. Use `ctx.derive()` for owned children with mixed Context field and Compose bindings. An unbound Scope's first registration fixes a private unblocked binding. Withdrawal does not reset that configuration.

`ctx.effect(lambda: ctx.get(ref).register(target_scope, value))` looks up the Compose using ctx.scope, selects the registration layer explicitly, and owns withdrawal even if target_scope is unrelated. The disposer retains its original Compose target if the Context leaf is replaced. Direct compose.register leaves ownership to its caller. A child Context can register a different Compose at the same Context path to obtain an independent collection.

## Structured operations and projections

Context accepts Ref Trees for batch reads and writes. `extract` flattens the input once, resolves and reads each Ref in traversal order, and reconstructs the requested structure once. It stops at the first unresolved path or missing visible value, without resolving later Refs. Leaf values retain their identities. `update_tree` assigns values from a matching tree using `update`'s preflight checks.

`ContextView` provides only `get/exists/keys/to_dict/flatten` for reading and inspecting a subtree. It keeps a Context and a path prefix, forwards reads, and does not own data, Scope, or lifecycle operations. Its read-only `context` and `path` properties expose the owning Context and absolute path (an empty string for the root), including after disposal; they do not make disposed data readable. For tree-shaped extraction, use `ctx.extract(...)` with absolute paths or Refs; views do not provide `extract()` or require Tree configuration.

`Context.flatten(ref=None, *, local=False)` accepts a container path as a string or Ref; omitting it selects the root. ContextView delegates its `flatten()` call to this method with its prefix. Both return absolute Ref keys without copying leaf values.

`keys()`, `ContextView`, and `to_dict()` traverse Schema structure before reading the flat leaf cells. Empty non-root containers therefore have a stable declared role but do not appear in the effective data view; the root view remains available. `to_dict()` projects visible Context leaves into nested ordinary dictionaries for display or serialization. Across different Schemas, this projection cannot distinguish a mapping-valued leaf from equivalent nested Context paths. `flatten()` instead returns the exact visible `dict[Ref, Any]` leaf mapping:

```python
leaf_schema = Schema({"settings": Schema.leaf()})
tree_schema = Schema({"settings": {"theme": Schema.leaf()}})
mapping_leaf = Context()
mapping_leaf.declare(leaf_schema)
mapping_leaf.update({leaf_schema.resolve("settings"): {"theme": "dark"}})
nested_path = Context()
nested_path.declare(tree_schema)
nested_path.update({tree_schema.resolve("settings.theme"): "dark"})

assert mapping_leaf.get("settings") == nested_path.to_dict("settings")
assert mapping_leaf.flatten() == {
    **mapping_leaf.get("$").flatten(),
    leaf_schema.resolve("settings"): {"theme": "dark"},
}
assert nested_path.get("settings").flatten() == {
    tree_schema.resolve("settings.theme"): "dark"
}
```

Both methods resolve the bound Scope's effective C3 view by default and accept `local=True`. A `ContextView` accepts only relative string paths for subtree access; the empty string addresses the view itself and is the default for `keys()` and `to_dict()`. Use Context directly for absolute Ref lookups. The view’s `flatten()` result still contains absolute Schema refs. Neither method copies leaf values. To materialize values into a new root, create it, declare all copied paths, then call `snapshot.update(ctx.flatten("app"))`. Select an application subtree such as `app`; all copied fields must use `assign` mode. Root flattening also includes the register-mode framework configuration. `register` fields require explicit `register()` calls on the new owner; a snapshot does not transfer ownership. A new root receives a fresh Scope by default, so contributions targeting the source Scope are not visible; explicitly reusing that Scope shares Compose visibility but still does not share Context data between roots.
