from __future__ import annotations

import gc
import weakref

import pytest

from slyme.context import Context, Schema, Scope
from slyme.context.core import _ContextBinding


def test_scope_lifecycle_does_not_scan_application_bindings(
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
    binding = root._data[root.schema._resolve_entry("value0")]
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
    assert tuple(root._scope_bindings[child.scope]) == (binding,)
    child.set("value", "updated")
    child.delete("value")
    assert tuple(root._scope_bindings[child.scope]) == (binding,)
    child.dispose()
    assert not binding._identity_scopes.get(
        binding._identity_for(child.scope, create=False)
    )
    assert tuple(root._scope_bindings[child.scope]) == (binding,)
    root.dispose()
    assert not root._scope_bindings


def test_reverse_index_does_not_keep_released_scopes_alive() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    children = [root.fork(scope=root.scope.fork()) for _ in range(100)]
    scope_refs = [weakref.ref(child.scope) for child in children]
    for child in children:
        child.set("value", "temporary")
        child.dispose()
    assert len(root._scope_bindings) == 100
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
    assert tuple(index) == tuple(root._data.values())
    assert root.get("value") == "new"
    root.dispose()


def test_shared_scope_indexes_remain_local_to_each_application() -> None:
    scope = Scope()
    schema = Schema({"value": Schema.leaf()})
    left = Context({"value": "left"}, schema=schema, scope=scope)
    right = Context({"value": "right"}, schema=schema, scope=scope)
    assert left._scope_bindings is not right._scope_bindings
    assert set(left._scope_bindings[scope]).isdisjoint(right._scope_bindings[scope])
    left.dispose()
    assert right.get("value") == "right"
    assert right._scope_bindings[scope]
    right.dispose()
