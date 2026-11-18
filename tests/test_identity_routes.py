from __future__ import annotations

import gc
import weakref
from dataclasses import replace

import pytest
from hypothesis import given
from hypothesis import strategies as st

from slyme.context import (
    Compose,
    Context,
    ContextPathError,
    Identity,
    Schema,
    Scope,
    ScopeBinding,
)
from slyme.context.store import _MISSING, _ContextBinding
from tests.compose_helpers import ValueLayer, collect_values


def test_binding_requires_a_write_target_and_preserves_explicit_order() -> None:
    with pytest.raises(ValueError, match="writable Identity"):
        ScopeBinding(())
    a, b = Identity(), Identity()
    binding = ScopeBinding((a, b))
    assert binding.identity is a
    assert binding.identities == (a, b)
    assert binding != ScopeBinding((b, a))


def test_binding_normalizes_equivalent_routes_without_changing_the_write_target() -> (
    None
):
    first, same_label = Identity("shared"), Identity("shared")
    barrier, unreachable = Identity(blocked=True), Identity()
    binding = ScopeBinding((first, same_label, first, barrier, unreachable))
    canonical = ScopeBinding((first, same_label, barrier))
    assert binding.identities == (first, same_label, barrier)
    assert binding.identity is first
    assert binding == canonical
    assert hash(binding) == hash(canonical)
    assert replace(binding, identities=barrier).identities == (barrier,)
    assert replace(binding, blocked=True) != binding

    scope = Scope()
    store = _ContextBinding()
    compose = Compose(factory=ValueLayer, query=collect_values)
    for bind in (store.bind, compose._bind):
        bind(scope, binding)
        bind(scope, canonical)
    store.set(scope, "value")
    remove = compose.register(scope, "value")
    assert store.get(scope) == "value"
    assert compose.resolve(scope) == ("value",)
    remove()
    store.clear()


def test_binding_does_not_retain_unreachable_identities() -> None:
    barrier, unreachable = Identity(blocked=True), Identity()
    retained = weakref.ref(unreachable)
    binding = ScopeBinding((barrier, unreachable))
    del unreachable
    gc.collect()
    assert retained() is None
    assert binding.identities == (barrier,)
    assert binding.identity is barrier


def test_binding_consumes_iterables_only_through_the_first_barrier() -> None:
    local, barrier = Identity(), Identity(blocked=True)

    def route():
        yield local
        yield local
        yield barrier
        raise AssertionError("Unreachable tail must not be consumed")

    assert ScopeBinding(route()) == ScopeBinding((local, barrier))
    source = [local, barrier]
    binding = ScopeBinding(source)
    source.clear()
    assert binding.identities == (local, barrier)
    with pytest.raises(ValueError, match="writable Identity"):
        ScopeBinding(iter(()))


def test_derive_accepts_iterable_bindings_for_context_and_compose() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    values = Compose(factory=ValueLayer, query=collect_values)
    local, shared = Identity(), Identity()
    provider = root.derive(bindings={"value": shared, values: shared})
    provider.set("value", "shared")
    provider.effect(lambda: values.register(provider.scope, "shared"))
    consumer = root.derive(
        bindings={"value": [local, shared], values: iter((local, shared))}
    )
    assert consumer.get("value") == "shared"
    assert values.resolve(consumer.scope) == ("shared",)
    consumer.set("value", "local")
    consumer.effect(lambda: values.register(consumer.scope, "local"))
    assert provider.get("value") == "shared"
    assert values.resolve(provider.scope) == ("shared",)
    for scope in (
        values.derive(binding=iter((local, shared))),
        Compose.derive_many(bindings={values: [local, shared]}),
    ):
        assert values.resolve(scope) == ("local", "shared")
    root.dispose()
    assert values.resolve(scope) == ()


@pytest.mark.parametrize("restore", [False, True])
def test_full_binding_retention_after_a_local_write(restore: bool) -> None:
    store = _ContextBinding()
    writer, reader = Scope(), Scope()
    local, shared = Identity(), Identity()
    config = ScopeBinding((local, shared))
    store.bind(writer, ScopeBinding(shared))
    store.set(writer, "shared")
    store.bind(reader, config)
    store.release_scope(reader)

    store.set(reader, "local")
    assert store._data[local].scopes == {reader}
    assert store._data[shared].scopes == {writer}
    if restore:
        assert store.restore_scope(reader)
        assert store.restore_scope(reader)
    else:
        store.bind(reader, config)
        store.bind(reader, config)
    store.release_scope(writer)
    store.delete(reader)
    assert store.get(reader) == "shared"
    store.release_scope(reader)
    assert not store._data


def test_context_routes_are_per_path_with_independent_precedence() -> None:
    root = Context()
    root.declare({"x": Schema.leaf(), "y": Schema.leaf(), "extra": Schema.leaf()})
    a, b, local = Identity(), Identity(), Identity()
    left = root.derive(bindings={"x": a, "y": a})
    right = root.derive(bindings={"x": b, "y": b})
    left.set_many({"x": "a", "y": "a", "extra": "private"})
    right.set_many({"x": "b", "y": "b"})
    child = root.derive(
        bindings={
            "x": ScopeBinding((local, a, b)),
            "y": ScopeBinding((local, b, a)),
        }
    )
    assert child.get("x") == "a"
    assert child.get("y") == "b"
    assert not child.exists("extra")
    assert not child.exists("x", local=True)
    child.delete("x")
    assert child.get("x") == "a"
    child.set("x", "local")
    assert child.get("x", local=True) == "local"
    assert left.get("x") == "a"
    assert right.get("x") == "b"
    child.delete("x")
    left.delete("x")
    assert child.get("x") == "b"
    right.delete("x")
    root.set("x", "root")
    assert child.get("x") == "root"
    root.declare({"later": Schema.leaf()})
    root.set("later", "inherited")
    assert child.get("later") == "inherited"
    root.dispose()


def test_imported_registration_does_not_prevent_a_local_registration() -> None:
    root = Context()
    root.declare({"service": Schema.leaf(mode="register")})
    shared = Identity()
    provider = root.derive(bindings={"service": shared})
    reader = root.derive(bindings={"service": ScopeBinding((Identity(), shared))})
    remove_shared = provider.register("service", "shared")
    assert reader.get("service") == "shared"
    remove_local = reader.register("service", "local")
    with pytest.raises(ContextPathError, match="existing local"):
        reader.register("service", "duplicate")
    assert provider.get("service") == "shared"
    remove_local()
    assert reader.get("service") == "shared"
    remove_shared()
    assert not reader.exists("service")
    provider.register("service", "replacement")
    assert reader.get("service") == "replacement"
    provider.dispose()
    assert not reader.exists("service")
    root.dispose()


@pytest.mark.parametrize("identity_blocked", [False, True])
def test_barriers_in_lists_agree_between_context_and_compose(identity_blocked) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "root")
    values = Compose(factory=ValueLayer, query=collect_values)
    root.effect(lambda: values.register(root.scope, "root"))
    a, b = Identity(blocked=identity_blocked), Identity()
    left = root.derive(bindings={"value": a, values: a})
    right = root.derive(bindings={"value": b, values: b})
    right.set("value", "b")
    right.effect(lambda: values.register(right.scope, "b"))
    config = ScopeBinding((Identity(), a, b), blocked=True)
    reader = root.derive(bindings={"value": config, values: config})
    assert reader.get("value", None) == (None if identity_blocked else "b")
    assert values.resolve(reader.scope) == (() if identity_blocked else ("b",))
    left.set("value", "a")
    remove = left.effect(lambda: values.register(left.scope, "a"))
    assert reader.get("value") == "a"
    assert values.resolve(reader.scope) == (("a",) if identity_blocked else ("a", "b"))
    left.delete("value")
    remove()
    right.delete("value")
    right.dispose()
    assert not reader.exists("value")
    assert values.resolve(reader.scope) == ()
    root.dispose()


def test_compose_imports_are_read_fallbacks_and_repeated_identities_are_deduplicated() -> (
    None
):
    values = Compose(factory=ValueLayer, query=collect_values)
    a, b = Identity(), Identity()
    parent = values.derive(binding=a)
    provider = values.derive(binding=b)
    remove_a = values.register(parent, "a")
    remove_b = values.register(provider, "b")
    reader = values.derive(
        scope_parent=parent, binding=ScopeBinding((Identity(), b, a, b))
    )
    assert values.resolve(reader) == ("b", "a")
    assert values.resolve(reader, local=True) == ()
    remove_local = values.register(reader, "local")
    assert values.resolve(reader) == ("local", "b", "a")
    assert values.resolve(reader, local=True) == ("local",)
    assert values.resolve(provider) == ("b",)
    remove_b()
    assert values.resolve(reader) == ("local", "a")
    remove_local()
    remove_a()
    assert values.resolve(reader) == ()
    assert not values._buckets


def test_reordered_binding_cannot_replace_existing_binding() -> None:
    scope = Scope()
    a, b = Identity(), Identity()
    store = _ContextBinding()
    compose = Compose(factory=ValueLayer, query=collect_values)
    for bind in (store.bind, compose._bind):
        bind(scope, ScopeBinding((a, b)))
        bind(scope, ScopeBinding((a, b)))
        with pytest.raises(ValueError, match="immutable"):
            bind(scope, ScopeBinding((b, a)))
    store.clear()


def test_imports_after_a_blocked_identity_do_not_retain_unreachable_data() -> None:
    class Payload:
        pass

    root = Context()
    root.declare({"value": Schema.leaf()})
    blocked, unreachable = Identity(blocked=True), Identity()
    writer = root.derive(bindings={"value": unreachable})
    value = Payload()
    retained = weakref.ref(value)
    writer.set("value", value)
    reader = root.derive(
        bindings={"value": ScopeBinding((Identity(), blocked, unreachable))}
    )
    writer.dispose()
    del value
    gc.collect()
    assert retained() is None
    assert not reader.exists("value")
    root.dispose()


def test_restoring_a_scope_restores_all_imports_without_reviving_expired_values() -> (
    None
):
    root = Context()
    root.declare({"value": Schema.leaf()})
    a, b = Identity(), Identity()
    writer = root.derive(bindings={"value": b})
    reader = root.derive(bindings={"value": ScopeBinding((a, b, b))})
    saved = reader.scope
    reader.dispose()
    writer.set("value", "current")
    restored = root.fork(scope=saved)
    writer.dispose()
    assert restored.get("value") == "current"
    restored.dispose()
    binding = root._store._data[root.resolve_entry("value")]
    assert not binding._data
    reopened = root.fork(scope=saved)
    assert not reopened.exists("value")
    root.dispose()
    assert not binding._data


def test_wide_shared_routes_release_all_slots_and_scope_references() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    identities = tuple(Identity() for _ in range(128))
    binding = None
    scope_refs = []
    for _ in range(3):
        readers = [
            root.derive(bindings={"value": ScopeBinding((Identity(), *identities))})
            for _ in range(100)
        ]
        scope_refs.extend(weakref.ref(reader.scope) for reader in readers)
        binding = root._store._data[root.resolve_entry("value")]
        assert len(binding._data) == 228
        for reader in readers:
            reader.dispose()
        del reader, readers
        assert not binding._data
    gc.collect()
    assert all(ref() is None for ref in scope_refs)
    root.dispose()


@given(
    routes=st.lists(
        st.lists(st.integers(0, 7), min_size=1, max_size=12), min_size=3, max_size=3
    ),
    barriers=st.lists(st.booleans(), min_size=3, max_size=3),
    blocked=st.sets(st.integers(0, 7)),
    present=st.sets(st.integers(0, 7)),
)
def test_routes_match_reference_order_after_mutations(
    routes, barriers, blocked, present
) -> None:
    identities = tuple(Identity(blocked=i in blocked) for i in range(8))
    store = _ContextBinding()
    compose = Compose(factory=ValueLayer, query=collect_values)
    owners = [Scope() for _ in identities]
    removers = {}
    for i, owner in enumerate(owners):
        config = ScopeBinding((identities[i],))
        store.bind(owner, config)
        compose._bind(owner, config)
        if i in present:
            store.set(owner, i)
            removers[i] = compose.register(owner, i)
    scope = None
    for route, barrier in reversed(list(zip(routes, barriers, strict=True))):
        scope = Scope(parent=scope)
        config = ScopeBinding(tuple(identities[i] for i in route), blocked=barrier)
        store.bind(scope, config)
        compose._bind(scope, config)
    assert scope is not None

    def expected():
        order = []
        for route, barrier in zip(routes, barriers, strict=True):
            for i in route:
                if i not in order:
                    order.append(i)
                if i in blocked:
                    return [i for i in order if i in present]
            if barrier:
                break
        return [i for i in order if i in present]

    for i in range(9):
        order = expected()
        assert compose.resolve(scope) == tuple(order)
        assert store.get(scope) == (order[0] if order else _MISSING)
        first = routes[0][0]
        assert store.get(scope, local=True) == (first if first in present else _MISSING)
        assert compose.resolve(scope, local=True) == (
            (first,) if first in present else ()
        )
        if i in removers:
            removers[i]()
            store.delete(owners[i])
            present.remove(i)
    store.clear()
