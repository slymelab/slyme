from __future__ import annotations

import gc
import weakref

import pytest

from slyme.context import Context, Schema, Scope
from slyme.context.core import _ContextBinding
from slyme.utils.exception import BaseExceptionGroup


def test_scope_release_is_sparse_and_saved_scopes_restore_their_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context(
        {f"value{i}": i for i in range(100)},
        schema=Schema({f"value{i}": Schema.leaf() for i in range(100)}),
    )
    previous = root.isolate("value0", identity="shared")
    saved_scope = previous.scope
    previous.dispose()
    writer = root.isolate("value0", identity="shared")
    writer.set("value0", "retained")
    binding = root._data[root.schema.resolve_entry("value0")]
    restored: list[tuple[_ContextBinding, Scope]] = []
    released: list[tuple[_ContextBinding, Scope]] = []
    acquire = _ContextBinding.acquire_scope
    release = _ContextBinding.release_scope

    def reject_scan():
        pytest.fail("Scope lifecycle must not scan all application bindings.")

    def record_acquire(self, scope):
        restored.append((self, scope))
        acquire(self, scope)

    def record_release(self, scope):
        released.append((self, scope))
        release(self, scope)

    with monkeypatch.context() as patch:
        patch.setattr(root._data, "values", reject_scan)
        patch.setattr(_ContextBinding, "acquire_scope", record_acquire)
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


@pytest.mark.parametrize("operation", ["set", "add", "isolate"])
def test_first_binding_is_indexed_but_reads_are_not(operation: str) -> None:
    root = Context({"value": "root"}, schema=Schema({"value": Schema.leaf()}))
    if operation == "isolate":
        child = root.isolate("value")
    else:
        child = root.fork(scope=root.scope.fork())
        assert child.get("value") == "root"
        assert child.scope not in root._scope_bindings
        if operation == "set":
            child.set("value", "child")
        else:
            child.add("value", "child")

    binding = next(iter(root._data.values()))
    assert root._scope_bindings[child.scope] == {"value"}
    child.set("value", "updated")
    child.delete("value")
    assert root._scope_bindings[child.scope] == {"value"}
    child.dispose()
    assert not binding._identity_scopes.get(
        binding._identity_for(child.scope, create=False)
    )
    assert child.scope not in root._scope_bindings
    root.dispose()
    assert not root._scope_bindings


def test_reverse_index_does_not_keep_released_scopes_alive() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    children = [root.fork(scope=root.scope.fork()) for _ in range(100)]
    scope_refs = [weakref.ref(child.scope) for child in children]
    for child in children:
        child.set("value", "temporary")
        child.dispose()
    assert not root._scope_bindings
    del child
    children.clear()
    gc.collect()
    assert all(scope_ref() is None for scope_ref in scope_refs)
    assert not root._scope_bindings
    root.dispose()


def test_reverse_index_does_not_retain_withdrawn_schema_values() -> None:
    class Payload:
        pass

    root = Context()
    remove_schema = root.declare({"value": Schema.leaf()})
    payload = Payload()
    payload_ref = weakref.ref(payload)
    root.add("value", payload)
    binding = next(iter(root._data.values()))
    binding_ref = weakref.ref(binding)
    index = root._scope_bindings[root.scope]
    del binding, payload
    remove_schema()
    gc.collect()
    assert binding_ref() is None
    assert payload_ref() is None
    assert not index
    assert not root._data

    root.declare({"value": Schema.leaf()})
    root.set("value", "new")
    assert not index
    assert root._scope_bindings[root.scope] == {"value"}
    assert root.get("value") == "new"
    root.dispose()


def test_shared_scope_indexes_remain_local_to_each_application() -> None:
    scope = Scope()
    schema = Schema({"value": Schema.leaf()})
    left = Context({"value": "left"}, schema=schema, scope=scope)
    right = Context({"value": "right"}, schema=schema, scope=scope)
    assert left._scope_bindings is not right._scope_bindings
    assert left._scope_bindings[scope] == right._scope_bindings[scope] == {"value"}
    assert left._scope_bindings[scope] is not right._scope_bindings[scope]
    left.dispose()
    assert right.get("value") == "right"
    assert right._scope_bindings[scope]
    right.dispose()


def test_new_scopes_do_not_scan_schema_entries() -> None:
    class NoScan(dict):
        def values(self):
            pytest.fail("A new Scope must not scan application declarations.")

    root = Context({"value": 1}, schema=Schema({"value": Schema.leaf()}))
    object.__setattr__(root.schema, "_entries", NoScan(root.schema._entries))
    child = root.fork(scope=root.scope.fork())
    child.set("value", 2)
    child.dispose()
    root.dispose()


@pytest.mark.parametrize("operation", ["delete", "drop"])
@pytest.mark.parametrize("target", ["", "group"])
def test_container_deletion_only_visits_intersecting_bindings(
    monkeypatch: pytest.MonkeyPatch, operation: str, target: str
) -> None:
    root = Context(
        {f"group.value{i}": i for i in range(1000)},
        schema=Schema(
            {
                "group": {f"value{i}": Schema.leaf() for i in range(1000)},
                "other": Schema.leaf(),
            }
        ),
    )
    child = root.fork(scope=root.scope.fork())
    child.set("group.value0", "child")
    child.set("other", "keep")
    visited: list[str] = []
    original = Context._binding

    def record(self, entry, *, create):
        visited.append(entry.ref.path)
        return original(self, entry, create=create)

    with monkeypatch.context() as patch:
        patch.setattr(Context, "_binding", record)
        if operation == "delete":
            child.delete(target)
        else:
            child.drop([target, "group.value0", target])
    expected = {"group.value0", "other"} if target == "" else {"group.value0"}
    assert set(visited) == expected
    assert len(visited) == len(expected)
    assert child.get("group.value0") == 0
    assert child._scope_bindings[child.scope] == {"group.value0", "other"}
    if target:
        assert child.get("other") == "keep"
    root.dispose()


@pytest.mark.parametrize("operation", ["delete", "drop"])
def test_delete_keeps_shared_identity_ownership_without_a_prior_write(
    operation: str,
) -> None:
    root = Context(schema=Schema({"group": {"value": Schema.leaf()}}))
    reader = root.isolate("group.value", identity="shared")
    writer = root.isolate("group.value", identity="shared")
    writer.set("group.value", "old")
    if operation == "delete":
        reader.delete("group")
    else:
        reader.drop(["group"])
    assert not reader.exists("group.value")
    assert reader._scope_bindings[reader.scope] == {"group.value"}
    writer.set("group.value", "new")
    writer.dispose()
    assert reader.get("group.value") == "new"
    reader.delete("group.value")
    assert not reader.exists("group.value")
    assert reader._scope_bindings[reader.scope] == {"group.value"}
    root.dispose()


def test_schema_withdrawal_cleans_every_application_before_path_reuse() -> None:
    schema = Schema()
    withdraw = schema.declare({"value": Schema.leaf()})
    left = Context(schema=schema)
    right = Context(schema=schema)
    writer = left.isolate("value", identity="shared")
    saved = writer.scope
    writer.set("value", "old")
    writer.dispose()
    left.set("value", "left")
    right.set("value", "right")
    old_entry = schema.resolve_entry("value")
    old_binding = left._data[old_entry]
    withdraw()
    assert not left._scope_bindings
    assert not right._scope_bindings
    assert not left._data
    assert not right._data
    assert not old_binding._values
    assert not old_binding._identity_scopes

    schema.declare({"value": Schema.leaf()})
    new_writer = left.isolate("value", identity="shared")
    new_writer.set("value", "new")
    reader = left.fork(scope=saved)
    assert saved not in left._scope_bindings
    assert not reader.exists("value")
    reader.dispose()
    assert new_writer.get("value") == "new"
    left.dispose()
    assert schema._contexts == {right}
    right.dispose()
    assert not schema._contexts


def test_schema_retains_an_application_until_explicit_disposal() -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context({"value": object()}, schema=schema)
    root_ref = weakref.ref(root)
    del root
    gc.collect()
    assert root_ref() is not None
    assert len(schema._contexts) == 1
    next(iter(schema._contexts)).dispose()
    gc.collect()
    assert root_ref() is None
    assert not schema._contexts


def test_schema_registers_roots_without_registering_children() -> None:
    schema = Schema()
    left = Context(schema=schema)
    right = Context(schema=schema)
    child = left.fork()
    grandchild = child.fork()
    assert schema._contexts == {left, right}
    child.dispose()
    assert schema._contexts == {left, right}
    assert not left._owned and not grandchild._owned
    left.dispose()
    assert schema._contexts == {right}
    right.dispose()
    right.dispose()
    assert not schema._contexts


def test_failed_context_initialization_removes_schema_registration() -> None:
    schema = Schema({"value": Schema.leaf()})
    existing = Context(schema=schema)
    with pytest.raises(KeyError):
        Context({"undeclared": 1}, schema=schema)
    assert schema._contexts == {existing}
    existing.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_failed_root_cleanup_removes_schema_registration(
    asynchronous: bool,
) -> None:
    schema = Schema()
    root = Context(schema=schema)
    failure = ValueError("cleanup failed")

    def cleanup():
        raise failure

    async def async_cleanup():
        cleanup()

    root.effect(lambda: async_cleanup if asynchronous else cleanup)
    with pytest.raises(BaseExceptionGroup) as raised:
        await root.adispose()
    assert raised.value.exceptions == (failure,)
    assert not schema._contexts


def test_schema_withdrawal_allows_roots_to_unregister_during_cleanup() -> None:
    schema = Schema()
    withdraw = schema.declare({"value": Schema.leaf()})
    left = Context(schema=schema)
    right = Context(schema=schema)

    class Payload:
        def __del__(self):
            left.dispose()
            right.dispose()

    left.set("value", Payload())
    right.set("value", "right")
    withdraw()
    assert not schema._contexts
    assert not left._data and not right._data


def test_withdrawal_preserves_other_app_reentrant_redeclaration() -> None:
    schema = Schema()
    withdraw = schema.declare({"value": Schema.leaf()})
    left = Context(schema=schema)
    right = Context({"value": "old"}, schema=schema)

    class Payload:
        def __del__(self):
            schema.declare({"value": Schema.leaf()})
            right.set("value", "new")

    left.set("value", Payload())
    withdraw()
    assert right.get("value") == "new"
    assert right._scope_bindings[right.scope] == {"value"}
    right.delete("")
    assert not right.exists("value")
    left.dispose()
    right.dispose()
