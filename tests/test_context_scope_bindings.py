from __future__ import annotations

import gc
import weakref

import pytest

from slyme.context import Context, Schema, Scope
from slyme.context.store import ContextStore, _ContextBinding
from slyme.utils.exception import BaseExceptionGroup


def test_scope_release_is_sparse_and_saved_scopes_restore_their_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NoScan(dict):
        def values(self):
            pytest.fail("Scope lifecycle must not scan all application bindings.")

    root = Context()
    root.declare(Schema({f"value{i}": Schema.leaf() for i in range(100)}))
    root.update({f"value{i}": i for i in range(100)})
    object.__setattr__(root._store, "_data", NoScan(root._store._data))
    previous = root.isolate("value0", identity="shared")
    saved_scope = previous.scope
    previous.dispose()
    writer = root.isolate("value0", identity="shared")
    writer.set("value0", "retained")
    binding = root._store._data[root.resolve_entry("value0")]
    restored: list[tuple[_ContextBinding, Scope]] = []
    released: list[tuple[_ContextBinding, Scope]] = []
    restore = _ContextBinding.restore_scope
    release = _ContextBinding.release_scope

    def record_restore(self, scope):
        result = restore(self, scope)
        if result:
            restored.append((self, scope))
        return result

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


@pytest.mark.parametrize("operation", ["set", "register", "isolate"])
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
    if operation == "isolate":
        child = root.isolate("value")
    else:
        child = root.fork(scope=root.scope.fork())
        assert child.get("value") == "root"
        assert not root._store._scope_usages[child.scope].entries
        if operation == "set":
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
    assert binding._lookup_data(child.scope) is None
    assert child.scope in binding._scope_identities
    assert not root._store._scope_usages[child.scope].viewers
    assert not root._store._scope_usages[child.scope].entries
    root.dispose()
    assert not root._store._scope_usages


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


def test_withdrawal_detaches_store_indexes_before_payload_redeclares_the_path() -> None:
    root = Context()
    withdraw = root.declare({"value": Schema.leaf(mode="register")})
    child = root.fork(scope=root.scope.fork())
    entry = root.resolve_entry("value")
    events = []

    class Payload:
        def __del__(self) -> None:
            assert entry not in root._store._data
            assert all(
                entry not in usage.entries
                for usage in root._store._scope_usages.values()
            )
            assert root.scope not in binding._scope_identities
            assert child.scope not in binding._scope_identities
            root.declare({"value": Schema.leaf()})
            child.set("value", "new")
            events.append("redeclared")

    remove = root.register("value", Payload())
    child.register("value", "old child")
    binding = root._store._data[entry]
    withdraw()
    assert events == ["redeclared"]
    assert binding.scopes == ()
    remove()
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
    schema = Schema({"value": Schema.leaf()})
    store = ContextStore(schema)
    first, second = Scope(), Scope()
    viewer = object()
    entry = schema.resolve_entry("value")
    store.acquire_scope(viewer, first)
    store.isolate(first, [entry], identity="shared")
    store.set(first, "value", "old")
    binding = store._data[entry]
    usage = store._scope_usages[first]
    store.release_scope(viewer, first)
    assert not usage.viewers and not usage.entries
    assert store._data[entry] is binding
    assert not binding._data

    store.acquire_scope(viewer, second)
    store.isolate(second, [entry], identity="shared")
    store.set(second, "value", "new")
    store.acquire_scope(viewer, first)
    assert store._scope_usages[first] is usage
    assert usage.entries == {entry}
    store.release_scope(viewer, second)
    assert store.leaf_value(first, entry, local=False) == "new"
    store.release_scope(viewer, first)
    assert not binding._data
    assert store._data[entry] is binding
    store.dispose()
    assert not store._data
    assert not store._scope_usages
    assert not schema._stores


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
        assert not root._store._scope_usages[scope].entries
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
    root = Context()
    root.declare(Schema({"group": {"value": Schema.leaf()}}))
    reader = root.isolate("group.value", identity="shared")
    writer = root.isolate("group.value", identity="shared")
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
    root = Context()
    withdraw = root.declare({"value": Schema.leaf()})
    schema = root._schema
    left = root.fork(scope=root.scope.fork())
    right = root.fork(scope=root.scope.fork())
    writer = left.isolate("value", identity="shared")
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
    new_writer = left.isolate("value", identity="shared")
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

    left.set(left_scope, "value", Payload())
    right.set(right_scope, "value", "right")
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
    right.set(right_scope, "value", "old")

    class Payload:
        def __del__(self):
            schema.declare({"value": Schema.leaf()})
            right.set(right_scope, "value", "new")

    left.set(left_scope, "value", Payload())
    withdraw()
    entry = schema.resolve_entry("value")
    assert right.leaf_value(right_scope, entry, local=False) == "new"
    assert right._scope_usages[right_scope].entries == {entry}
    right.delete(right_scope, "")
    assert not right.exists(right_scope, "value")
    left.release_scope(left_viewer, left_scope)
    right.release_scope(right_viewer, right_scope)
    left.dispose()
    right.dispose()
