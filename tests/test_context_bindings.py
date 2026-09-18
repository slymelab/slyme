from __future__ import annotations

import gc
import weakref
from collections.abc import Hashable

import pytest

from slyme.context import Context, Schema, Scope
from slyme.context.store import _ContextBinding, _ScopeUsage


@pytest.mark.parametrize("remove_first", [False, True])
def test_registration_disposer_owns_only_its_identity_data_until_called(
    remove_first: bool,
) -> None:
    class Payload:
        pass

    scope = Scope()
    other_scope = scope.fork()
    schema = Schema({"value": Schema.leaf(mode="register")})
    binding = _ContextBinding(
        schema.resolve_entry("value"),
        {scope: _ScopeUsage(), other_scope: _ScopeUsage()},
    )
    payload = Payload()
    other_payload = Payload()
    payload_ref = weakref.ref(payload)
    other_ref = weakref.ref(other_payload)
    binding_ref = weakref.ref(binding)
    remove = binding.register_value(scope, payload)
    binding.set_value(other_scope, other_payload)
    if remove_first:
        remove()

    del binding, payload, other_payload
    gc.collect()
    assert binding_ref() is None
    assert (payload_ref() is None) is remove_first
    assert other_ref() is None

    remove()
    gc.collect()
    assert payload_ref() is None
    assert other_ref() is None
    remove()


def test_shared_identity_has_one_current_value_and_a_persistent_barrier() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": "root"})
    left = root.isolate("value", identity="shared")
    right = root.isolate("value", identity="shared")
    left.set("value", "original")
    binding = next(iter(root._store._data.values()))
    right.set("value", "updated")
    assert left.get("value") == "updated"
    assert right.get("value") == "updated"
    assert len(binding._data) == 2
    assert binding._data["shared"].blocked

    right.delete("value")
    assert not left.exists("value")
    assert not right.exists("value")
    assert binding._data["shared"].blocked
    assert root.get("value") == "root"
    right.set("value", None)
    left.dispose()
    assert right.get("value") is None
    right.dispose()
    assert len(binding._data) == 1
    assert not any(data.blocked for data in binding._data.values())
    root.dispose()


def test_shared_value_does_not_retain_its_disposed_writers_scope() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    writer = root.isolate("value", identity="shared")
    reader = root.isolate("value", identity="shared")
    writer.set("value", "retained")
    scope_ref = weakref.ref(writer.scope)
    writer.dispose()
    del writer
    gc.collect()
    assert scope_ref() is None
    assert reader.get("value") == "retained"
    root.dispose()


@pytest.mark.parametrize("operation", ["set", "delete", "remove"])
def test_value_finalizer_can_replace_the_same_path(operation: str) -> None:
    registration = operation == "remove"
    ctx = Context()
    ctx.declare(
        Schema({"value": Schema.leaf(mode="register" if registration else "assign")})
    )
    events: list[str] = []

    class Payload:
        def __del__(self) -> None:
            events.append("finalized")
            if registration:
                ctx.register("value", "reentrant")
            else:
                ctx.set("value", "reentrant")

    if registration:
        remove = ctx.register("value", Payload())
    else:
        ctx.set("value", Payload())
    if operation == "set":
        ctx.set("value", "replacement")
    elif operation == "delete":
        ctx.delete("value")
    else:
        remove()
    assert events == ["finalized"]
    assert ctx.get("value") == "reentrant"
    if registration:
        remove()
        assert ctx.get("value") == "reentrant"
    else:
        ctx.delete("value")
        assert not ctx.exists("value")
    ctx.dispose()


def test_identity_release_preserves_a_barrier_created_by_a_value_finalizer() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": "root"})
    writer = root.isolate("value", identity="shared")
    replacements: list[Context] = []

    class Payload:
        def __del__(self) -> None:
            child = root.isolate("value", identity="shared")
            child.set("value", "new")
            replacements.append(child)

    writer.set("value", Payload())
    writer.dispose()
    assert len(replacements) == 1
    child = replacements[0]
    assert child.get("value") == "new"
    child.delete("value")
    assert not child.exists("value")
    root.dispose()


def test_context_resolve_walks_complete_c3_without_creating_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": "root"})
    left = root.isolate("value", identity="shared")
    right = root.isolate("value", identity="shared")
    unbound = Scope(parents=(left.scope, right.scope))
    child = root.fork(scope=unbound.fork())
    child.set("value", "child")
    binding = next(iter(root._store._data.values()))
    seen: list[Scope] = []
    original = binding._scope_identities.get
    identities_before = dict(binding._scope_identities)

    def record(scope: Scope, default: Hashable = None) -> Hashable:
        seen.append(scope)
        return original(scope, default)

    with monkeypatch.context() as patch:
        patch.setattr(binding._scope_identities, "get", record)
        assert child.get("value") == "child"
        assert tuple(seen) == child.scope.mro
    assert dict(binding._scope_identities) == identities_before
    root.dispose()
