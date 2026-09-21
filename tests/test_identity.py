import gc
import weakref
from dataclasses import FrozenInstanceError

import pytest

from slyme.context import Compose, Context, Identity, Schema, Scope, ScopeBinding
from slyme.context.store import _MISSING, _ContextBinding
from tests.compose_helpers import ValueLayer, collect_values


def test_identity_uses_object_identity_and_has_frozen_configuration() -> None:
    left = Identity("shared", blocked=True)
    right = Identity("shared", blocked=True)
    assert left != right
    assert len({left, right}) == 2
    with pytest.raises(FrozenInstanceError):
        left.blocked = False  # type: ignore[misc]


def test_scope_binding_is_a_frozen_comparable_value() -> None:
    identity = Identity()
    config = ScopeBinding(identity)
    assert config == ScopeBinding(identity)
    assert config != ScopeBinding(identity, blocked=True)
    assert config != ScopeBinding(Identity())
    assert ScopeBinding().identity is not ScopeBinding().identity
    assert not config.blocked
    assert not config.identity.blocked
    with pytest.raises(FrozenInstanceError):
        config.blocked = True  # type: ignore[misc]


@pytest.mark.parametrize("blocked", [False, True])
def test_binding_policy_survives_payload_cleanup(blocked: bool) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    identity = Identity()
    config = ScopeBinding(identity, blocked=blocked)
    child = root.derive(bindings={"value": config})
    store = root._store
    entry = root.resolve_entry("value")
    child.set("value", "value")
    child.delete("value")
    for ctx in (child, root.fork(scope=child.scope)):
        store.bind(ctx.scope, entry, config)
        store.bind(ctx.scope, entry, ScopeBinding(identity, blocked=blocked))
        with pytest.raises(ValueError, match="immutable"):
            store.bind(ctx.scope, entry, ScopeBinding(Identity(), blocked=blocked))
        with pytest.raises(ValueError, match="immutable"):
            store.bind(ctx.scope, entry, ScopeBinding(identity, blocked=not blocked))
        ctx.dispose()
    restored = root.fork(scope=child.scope)
    store.bind(restored.scope, entry, config)
    with pytest.raises(ValueError, match="immutable"):
        store.bind(restored.scope, entry, ScopeBinding(identity, blocked=not blocked))
    root.dispose()


def test_implicit_first_write_also_fixes_binding_policy() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", 1)
    with pytest.raises(ValueError, match="immutable"):
        root._store.bind(
            root.scope,
            root.resolve_entry("value", role="leaf"),
            ScopeBinding(blocked=True),
        )
    values = Compose(factory=ValueLayer, query=collect_values)
    remove = values.register(root.scope, 1)
    remove()
    with pytest.raises(ValueError, match="immutable"):
        values._bind(root.scope, ScopeBinding(blocked=True))
    root.dispose()


@pytest.mark.parametrize("level", ["scope", "identity"])
def test_empty_binding_stops_lookup_without_any_identity_data(level: str) -> None:
    root = Scope()
    child = root.fork()
    binding = _ContextBinding()
    binding.set(root, "parent")
    identity = Identity(blocked=level == "identity")
    binding.bind(child, ScopeBinding(identity, blocked=level == "scope"))
    binding.release_scope(child)
    assert identity not in binding._data
    assert binding.get(child) is _MISSING
    binding.clear()


@pytest.mark.parametrize("level", ["scope", "identity"])
def test_compose_barriers_survive_empty_buckets(level: str) -> None:
    root = Scope()
    identity = Identity(blocked=level == "identity")
    values = Compose(factory=ValueLayer, query=collect_values)
    values.register(root, "parent")
    config = ScopeBinding(identity, blocked=level == "scope")
    left = values.derive(parents=root, binding=config)
    right = values.derive(parents=root, binding=ScopeBinding(identity, blocked=False))
    assert identity not in values._buckets
    assert values.resolve(left) == ()
    expected = ("parent",) if level == "scope" else ()
    assert values.resolve(right) == expected
    remove = values.register(right, "shared")
    assert values.resolve(left) == ("shared",)
    assert values.resolve(left, local=True) == ("shared",)
    remove()
    assert identity not in values._buckets
    assert values.resolve(left) == ()
    assert values.resolve(right) == expected
    values._bind(left, config)
    with pytest.raises(ValueError, match="immutable"):
        values._bind(left, ScopeBinding(identity, blocked=level != "scope"))
    with pytest.raises(ValueError, match="immutable"):
        values._bind(left, ScopeBinding(Identity(), blocked=level == "scope"))


def test_compose_deduplicates_shared_data_but_still_checks_each_scope_barrier() -> None:
    root = Scope()
    identity = Identity()
    values = Compose(factory=ValueLayer, query=collect_values)
    left = values.derive(parents=root, binding=ScopeBinding(identity, blocked=False))
    right = values.derive(parents=root, binding=ScopeBinding(identity, blocked=True))
    child = Scope(parents=(left, right))
    values.register(root, "parent")
    remove = values.register(left, "shared")
    assert values.resolve(child) == ("shared",)
    assert tuple(tuple(layer.values()) for layer in values.layers(child)) == (
        ("shared",),
    )
    assert len(tuple(values.layers())) == 2
    remove()
    assert values.resolve(child) == ()
    assert values.resolve(child, local=True) == ()


def test_identity_configuration_is_shared_but_data_is_container_local() -> None:
    identity = Identity(blocked=True)
    scope = Scope()
    left_root, right_root = Context(scope=scope), Context(scope=scope)
    left_root.declare({"value": Schema.leaf()})
    right_root.declare({"value": Schema.leaf()})
    left = left_root.derive(bindings={"value": ScopeBinding(identity, blocked=True)})
    right = right_root.derive(bindings={"value": ScopeBinding(identity, blocked=True)})
    left.set("value", "left")
    assert not right.exists("value")
    first, second = (
        Compose(factory=ValueLayer, query=collect_values),
        Compose(factory=ValueLayer, query=collect_values),
    )
    first_scope = first.derive(
        parents=scope, binding=ScopeBinding(identity, blocked=True)
    )
    second_scope = second.derive(
        parents=scope, binding=ScopeBinding(identity, blocked=True)
    )
    first.register(first_scope, "first")
    assert second.resolve(second_scope) == ()
    left_root.dispose()
    right_root.dispose()


def test_fresh_scope_plugin_reload_releases_contributions_and_payloads() -> None:
    class Payload:
        pass

    root = Context()
    root.declare({"service": Schema.leaf(mode="register"), "state": Schema.leaf()})
    root.register("service", "default")
    hooks = Compose(factory=ValueLayer, query=collect_values)
    closed: list[int] = []
    for generation in range(20):
        plugin = root.derive(bindings={"service": ScopeBinding(blocked=True)})
        scope_ref = weakref.ref(plugin.scope)
        payload = Payload()
        payload_ref = weakref.ref(payload)
        plugin.set("state", payload)
        plugin.register("service", payload)
        plugin.declare({"temporary": Schema.leaf()})
        plugin.set("temporary", payload)
        plugin.effect(
            lambda ctx=plugin, value=payload: hooks.register(ctx.scope, value)
        )
        plugin.effect(lambda item=generation: lambda: closed.append(item))
        assert hooks.resolve(plugin.scope) == (payload,)
        plugin.dispose()
        assert root.get("service") == "default"
        assert not root.exists("state")
        assert not tuple(hooks.layers())
        assert "temporary" not in {entry.ref.path for entry in root.entries}
        del plugin, payload
        gc.collect()
        assert scope_ref() is None
        assert payload_ref() is None
    assert closed == list(range(20))
    root.dispose()


def test_shared_identity_retains_assignments_but_not_owned_registrations() -> None:
    root = Context()
    root.declare({"state": Schema.leaf(), "service": Schema.leaf(mode="register")})
    identity = Identity()
    plugin = root.derive(
        bindings={
            "state": ScopeBinding(identity, blocked=True),
            "service": ScopeBinding(identity, blocked=True),
        }
    )
    viewer = root.derive(
        bindings={
            "state": ScopeBinding(identity, blocked=True),
            "service": ScopeBinding(identity, blocked=True),
        }
    )
    plugin.set("state", "old state")
    plugin.register("service", "old service")
    plugin.dispose()
    replacement = root.derive(
        bindings={
            "state": ScopeBinding(identity, blocked=True),
            "service": ScopeBinding(identity, blocked=True),
        }
    )
    assert replacement.get("state") == "old state"
    assert not replacement.exists("service")
    fresh = root.derive(
        bindings={
            "state": ScopeBinding(blocked=True),
            "service": ScopeBinding(blocked=True),
        }
    )
    assert not fresh.exists("state")
    viewer.dispose()
    replacement.dispose()
    reused = root.derive(bindings={"state": ScopeBinding(identity, blocked=True)})
    assert not reused.exists("state")
    root.dispose()
