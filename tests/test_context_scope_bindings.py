from __future__ import annotations

import gc
import weakref

import pytest

from slyme.context import Context, Identity, Schema, Scope, ScopeBinding
from slyme.context.store import ContextStore, _ContextBinding
from slyme.utils.exception import BaseExceptionGroup


def test_scope_release_is_sparse_and_saved_scopes_restore_their_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_identity = Identity("shared", blocked=True)

    class NoScan(dict):
        def values(self):
            pytest.fail("Scope lifecycle must not scan all application bindings.")

    root = Context()
    root.declare(Schema({f"value{i}": Schema.leaf() for i in range(100)}))
    root.update({f"value{i}": i for i in range(100)})
    object.__setattr__(root._store, "_data", NoScan(root._store._data))
    previous = root.derive(
        bindings={"value0": ScopeBinding(shared_identity, blocked=False)}
    )
    saved_scope = previous.scope
    previous.dispose()
    writer = root.derive(
        bindings={"value0": ScopeBinding(shared_identity, blocked=False)}
    )
    writer.set("value0", "retained")
    binding = root._store._data[root.resolve_entry("value0")]
    object.__setattr__(root._schema, "_entries", NoScan(root._schema._entries))
    restored: list[tuple[_ContextBinding, Scope]] = []
    released: list[tuple[_ContextBinding, Scope]] = []
    restore = _ContextBinding.restore_scope
    release = _ContextBinding.release_scope

    def record_restore(self, scope):
        restored.append((self, scope))
        return restore(self, scope)

    def record_release(self, scope):
        released.append((self, scope))
        release(self, scope)

    with monkeypatch.context() as patch:
        patch.setattr(_ContextBinding, "restore_scope", record_restore)
        patch.setattr(_ContextBinding, "release_scope", record_release)
        empty = root.fork(scope=root.scope.fork())
        assert empty.get("value1") == 1
        empty.dispose()
        assert restored == released == []

        reader = root.fork(scope=saved_scope.fork())
        assert restored == [(binding, saved_scope)]
        writer.dispose()
        assert reader.get("value0") == "retained"
        reader.dispose()
        assert released == [(binding, writer.scope), (binding, saved_scope)]
    root.dispose()


@pytest.mark.parametrize("operation", ["set", "register", "bind", "block"])
def test_first_binding_is_indexed_but_reads_are_not(operation: str) -> None:
    registration = operation == "register"
    root = Context()
    root.declare(
        Schema({"value": Schema.leaf(mode="register" if registration else "assign")})
    )
    if registration:
        root.register("value", "root")
    else:
        root.set("value", "root")
    child = root.fork(scope=root.scope.fork())
    assert child.get("value") == "root"
    assert not root._store._scope_usages[child.scope].entries
    if operation == "block":
        child._store.bind(
            child.scope,
            child.resolve_entry("value", role="leaf"),
            ScopeBinding(blocked=True),
        )
    elif operation == "bind":
        child._store.bind(
            child.scope,
            child.resolve_entry("value", role="leaf"),
            ScopeBinding(Identity(), blocked=False),
        )
    elif operation == "set":
        child.set("value", "child")
    else:
        remove = child.register("value", "child")

    binding = root._store._data[root.resolve_entry("value")]
    assert root._store._scope_usages[child.scope].entries == {
        root.resolve_entry("value")
    }
    if registration:
        remove()
    else:
        child.set("value", "updated")
        child.delete("value")
    assert root._store._scope_usages[child.scope].entries == {
        root.resolve_entry("value")
    }
    child.dispose()
    assert binding._get_data(child.scope) is None
    assert child.scope in binding._scope_bindings
    assert not root._store._scope_usages[child.scope].viewers
    assert root._store._scope_usages[child.scope].entries == {
        root.resolve_entry("value")
    }
    root.dispose()
    assert all(not usage.viewers for usage in root._store._scope_usages.values())


def test_reverse_index_does_not_keep_released_scopes_alive() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    children = [root.fork(scope=root.scope.fork()) for _ in range(100)]
    scope_refs = [weakref.ref(child.scope) for child in children]
    for child in children:
        child.set("value", "temporary")
        child.dispose()
    assert len(root._store._scope_usages) == 101
    assert root._store._scope_usages[root.scope].entries == {
        root.resolve_entry(ref.path) for ref in root.get("$").flatten()
    }
    del child
    children.clear()
    gc.collect()
    assert all(scope_ref() is None for scope_ref in scope_refs)
    assert set(root._store._scope_usages) == {root.scope}
    assert root._store._scope_usages[root.scope].entries == {
        root.resolve_entry(ref.path) for ref in root.get("$").flatten()
    }
    root.dispose()


def test_reverse_index_does_not_retain_withdrawn_schema_values() -> None:
    class Payload:
        pass

    root = Context()
    remove_schema = root.declare({"value": Schema.leaf(mode="register")})
    payload = Payload()
    payload_ref = weakref.ref(payload)
    remove_value = root.register("value", payload)
    binding = root._store._data[root.resolve_entry("value")]
    binding_ref = weakref.ref(binding)
    index = root._store._scope_usages[root.scope].entries
    del binding, payload
    remove_schema()
    gc.collect()
    assert binding_ref() is not None
    assert not binding_ref()._data
    assert payload_ref() is None
    assert index == {root.resolve_entry(ref.path) for ref in root.get("$").flatten()}
    assert set(root._store._data) == index

    root.declare({"value": Schema.leaf()})
    root.set("value", "new")
    remove_value()
    remove_value()
    gc.collect()
    assert binding_ref() is None
    assert index == {root.resolve_entry(ref.path) for ref in root.flatten()}
    assert root._store._scope_usages[root.scope].entries is index
    assert root.get("value") == "new"
    root.dispose()


def test_withdrawal_detaches_store_indexes_before_redeclaring_the_path() -> None:
    root = Context()
    withdraw = root.declare({"value": Schema.leaf(mode="register")})
    child = root.fork(scope=root.scope.fork())
    entry = root.resolve_entry("value")

    class Payload:
        pass

    payload = Payload()
    payload_ref = weakref.ref(payload)
    remove = root.register("value", payload)
    remove_child = child.register("value", "old child")
    del payload
    binding = root._store._data[entry]
    withdraw()
    gc.collect()
    assert payload_ref() is None
    assert entry not in root._store._data
    assert all(
        entry not in usage.entries for usage in root._store._scope_usages.values()
    )
    assert not binding._scope_bindings
    assert binding.scopes == ()

    root.declare({"value": Schema.leaf()})
    child.set("value", "new")
    remove()
    remove_child()
    assert child.get("value") == "new"
    new_entry = root.resolve_entry("value")
    assert new_entry is not entry
    assert root._store._scope_usages[child.scope].entries == {new_entry}
    child.dispose()
    assert not root.exists("value")
    root.dispose()


def test_shared_scope_indexes_remain_local_to_each_application() -> None:
    scope = Scope()
    schema = Schema({"value": Schema.leaf()})
    left = Context(scope=scope)
    left.declare(schema)
    left.update({"value": "left"})
    right = Context(scope=scope)
    right.declare(schema)
    right.update({"value": "right"})
    assert left._store._scope_usages is not right._store._scope_usages
    assert left._store._scope_usages[scope].entries == {
        left.resolve_entry(ref.path) for ref in left.flatten()
    }
    assert right._store._scope_usages[scope].entries == {
        right.resolve_entry(ref.path) for ref in right.flatten()
    }
    assert (
        left._store._scope_usages[scope].entries
        is not right._store._scope_usages[scope].entries
    )
    left.dispose()
    assert right.get("value") == "right"
    assert right._store._scope_usages[scope].entries
    right.dispose()


def test_new_scopes_do_not_scan_schema_entries() -> None:
    class NoScan(dict):
        def values(self):
            pytest.fail("A new Scope must not scan application declarations.")

    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": 1})
    object.__setattr__(root._schema, "_entries", NoScan(root._schema._entries))
    child = root.fork(scope=root.scope.fork())
    child.set("value", 2)
    child.dispose()
    root.dispose()


def test_store_retains_saved_identities_across_a_gap_without_viewers() -> None:
    shared_identity = Identity("shared", blocked=True)
    schema = Schema({"value": Schema.leaf()})
    store = ContextStore(schema)
    first, second = Scope(), Scope()
    viewer = object()
    entry = schema.resolve_entry("value")
    store.acquire_scope(viewer, first)
    store.bind(first, entry, ScopeBinding(shared_identity, blocked=False))
    store.set(first, entry, "old")
    binding = store._data[entry]
    usage = store._scope_usages[first]
    store.release_scope(viewer, first)
    assert not usage.viewers
    assert usage.entries == {entry}
    assert store._scope_usages[first] is usage
    assert store._data[entry] is binding
    assert not binding._data

    store.acquire_scope(viewer, second)
    store.bind(second, entry, ScopeBinding(shared_identity, blocked=False))
    store.set(second, entry, "new")
    store.acquire_scope(viewer, first)
    assert store._scope_usages[first] is usage
    assert store._scope_usages[first].entries == {entry}
    store.release_scope(viewer, second)
    assert store.get(first, entry) == "new"
    store.release_scope(viewer, first)
    assert not binding._data
    assert store._data[entry] is binding
    store.dispose()
    assert not store._data
    assert all(not usage.viewers for usage in store._scope_usages.values())
    assert not schema._stores


def test_saved_scope_history_prunes_withdrawn_declaration_generations() -> None:
    class Payload:
        pass

    root = Context()
    scope = root.scope.fork()
    old_entries = []
    for _ in range(20):
        withdraw = root.declare({"temporary": Schema.leaf()})
        child = root.fork(scope=scope)
        payload = Payload()
        payload_ref = weakref.ref(payload)
        child.set("temporary", payload)
        old_entry = root.resolve_entry("temporary")
        old_entries.append(weakref.ref(old_entry))
        del payload
        child.dispose()
        withdraw()
        assert not old_entry.alive
        assert old_entry in root._store._scope_usages[scope].entries
        assert not root._store._scope_usages[scope].viewers
        assert payload_ref() is None

    del old_entry
    root.declare({"temporary": Schema.leaf()})
    root.set("temporary", "parent")
    reader = root.fork(scope=scope)
    assert not root._store._scope_usages[scope].entries
    assert reader.get("temporary") == "parent"
    assert not reader.exists("temporary", local=True)
    gc.collect()
    assert all(reference() is None for reference in old_entries)
    root.dispose()


@pytest.mark.parametrize("failure_after_restore", [False, True])
def test_sparse_restore_failure_rolls_back_all_recorded_bindings(
    monkeypatch: pytest.MonkeyPatch, failure_after_restore: bool
) -> None:
    shared_identity = Identity("shared", blocked=True)
    root = Context()
    paths = ("first", "second", "third")
    root.declare({path: Schema.leaf() for path in paths})
    config = ScopeBinding(shared_identity, blocked=False)
    previous = root.derive(bindings={path: config for path in paths})
    scope = previous.scope
    previous.dispose()
    writer = root.derive(bindings={path: config for path in paths})
    writer.update({path: path for path in paths})
    entries = set(root._store._scope_usages[scope].entries)
    restored = []
    original = _ContextBinding.restore_scope
    failure = ValueError("second binding failed")

    def fail_second(binding, restored_scope):
        restored.append(binding)
        if len(restored) == 2:
            if failure_after_restore:
                original(binding, restored_scope)
            raise failure
        return original(binding, restored_scope)

    with monkeypatch.context() as patch:
        patch.setattr(_ContextBinding, "restore_scope", fail_second)
        with pytest.raises(ValueError) as caught:
            root.fork(scope=scope)
    assert caught.value is failure
    assert len(restored) == 2
    assert not root._store._scope_usages[scope].viewers
    assert root._store._scope_usages[scope].entries == entries
    for entry in entries:
        assert root._store._data[entry].scopes == (writer.scope,)
    reader = root.fork(scope=scope)
    writer.dispose()
    assert [reader.get(path) for path in paths] == list(paths)
    root.dispose()


def test_scope_usage_lifecycle_requires_only_point_lookups() -> None:
    class PointLookupOnly(weakref.WeakKeyDictionary):
        def __iter__(self):
            pytest.fail("Scope lifecycle must not enumerate usage records.")

        def __len__(self):
            pytest.fail("Scope lifecycle must not count usage records.")

        def items(self):
            pytest.fail("Scope lifecycle must not enumerate usage records.")

        def values(self):
            pytest.fail("Scope lifecycle must not enumerate usage records.")

    root = Context()
    root.declare({"value": Schema.leaf()})
    root._store._scope_usages = PointLookupOnly(root._store._scope_usages)
    scope = root.scope.fork()
    for _ in range(2):
        child = root.fork(scope=scope)
        child.set("value", 1)
        child.dispose()
        assert not root._store._scope_usages[scope].viewers
        assert root._store._scope_usages[scope].entries == {root.resolve_entry("value")}
    root.dispose()


@pytest.mark.parametrize("operation", ["delete", "drop"])
@pytest.mark.parametrize("target", ["group"])
def test_container_deletion_only_visits_intersecting_bindings(
    monkeypatch: pytest.MonkeyPatch, operation: str, target: str
) -> None:
    root = Context()
    root.declare(
        Schema(
            {
                "group": {f"value{i}": Schema.leaf() for i in range(1000)},
                "other": Schema.leaf(),
            }
        )
    )
    root.update({f"group.value{i}": i for i in range(1000)})
    child = root.fork(scope=root.scope.fork())
    child.set("group.value0", "child")
    child.set("other", "keep")
    visited: list[str] = []
    original = ContextStore._delete_leaf

    def record(self, scope, entry):
        visited.append(entry.ref.path)
        return original(self, scope, entry)

    with monkeypatch.context() as patch:
        patch.setattr(ContextStore, "_delete_leaf", record)
        if operation == "delete":
            child.delete(target)
        else:
            child.drop([target, "group.value0", target])
    expected = {"group.value0", "other"} if target == "" else {"group.value0"}
    assert set(visited) == expected
    assert len(visited) == len(expected)
    assert child.get("group.value0") == 0
    assert child._store._scope_usages[child.scope].entries == {
        root.resolve_entry(path) for path in ("group.value0", "other")
    }
    if target:
        assert child.get("other") == "keep"
    root.dispose()


@pytest.mark.parametrize("operation", ["delete", "drop"])
def test_delete_keeps_shared_identity_ownership_without_a_prior_write(
    operation: str,
) -> None:
    shared_identity = Identity("shared", blocked=True)
    root = Context()
    root.declare(Schema({"group": {"value": Schema.leaf()}}))
    reader = root.derive(
        bindings={"group.value": ScopeBinding(shared_identity, blocked=False)}
    )
    writer = root.derive(
        bindings={"group.value": ScopeBinding(shared_identity, blocked=False)}
    )
    writer.set("group.value", "old")
    if operation == "delete":
        reader.delete("group")
    else:
        reader.drop(["group"])
    assert not reader.exists("group.value")
    assert reader._store._scope_usages[reader.scope].entries == {
        root.resolve_entry("group.value")
    }
    writer.set("group.value", "new")
    writer.dispose()
    assert reader.get("group.value") == "new"
    reader.delete("group.value")
    assert not reader.exists("group.value")
    assert reader._store._scope_usages[reader.scope].entries == {
        root.resolve_entry("group.value")
    }
    root.dispose()


def test_schema_withdrawal_cleans_every_scope_before_path_reuse() -> None:
    shared_identity = Identity("shared", blocked=True)
    root = Context()
    withdraw = root.declare({"value": Schema.leaf()})
    schema = root._schema
    left = root.fork(scope=root.scope.fork())
    right = root.fork(scope=root.scope.fork())
    writer = left.derive(
        bindings={"value": ScopeBinding(shared_identity, blocked=False)}
    )
    saved = writer.scope
    writer.set("value", "old")
    writer.dispose()
    left.set("value", "left")
    right.set("value", "right")
    old_entry = schema.resolve_entry("value")
    old_binding = left._store._data[old_entry]
    withdraw()
    assert not left._store._scope_usages[left.scope].entries
    assert not right._store._scope_usages[right.scope].entries
    assert set(left._store._data) == {
        root.resolve_entry(ref.path) for ref in root.get("$").flatten()
    }
    assert set(right._store._data) == {
        root.resolve_entry(ref.path) for ref in root.get("$").flatten()
    }
    assert not old_binding._data

    root.declare({"value": Schema.leaf()})
    new_writer = left.derive(
        bindings={"value": ScopeBinding(shared_identity, blocked=False)}
    )
    new_writer.set("value", "new")
    reader = left.fork(scope=saved)
    assert not left._store._scope_usages[saved].entries
    assert not reader.exists("value")
    reader.dispose()
    assert new_writer.get("value") == "new"
    left.dispose()
    assert schema._stores == {right._store}
    right.dispose()
    root.dispose()
    assert not schema._stores


def test_schema_retains_an_application_until_explicit_disposal() -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context()
    root.declare(schema)
    root.update({"value": object()})
    schema = root._schema
    root_ref = weakref.ref(root)
    del root
    gc.collect()
    assert root_ref() is not None
    assert len(schema._stores) == 1
    root_ref().dispose()
    gc.collect()
    assert root_ref() is None
    assert not schema._stores


def test_schema_registers_roots_without_registering_children() -> None:
    left = Context()
    right = Context()
    schema = left._schema
    initial_owned = tuple(left._lifecycle._owned)
    child = left.fork()
    grandchild = child.fork()
    assert schema._stores == {left._store}
    assert right._schema._stores == {right._store}
    child.dispose()
    assert schema._stores == {left._store}
    assert tuple(left._lifecycle._owned) == initial_owned
    assert not grandchild._lifecycle._owned
    left.dispose()
    assert not schema._stores
    assert right._schema._stores == {right._store}
    right.dispose()
    right.dispose()
    assert not schema._stores


def test_failed_declaration_leaves_the_created_context_available() -> None:
    ctx = Context()
    ctx.declare({"value": Schema.leaf(int)})
    with pytest.raises(ValueError, match="Conflicting"):
        ctx.declare({"temporary": Schema.leaf(), "value": Schema.leaf(str)})
    assert ctx._schema._stores == {ctx._store}
    assert ctx._store._scope_usages[ctx.scope].viewers == {ctx}
    with pytest.raises(KeyError):
        ctx.resolve("temporary")
    ctx.set("value", 1)
    assert ctx.get("value") == 1
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_failed_root_cleanup_removes_schema_registration(
    asynchronous: bool,
) -> None:
    root = Context()
    schema = root._schema
    failure = ValueError("cleanup failed")

    def cleanup():
        raise failure

    async def async_cleanup():
        cleanup()

    root.effect(lambda: async_cleanup if asynchronous else cleanup)
    with pytest.raises(BaseExceptionGroup) as raised:
        await root.adispose()
    assert raised.value.exceptions == (failure,)
    assert not schema._stores


def test_schema_withdrawal_allows_stores_to_unregister_during_cleanup() -> None:
    schema = Schema()
    withdraw = schema.declare({"value": Schema.leaf()})
    left = ContextStore(schema)
    right = ContextStore(schema)
    left_scope, right_scope = Scope(), Scope()
    left_viewer, right_viewer = object(), object()
    left.acquire_scope(left_viewer, left_scope)
    right.acquire_scope(right_viewer, right_scope)

    class Payload:
        def __del__(self):
            left.release_scope(left_viewer, left_scope)
            right.release_scope(right_viewer, right_scope)
            left.dispose()
            right.dispose()

    left.set(left_scope, schema.resolve_entry("value"), Payload())
    right.set(right_scope, schema.resolve_entry("value"), "right")
    withdraw()
    assert not schema._stores
    assert not left._data and not right._data


def test_withdrawal_preserves_other_store_reentrant_redeclaration() -> None:
    schema = Schema()
    withdraw = schema.declare({"value": Schema.leaf()})
    left = ContextStore(schema)
    right = ContextStore(schema)
    left_scope, right_scope = Scope(), Scope()
    left_viewer, right_viewer = object(), object()
    left.acquire_scope(left_viewer, left_scope)
    right.acquire_scope(right_viewer, right_scope)
    right.set(right_scope, schema.resolve_entry("value"), "old")

    class Payload:
        def __del__(self):
            schema.declare({"value": Schema.leaf()})
            right.set(right_scope, schema.resolve_entry("value"), "new")

    left.set(left_scope, schema.resolve_entry("value"), Payload())
    withdraw()
    entry = schema.resolve_entry("value")
    assert right.get(right_scope, entry) == "new"
    assert right._scope_usages[right_scope].entries == {entry}
    right.delete(right_scope, schema.resolve_entry(""))
    assert not right.exists(right_scope, entry)
    left.release_scope(left_viewer, left_scope)
    right.release_scope(right_viewer, right_scope)
    left.dispose()
    right.dispose()
