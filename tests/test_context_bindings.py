from __future__ import annotations

import gc
import weakref
from collections.abc import Hashable

import pytest

from slyme.context import Context, ContextStore, Schema, Scope
from slyme.context.store import _MISSING, _ContextBinding


def test_binding_distinguishes_saved_identity_from_active_scope_ownership() -> None:
    binding = _ContextBinding()
    left, right = Scope(), Scope()
    assert not binding.restore_scope(left)
    assert not binding._scope_identities
    assert binding.scopes == ()
    binding.bind(left, identity="shared")
    binding.set_blocked(left, blocked=True)
    binding.bind(right, identity="shared")
    binding.set_blocked(right, blocked=True)
    binding.set(left, "value")
    snapshot = binding.scopes
    assert set(snapshot) == {left, right}

    binding.release_scope(left)
    assert left in binding._scope_identities
    assert binding.scopes == (right,)
    assert set(snapshot) == {left, right}
    assert binding.restore_scope(left)
    assert binding.restore_scope(left)
    assert set(binding.scopes) == {left, right}
    binding.release_scope(right)
    assert binding.get(left) == "value"
    binding.release_scope(left)
    assert binding.scopes == ()
    assert left in binding._scope_identities

    binding.clear()
    assert not binding.restore_scope(left)
    assert not binding.restore_scope(right)


def test_block_reuses_equal_identity_without_replacing_value_or_token() -> None:
    binding = _ContextBinding()
    scope = Scope()
    identity = ("shared", 1)
    equal_identity = tuple(["shared", 1])
    token = object()
    binding.bind(scope, identity=identity)
    binding.set_blocked(scope, blocked=True)
    binding.set(scope, None, token=token)
    data = binding._data[identity]

    binding.bind(scope, identity=equal_identity)
    binding.set_blocked(scope, blocked=True)
    binding.set_blocked(scope, blocked=True)
    assert binding._data[identity] is data
    assert binding.scopes == (scope,)
    assert binding.matches(scope, token=token)
    assert binding.get(scope) is None
    binding.delete(scope, token=token)
    assert data.blocked
    assert binding.get(scope) is _MISSING
    binding.clear()


@pytest.mark.parametrize("released", [False, True])
def test_block_rejects_rebinding_a_saved_identity(released: bool) -> None:
    binding = _ContextBinding()
    scope = Scope()
    binding.set(scope, "value")
    identity = binding._scope_identities[scope]
    if released:
        binding.release_scope(scope)

    with pytest.raises(ValueError, match="cannot be rebound"):
        binding.bind(scope, identity=object())
        binding.set_blocked(scope, blocked=True)
    assert binding._scope_identities[scope] is identity
    if released:
        assert not binding._data
    else:
        assert binding.get(scope) == "value"
        assert not binding._data[identity].blocked
    binding.clear()


def test_scope_restoration_does_not_revive_released_data() -> None:
    binding = _ContextBinding()
    scope = Scope()
    token = object()
    binding.bind(scope, identity="saved")
    binding.set_blocked(scope, blocked=True)
    binding.set(scope, "old", token=token)
    binding.release_scope(scope)
    binding.release_scope(scope)
    assert not binding._data

    assert binding.restore_scope(scope)
    assert binding._scope_identities[scope] == "saved"
    assert binding.scopes == (scope,)
    assert binding.get(scope, local=True) is _MISSING
    assert not binding.matches(scope, token=token)
    assert not binding._data["saved"].blocked
    binding.set(scope, "new")
    assert binding.get(scope) == "new"
    binding.clear()


def test_binding_clear_invalidates_old_tokens() -> None:
    class Payload:
        pass

    binding = _ContextBinding()
    scope = Scope()
    payload = Payload()
    reference = weakref.ref(payload)
    token = object()
    binding.set(scope, payload, token=token)
    del payload
    binding.clear()
    gc.collect()
    assert reference() is None
    assert scope not in binding._scope_identities
    assert binding.scopes == ()
    assert not binding.matches(scope, token=token)

    replacement_token = object()
    binding.set(scope, "replacement", token=replacement_token)
    assert not binding.matches(scope, token=token)
    with pytest.raises(ValueError, match="token"):
        binding.delete(scope, token=token)
    assert binding.get(scope) == "replacement"
    binding.delete(scope, token=replacement_token)
    assert binding.get(scope) is _MISSING
    binding.clear()


@pytest.mark.parametrize("operation", ["set", "delete"])
@pytest.mark.parametrize("supplied_token", [None, object()])
def test_protected_values_require_the_exact_token(
    operation: str, supplied_token: object | None
) -> None:
    binding = _ContextBinding()
    scope = Scope()
    owner = object()
    binding.set(scope, None, token=owner)
    assert binding.get(scope, local=True) is not _MISSING
    with pytest.raises(ValueError, match="token"):
        if operation == "set":
            binding.set(scope, "replacement", token=supplied_token)
        else:
            binding.delete(scope, token=supplied_token)
    assert binding.matches(scope, token=owner)
    assert not binding.matches(scope, token=None)
    assert binding.get(scope) is None
    binding.set(scope, "updated", token=owner)
    assert binding.get(scope) == "updated"
    binding.delete(scope, token=owner)
    assert not binding.matches(scope, token=owner)
    assert binding.get(scope, local=True) is _MISSING
    binding.clear()


def test_tokens_match_by_identity_not_equality() -> None:
    class Token:
        def __eq__(self, other: object) -> bool:
            return True

    binding = _ContextBinding()
    scope = Scope()
    owner = Token()
    binding.set(scope, "protected", token=owner)
    other = Token()
    assert owner == other
    assert not binding.matches(scope, token=other)
    with pytest.raises(ValueError, match="token"):
        binding.set(scope, "replacement", token=other)
    with pytest.raises(ValueError, match="token"):
        binding.delete(scope, token=other)
    assert binding.get(scope) == "protected"
    binding.clear()


def test_binding_local_get_and_matches_neither_inherit_nor_create_an_identity() -> None:
    binding = _ContextBinding()
    parent = Scope()
    child = parent.fork()
    token = object()
    binding.set(parent, "inherited", token=token)
    assert binding.get(child) == "inherited"
    assert binding.get(child, local=True) is _MISSING
    assert not binding.matches(child, token=token)
    assert not binding.restore_scope(child)
    binding.release_scope(child)
    assert child not in binding._scope_identities
    binding.clear()


@pytest.mark.parametrize("supplied_token", [None, object()])
def test_unprotected_values_accept_any_token(supplied_token: object | None) -> None:
    binding = _ContextBinding()
    scope = Scope()
    binding.delete(scope, token=supplied_token)
    assert scope not in binding._scope_identities
    assert not binding.matches(scope, token=None)
    binding.set_blocked(scope, blocked=True)
    binding.set(scope, "first")
    assert binding.matches(scope, token=None)
    assert not binding.matches(scope, token=object())
    binding.set(scope, "second", token=supplied_token)
    assert binding.matches(scope, token=supplied_token)
    binding.delete(scope, token=supplied_token)
    binding.set(scope, "unprotected")
    binding.delete(scope, token=supplied_token)
    assert binding.get(scope, local=True) is _MISSING
    assert scope in binding._scope_identities
    assert binding.get(scope) is _MISSING
    binding.clear()


def test_shared_identity_enforces_the_same_token_from_every_scope() -> None:
    binding = _ContextBinding()
    left, right = Scope(), Scope()
    binding.bind(left, identity="shared")
    binding.set_blocked(left, blocked=True)
    binding.bind(right, identity="shared")
    binding.set_blocked(right, blocked=True)
    owner = object()
    binding.set(left, "original", token=owner)
    assert binding.matches(right, token=owner)
    with pytest.raises(ValueError, match="token"):
        binding.delete(right)
    binding.set(right, "updated", token=owner)
    assert binding.get(left) == "updated"
    binding.delete(right, token=owner)
    assert binding.get(left, local=True) is _MISSING
    binding.clear()


def test_binding_clear_detaches_identities_before_value_finalization() -> None:
    binding = _ContextBinding()
    scope = Scope()
    events: list[str] = []

    class Payload:
        def __del__(self) -> None:
            assert binding.scopes == ()
            assert scope not in binding._scope_identities
            binding.bind(scope, identity="replacement")
            binding.set_blocked(scope, blocked=True)
            binding.set(scope, "new")
            events.append("replaced")

    binding.bind(scope, identity="original")
    binding.set_blocked(scope, blocked=True)
    binding.set(scope, Payload())
    binding.clear()
    assert events == ["replaced"]
    assert binding.get(scope) == "new"
    assert binding.scopes == (scope,)
    binding.clear()


@pytest.mark.parametrize("remove_first", [False, True])
def test_registration_disposer_retains_binding_but_not_store_until_called(
    remove_first: bool,
) -> None:
    class Payload:
        pass

    scope = Scope()
    other_scope = scope.fork()
    schema = Schema({"value": Schema.leaf(mode="register")})
    store = ContextStore(schema)
    store.acquire_scope(object(), scope)
    store.acquire_scope(object(), other_scope)
    payload = Payload()
    other_payload = Payload()
    payload_ref = weakref.ref(payload)
    other_ref = weakref.ref(other_payload)
    remove = store.register(scope, schema.resolve_entry("value"), payload)
    store.register(other_scope, schema.resolve_entry("value"), other_payload)
    binding = store._data[schema.resolve_entry("value")]
    binding_ref = weakref.ref(binding)
    store_ref = weakref.ref(store)
    if remove_first:
        remove()

    del binding, store, schema, payload, other_payload
    gc.collect()
    assert (binding_ref() is None) is remove_first
    assert store_ref() is None
    assert (payload_ref() is None) is remove_first
    assert (other_ref() is None) is remove_first

    remove()
    gc.collect()
    assert binding_ref() is None
    assert payload_ref() is None
    assert other_ref() is None
    remove()


def test_registration_disposer_replays_failure_without_repeating_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema = Schema({"value": Schema.leaf(mode="register")})
    store = ContextStore(schema)
    scope, viewer = Scope(), object()
    store.acquire_scope(viewer, scope)
    remove = store.register(scope, schema.resolve_entry("value"), "installed")
    binding = store._data[schema.resolve_entry("value")]
    original = _ContextBinding.delete
    failure = ValueError("cleanup failed")
    calls = []

    def fail(self, scope, *, token=None):
        calls.append(scope)
        original(self, scope, token=token)
        raise failure

    with monkeypatch.context() as patch:
        patch.setattr(_ContextBinding, "delete", fail)
        for _ in range(2):
            with pytest.raises(ValueError) as caught:
                remove()
            assert caught.value is failure
    assert calls == [scope]
    assert binding.get(scope, local=True) is _MISSING
    store.release_scope(viewer, scope)
    store.dispose()
    binding_ref = weakref.ref(binding)
    del binding, store, schema, caught
    # Cached exceptions retain method frames independently of the disposer closure.
    failure.__traceback__ = None
    gc.collect()
    assert binding_ref() is None


def test_registration_disposer_skips_replacement_with_an_unprotected_token() -> None:
    schema = Schema({"value": Schema.leaf(mode="register")})
    store = ContextStore(schema)
    scope, viewer = Scope(), object()
    store.acquire_scope(viewer, scope)
    remove = store.register(scope, schema.resolve_entry("value"), "old")
    binding = store._data[schema.resolve_entry("value")]
    binding.clear()
    binding.set(scope, "replacement")
    remove()
    remove()
    assert binding.get(scope) == "replacement"
    store.release_scope(viewer, scope)
    store.dispose()


@pytest.mark.parametrize("withdraw", ["schema", "scope"])
def test_registration_data_is_released_before_a_retained_disposer_runs(
    withdraw: str,
) -> None:
    class Payload:
        pass

    schema = Schema()
    undeclare = schema.declare({"value": Schema.leaf(mode="register")})
    store = ContextStore(schema)
    scope, viewer = Scope(), object()
    store.acquire_scope(viewer, scope)
    payload = Payload()
    reference = weakref.ref(payload)
    remove = store.register(scope, schema.resolve_entry("value"), payload)
    binding_ref = weakref.ref(store._data[schema.resolve_entry("value")])
    del payload

    if withdraw == "schema":
        undeclare()
    else:
        store.release_scope(viewer, scope)
    gc.collect()
    assert reference() is None
    assert binding_ref() is not None
    remove()
    gc.collect()
    if withdraw == "schema":
        assert binding_ref() is None
    else:
        assert binding_ref() is store._data[schema.resolve_entry("value")]
        assert not binding_ref()._data

    if withdraw == "schema":
        store.release_scope(viewer, scope)
    else:
        undeclare()
    store.dispose()
    gc.collect()
    assert binding_ref() is None


def test_registration_rejects_an_existing_unprotected_value() -> None:
    schema = Schema({"value": Schema.leaf(mode="register")})
    store = ContextStore(schema)
    scope, viewer = Scope(), object()
    store.acquire_scope(viewer, scope)
    binding = store._writable_binding(scope, schema.resolve_entry("value"))
    binding.set(scope, "unprotected")
    with pytest.raises(KeyError, match="existing local path"):
        store.register(scope, schema.resolve_entry("value"), "replacement")
    assert binding.get(scope) == "unprotected"
    store.release_scope(viewer, scope)
    store.dispose()


def test_shared_identity_has_one_current_value_and_a_persistent_barrier() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": "root"})
    left = root.fork(scope=root.scope.fork())
    left.bind("value", identity="shared")
    left.set_blocked("value", blocked=True)
    right = root.fork(scope=root.scope.fork())
    right.bind("value", identity="shared")
    right.set_blocked("value", blocked=True)
    left.set("value", "original")
    binding = root._store._data[root.resolve_entry("value")]
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
    writer = root.fork(scope=root.scope.fork())
    writer.bind("value", identity="shared")
    writer.set_blocked("value", blocked=True)
    reader = root.fork(scope=root.scope.fork())
    reader.bind("value", identity="shared")
    reader.set_blocked("value", blocked=True)
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
    writer = root.fork(scope=root.scope.fork())
    writer.bind("value", identity="shared")
    writer.set_blocked("value", blocked=True)
    replacements: list[Context] = []

    class Payload:
        def __del__(self) -> None:
            child = root.fork(scope=root.scope.fork())
            child.bind("value", identity="shared")
            child.set_blocked("value", blocked=True)
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


@pytest.mark.parametrize("value", [None, "child"])
def test_context_get_stops_at_value_or_barrier_without_creating_identities(
    monkeypatch: pytest.MonkeyPatch,
    value: str | None,
) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": "root"})
    left = root.fork(scope=root.scope.fork())
    left.bind("value", identity="shared")
    left.set_blocked("value", blocked=True)
    right = root.fork(scope=root.scope.fork())
    right.bind("value", identity="shared")
    right.set_blocked("value", blocked=True)
    unbound = Scope(parents=(left.scope, right.scope))
    child = root.fork(scope=unbound.fork())
    child.set("value", value)
    binding = root._store._data[root.resolve_entry("value")]
    seen: list[Scope] = []
    original = binding._scope_identities.get
    identities_before = dict(binding._scope_identities)

    def record(scope: Scope, default: Hashable = None) -> Hashable:
        seen.append(scope)
        return original(scope, default)

    with monkeypatch.context() as patch:
        patch.setattr(binding._scope_identities, "get", record)
        assert child.get("value") == value
        assert seen == [child.scope]

        child.delete("value")
        seen.clear()
        assert binding.get(child.scope) is _MISSING
        assert seen == [child.scope, unbound, left.scope]

        seen.clear()
        assert binding.get(child.scope, local=True) is _MISSING
        assert seen == [child.scope]
    assert dict(binding._scope_identities) == identities_before
    root.dispose()
