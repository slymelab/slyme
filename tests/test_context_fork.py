from __future__ import annotations

from typing import Literal

import pytest

from slyme.context import Compose, Context, Identity, Ref, Schema, Scope, ScopeBinding
from slyme.context.default import DATA_TREE_REF
from slyme.context.schema import ContextPathError
from tests.compose_helpers import ValueLayer, collect_values


@pytest.mark.parametrize("mode", ["scope", "identity"])
def test_binding_configuration_survives_all_viewer_lifetimes(
    mode: Literal["scope", "identity"],
) -> None:
    shared_identity = Identity("shared", blocked=mode == "identity")
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    owner = root.derive(
        bindings={"value": ScopeBinding(shared_identity, blocked=mode == "scope")}
    )
    scope = owner.scope
    viewer = root.fork(scope=scope)
    peer = root.derive(bindings={"value": ScopeBinding(shared_identity, blocked=False)})
    binding = root._store._data[root.resolve_entry("value")]

    owner.dispose()
    assert not viewer.exists("value")
    assert binding._data[shared_identity].scopes == {scope, peer.scope}
    viewer.dispose()
    assert binding._scope_bindings[scope].blocked == (mode == "scope")
    assert binding._data[shared_identity].scopes == {peer.scope}

    restored = root.fork(scope=scope)
    expected = "parent" if mode == "scope" else None
    assert not restored.exists("value")
    assert peer.get("value", None) == expected
    assert binding._scope_bindings[scope].identity is shared_identity
    restored.dispose()
    peer.dispose()
    assert shared_identity not in binding._data

    reused = root.fork(scope=scope)
    assert not reused.exists("value")
    assert binding._scope_bindings[scope].identity is shared_identity
    fresh = root.fork(scope=root.scope.fork())
    assert fresh.get("value") == "parent"
    root.dispose()


def test_both_barriers_can_coexist_without_resetting_each_other() -> None:
    shared_identity = Identity("shared", blocked=True)
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    left = root.derive(bindings={"value": ScopeBinding(shared_identity, blocked=True)})
    right = root.derive(bindings={"value": ScopeBinding(shared_identity, blocked=True)})
    binding = root._store._data[root.resolve_entry("value")]
    assert binding._scope_bindings[left.scope].blocked
    assert binding._scope_bindings[left.scope].identity.blocked
    right.set("value", "local")
    assert left.get("value") == "local"
    left.delete("value")
    left.dispose()
    assert not right.exists("value")
    assert shared_identity.blocked
    root.dispose()


@pytest.mark.parametrize("mode", ["assign", "register"])
def test_derive_combines_per_field_identities_and_local_barriers(
    mode: Literal["assign", "register"],
) -> None:
    shared_identity = Identity("shared")
    root = Context()
    root.declare(
        {
            "first": Schema.leaf(mode=mode),
            "second": Schema.leaf(mode=mode),
            "other": Schema.leaf(),
        }
    )
    write_root = root.set if mode == "assign" else root.register
    write_root("first", "root first")
    write_root("second", "root second")
    root.set("other", "inherited")
    second = Ref("second")
    left = root.derive(
        bindings={
            "first": ScopeBinding(blocked=True),
            second: ScopeBinding(shared_identity, blocked=True),
        }
    )
    right = root.derive(
        bindings={
            "first": ScopeBinding(blocked=True),
            second: ScopeBinding(shared_identity, blocked=True),
        }
    )
    observer = root.derive(
        bindings={second: ScopeBinding(shared_identity, blocked=False)}
    )

    assert left.parent is root
    assert left.scope.parents == (root.scope,)
    assert left.scope is not right.scope
    assert left.get(DATA_TREE_REF) is root.get(DATA_TREE_REF)
    assert left.get("other") == "inherited"
    assert not left.exists("first")
    assert not left.exists(second)
    assert observer.get(second) == "root second"

    write_left = left.set if mode == "assign" else left.register
    write_left("first", "private")
    write_left(second, "shared value")
    assert not right.exists("first")
    assert right.get(second) == observer.get(second) == "shared value"
    left.dispose()
    if mode == "assign":
        assert right.get(second) == "shared value"
        right.delete(second)
    else:
        assert not right.exists(second)
    assert not right.exists(second)
    assert observer.get(second) == "root second"
    assert root.get(second) == "root second"
    root.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        right.get("other")


def test_derive_allocates_distinct_identities_for_separate_bindings() -> None:
    root = Context()
    root.declare({"first": Schema.leaf(), "second": Schema.leaf()})
    child = root.derive(
        bindings={
            "first": ScopeBinding(blocked=True),
            "second": ScopeBinding(blocked=True),
        }
    )
    identities = [
        root._store._data[root.resolve_entry(path)]
        ._scope_bindings[child.scope]
        .identity
        for path in ("first", "second")
    ]
    assert identities[0] is not identities[1]
    nested = child.derive(bindings={"first": ScopeBinding(blocked=True)})
    child.set("first", "outer")
    child.set("second", "inherited")
    assert not nested.exists("first")
    assert nested.get("second") == "inherited"
    root.dispose()


@pytest.mark.parametrize("path", ["missing", "group", ""])
def test_derive_validates_all_paths_before_creating_a_child(path: str) -> None:
    root = Context()
    root.declare({"group": {"value": Schema.leaf()}})
    children = tuple(root._lifecycle._owned)
    bindings = dict(root._store._data)
    with pytest.raises(ContextPathError):
        root.derive(
            bindings={
                "group.value": ScopeBinding(blocked=True),
                path: ScopeBinding(blocked=True),
            }
        )
    assert tuple(root._lifecycle._owned) == children
    assert root._store._data == bindings
    root.dispose()


def test_derive_releases_partial_bindings_on_conflict() -> None:
    shared_identity = Identity("shared")
    root = Context()
    root.declare({"value": Schema.leaf()})
    peer = root.derive(bindings={"value": ScopeBinding(shared_identity, blocked=False)})
    peer.set("value", "retained")
    binding = root._store._data[root.resolve_entry("value")]
    children = tuple(root._lifecycle._owned)
    with pytest.raises(ValueError, match="immutable"):
        root.derive(
            bindings={
                "value": ScopeBinding(shared_identity, blocked=True),
                Ref("value"): ScopeBinding(),
            }
        )
    assert tuple(root._lifecycle._owned) == children
    assert binding._data[shared_identity].scopes == {peer.scope}
    assert not shared_identity.blocked
    assert peer.get("value") == "retained"
    root.dispose()


def test_empty_derive_still_creates_a_scope_and_lifetime() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    child = root.derive(bindings={})
    assert child.parent is root
    assert child.scope.parents == (root.scope,)
    assert child.get("value") == "parent"
    child.set("value", "child")
    assert root.get("value") == "parent"
    root.dispose()


@pytest.mark.parametrize("mode", ["scope", "identity"])
def test_schema_withdrawal_removes_bindings_but_not_identity_configuration(
    mode: Literal["scope", "identity"],
) -> None:
    root = Context()
    withdraw = root.declare({"value": Schema.leaf()})
    identity = Identity(blocked=mode == "identity")
    child = root.derive(bindings={"value": ScopeBinding(identity, blocked=True)})
    binding = root._store._data[root.resolve_entry("value")]
    withdraw()
    assert not binding._data
    assert not binding._scope_bindings
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    assert child.get("value") == "parent"
    configured = child.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    assert configured.get("value", None) == (None if mode == "identity" else "parent")
    root.dispose()


def test_fork_distinguishes_shared_supplied_and_new_scopes() -> None:
    root = Context()
    shared = root.fork()
    explicit_none = root.fork(scope=None)
    configured = root.derive(bindings={})
    supplied = root.fork(scope=configured.scope)
    assert shared.scope is explicit_none.scope is root.scope
    assert configured.scope.parents == (root.scope,)
    assert supplied.scope is configured.scope
    assert all(
        child.parent is root for child in (shared, explicit_none, configured, supplied)
    )
    root.dispose()


def test_derive_rejects_inconsistent_parents_before_registering_a_child() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    left, right = Scope(), Scope()
    xy = Scope(parents=(left, right))
    yx = Scope(parents=(right, left))
    values = Compose(factory=ValueLayer, query=collect_values)
    children = tuple(root._lifecycle._owned)
    data = dict(root._store._data)
    with pytest.raises(TypeError, match="consistent Scope C3"):
        root.derive(
            parents=(xy, yx),
            bindings={
                "value": ScopeBinding(blocked=True),
                values: ScopeBinding(blocked=True),
            },
        )
    assert tuple(root._lifecycle._owned) == children
    assert root._store._data == data
    assert not values._scope_bindings
    root.dispose()


@pytest.mark.parametrize("single", [False, True])
def test_derive_selects_visibility_parents_independently_of_lifecycle(
    single: bool,
) -> None:
    root = Context()
    root.declare({"value": Schema.leaf(), "other": Schema.leaf()})
    owner = root.fork(scope=root.scope.fork())
    owner.set("other", "owner")
    provider = root.fork(scope=root.scope.fork())
    provider.set("other", "provider")
    original = provider.scope.parents
    identity = Identity()
    child = owner.derive(
        label="derived",
        parents=provider.scope if single else (provider.scope,),
        bindings={"value": ScopeBinding(identity, blocked=True)},
    )
    assert child.parent is owner
    assert child.root is root
    assert child.scope is not provider.scope
    assert child.scope.parents == (provider.scope,)
    assert child.scope.label == "derived"
    assert child.get("other") == "provider"
    assert provider.scope.parents == original
    child.set("value", "owned")
    binding = root._store._data[root.resolve_entry("value")]
    assert binding._data[identity].scopes == {child.scope}
    owner.dispose()
    assert identity not in binding._data
    with pytest.raises(RuntimeError, match="disposed"):
        child.get("value")
    assert provider.get("other") == "provider"
    root.dispose()


@pytest.mark.parametrize("options", [{}, {"bindings": None}, {"bindings": {}}])
def test_derive_without_bindings_creates_an_owned_inheriting_scope(options) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    child = root.derive(**options)
    explicit = root.fork(scope=root.scope.fork())
    assert child.parent is root
    assert child.root is root
    assert child.scope is not root.scope
    assert child.scope.parents == (root.scope,)
    assert child.scope.mro == (child.scope, root.scope)
    assert child.get("value") == explicit.get("value") == "parent"
    assert not child.exists("value", local=True)
    assert child.get(DATA_TREE_REF) is root.get(DATA_TREE_REF)

    child.set("value", "child")
    assert root.get("value") == explicit.get("value") == "parent"
    child.delete("value")
    root.set("value", "updated")
    assert child.get("value") == explicit.get("value") == "updated"
    released = []
    child.effect(lambda: lambda: released.append("child"))
    root.dispose()
    assert released == ["child"]
    with pytest.raises(RuntimeError, match="disposed"):
        child.get("value")


def test_derive_without_bindings_accepts_independent_scope_and_label() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    child = root.derive(parents=(), label="independent", bindings=None)
    assert child.parent is root
    assert child.scope.label == "independent"
    assert child.scope.mro == (child.scope,)
    assert not child.exists("value")
    assert child.get(DATA_TREE_REF, None) is None
    child.set("value", "child")
    assert root.get("value") == "parent"
    root.dispose()


def test_derive_uses_c3_parents_and_allows_an_independent_root_scope() -> None:
    root = Context()
    root.declare({"first": Schema.leaf(), "second": Schema.leaf()})
    left = root.fork(scope=root.scope.fork())
    right = root.fork(scope=root.scope.fork())
    left.set("first", "left")
    right.update({"first": "right", "second": "right"})
    child = root.derive(parents=(left.scope, right.scope))
    assert child.scope.mro == (child.scope, left.scope, right.scope, root.scope)
    assert child.get("first") == "left"
    assert child.get("second") == "right"

    independent = root.derive(
        parents=(), label="independent", bindings={"first": ScopeBinding(blocked=True)}
    )
    assert independent.parent is root
    assert independent.scope.parents == ()
    assert independent.scope.mro == (independent.scope,)
    assert independent.get("second", None) is None
    assert independent.get(DATA_TREE_REF, None) is None
    independent.set("first", "private")
    assert independent.get("first") == "private"
    root.dispose()


def test_mixed_targets_share_one_new_scope_without_replacing_compose_fields() -> None:
    root = Context()
    root.declare({"service": Schema.leaf(), "tools": Schema.leaf()})
    root.set("service", "parent service")
    tools, hooks = (
        Compose(factory=ValueLayer, query=collect_values),
        Compose(factory=ValueLayer, query=collect_values),
    )
    root.set("tools", tools)
    root.effect(lambda: tools.register(root.scope, "parent tool"))
    root.effect(lambda: hooks.register(root.scope, "parent hook"))
    shared = Identity("tools")
    config = ScopeBinding(shared, blocked=False)
    child = root.derive(
        bindings={
            Ref("service"): ScopeBinding(blocked=True),
            tools: config,
            hooks: ScopeBinding(blocked=True),
        }
    )
    assert child.scope.mro == (child.scope, root.scope)
    assert child.get("tools") is tools
    assert not child.exists("service")
    assert tools.resolve(child.scope) == ("parent tool",)
    assert hooks.resolve(child.scope) == ()
    assert tools._scope_bindings[child.scope] is config
    assert (
        tools._scope_bindings[child.scope].identity
        is not hooks._scope_bindings[child.scope].identity
    )
    child.effect(lambda: tools.register(child.scope, "child tool"))
    child.effect(lambda: hooks.register(child.scope, "child hook"))
    assert tools.resolve(child.scope) == ("child tool", "parent tool")
    assert tools.resolve(root.scope) == ("parent tool",)
    assert hooks.resolve(child.scope) == ("child hook",)
    child.dispose()
    assert tools.resolve(root.scope) == ("parent tool",)
    assert hooks.resolve(root.scope) == ("parent hook",)
    root.dispose()


def test_path_and_compose_object_targets_configure_different_storage() -> None:
    root = Context()
    root.declare({"tools": Schema.leaf()})
    original = Compose(factory=ValueLayer, query=collect_values)
    root.set("tools", original)
    root.effect(lambda: original.register(root.scope, "parent"))
    child = root.derive(
        bindings={
            "tools": ScopeBinding(blocked=True),
            original: ScopeBinding(blocked=True),
        }
    )
    assert not child.exists("tools")
    assert original.resolve(child.scope) == ()
    replacement = Compose(factory=ValueLayer, query=collect_values)
    child.set("tools", replacement)
    child.effect(lambda: replacement.register(child.scope, "replacement"))
    assert child.get("tools").resolve(child.scope) == ("replacement",)
    assert original.resolve(child.scope) == ()
    assert root.get("tools") is original
    root.dispose()


def test_derive_preflights_paths_before_touching_any_compose() -> None:
    root = Context()
    values = Compose(factory=ValueLayer, query=collect_values)
    children = tuple(root._lifecycle._owned)
    with pytest.raises(ContextPathError):
        root.derive(
            bindings={
                values: ScopeBinding(blocked=True),
                "missing": ScopeBinding(blocked=True),
            }
        )
    assert not values._scope_bindings
    assert tuple(root._lifecycle._owned) == children
    root.dispose()


def test_failed_mixed_configuration_releases_context_data_and_child() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    values = Compose(factory=ValueLayer, query=collect_values)
    children = tuple(root._lifecycle._owned)
    with pytest.raises(ValueError, match="immutable"):
        root.derive(
            bindings={
                values: ScopeBinding(blocked=True),
                "value": ScopeBinding(blocked=True),
                Ref("value"): ScopeBinding(blocked=True),
            }
        )
    assert tuple(root._lifecycle._owned) == children
    assert not root._store._data[root.resolve_entry("value")]._data
    assert not tuple(values.layers())
    root.dispose()


def test_duplicate_path_spellings_accept_identical_binding_values() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    identity = Identity()
    child = root.derive(
        bindings={"value": identity, Ref("value"): ScopeBinding(identity)}
    )
    child.set("value", "child")
    assert child.get("value") == "child"
    root.dispose()


@pytest.mark.parametrize("level", ["none", "scope", "identity"])
def test_context_and_compose_derivations_keep_the_explicit_binding(
    level: str,
) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    values = Compose(factory=ValueLayer, query=collect_values)
    root.effect(lambda: values.register(root.scope, "parent"))
    config = (
        ScopeBinding(blocked=True)
        if level == "scope"
        else ScopeBinding(Identity(blocked=True))
        if level == "identity"
        else ScopeBinding()
    )
    child = root.derive(bindings={"value": config, values: config})
    independent = values.derive(parents=root.scope, binding=config)
    batch = Compose.derive_many(parents=root.scope, bindings={values: config})
    bindings = (
        root._store._data[root.resolve_entry("value")]._scope_bindings[child.scope],
        values._scope_bindings[child.scope],
        values._scope_bindings[independent],
        values._scope_bindings[batch],
    )
    assert all(binding is config for binding in bindings)
    assert child.get("value", None) == ("parent" if level == "none" else None)
    assert values.resolve(independent) == (("parent",) if level == "none" else ())
    assert values.resolve(batch) == values.resolve(independent)
    assert independent.parents == (root.scope,)
    root.dispose()


@pytest.mark.parametrize("blocked", [False, True])
def test_identity_shorthand_keeps_its_policy_without_a_scope_barrier(
    blocked: bool,
) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "parent")
    values = Compose(factory=ValueLayer, query=collect_values)
    root.effect(lambda: values.register(root.scope, "parent"))
    identity = Identity(blocked=blocked)
    child = root.derive(bindings={"value": identity, values: identity})
    peer = root.derive(bindings={"value": ScopeBinding(identity)})
    independent = values.derive(parents=root.scope, binding=identity)
    batch = Compose.derive_many(parents=root.scope, bindings={values: identity})
    bindings = (
        root._store._data[root.resolve_entry("value")]._scope_bindings[child.scope],
        values._scope_bindings[child.scope],
        values._scope_bindings[independent],
        values._scope_bindings[batch],
    )
    assert all(
        binding.identity is identity and not binding.blocked for binding in bindings
    )
    assert child.get("value", None) == (None if blocked else "parent")
    assert values.resolve(independent) == (() if blocked else ("parent",))
    child.set("value", "shared")
    assert peer.get("value") == "shared"
    remove = child.effect(lambda: values.register(child.scope, "shared"))
    expected = ("shared",) if blocked else ("shared", "parent")
    assert values.resolve(independent) == values.resolve(batch) == expected
    child.delete("value")
    remove()
    assert peer.get("value", None) == (None if blocked else "parent")
    assert values.resolve(batch) == (() if blocked else ("parent",))
    root.dispose()


@pytest.mark.parametrize("mode", ["assign", "register"])
def test_default_binding_falls_back_after_local_value_removal(
    mode: Literal["assign", "register"],
) -> None:
    root = Context()
    root.declare({"value": Schema.leaf(mode=mode)})
    write = root.set if mode == "assign" else root.register
    write("value", "parent")
    child = root.derive(bindings={"value": ScopeBinding()})
    sibling = root.derive(bindings={"value": ScopeBinding()})
    assert child.get("value") == "parent"
    assert not child.exists("value", local=True)
    if mode == "assign":
        child.set("value", "child")
        assert child.get("value") == "child"
        child.delete("value")
    else:
        remove = child.register("value", "child")
        assert child.get("value") == "child"
        remove()
    assert child.get("value") == sibling.get("value") == "parent"
    write_child = child.set if mode == "assign" else child.register
    write_child("value", "child")
    assert sibling.get("value") == root.get("value") == "parent"
    root.dispose()


def test_compose_derive_does_not_register_a_context_or_own_contributions() -> None:
    root = Context()
    values = Compose(factory=ValueLayer, query=collect_values)
    children = tuple(root._lifecycle._owned)
    scope = values.derive(parents=root.scope, binding=ScopeBinding())
    assert tuple(root._lifecycle._owned) == children
    assert scope not in root._store._scope_usages
    remove = values.register(scope, "independent")
    root.dispose()
    assert values.resolve(scope) == ("independent",)
    remove()
    assert values.resolve(scope) == ()
