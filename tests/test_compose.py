from __future__ import annotations

import gc
import weakref
from types import MappingProxyType

import pytest

import slyme.context as context_module
from slyme.context import Compose, Context, Schema, Scope

R = Schema({"hooks": Schema.leaf(), "tools": Schema.leaf()})


def test_compose_is_the_only_public_composition_type() -> None:
    assert context_module.Compose is Compose
    assert not hasattr(context_module, "Value")


def test_one_uses_scope_and_entry_precedence() -> None:
    root = Scope("root")
    agent = root.fork(name="agent")
    values = Compose[str, str].one()

    remove_root = values.add(root, "root")
    remove_agent = values.add(agent, "agent")
    remove_later = values.add(agent, "later")

    assert values.resolve(root) == "root"
    assert values.resolve(agent) == "agent"

    remove_override = values.add(agent, "override", position="prepend")
    assert values.resolve(agent) == "override"
    remove_override()
    assert values.resolve(agent) == "agent"

    remove_agent()
    assert values.resolve(agent) == "later"
    remove_later()
    assert values.resolve(agent) == "root"
    remove_root()
    with pytest.raises(LookupError, match="no value"):
        values.resolve(agent)


def test_collect_follows_c3_without_repeating_diamond_ancestors() -> None:
    root = Scope("root")
    left = root.fork(name="left")
    right = root.fork(name="right")
    child = Scope("child", parents=(left, right))
    values = Compose[str, tuple[str, ...]].collect()

    values.add(root, "root")
    values.add(left, "left")
    values.add(right, "right")
    values.add(child, "child-last")
    values.add(child, "child-first", position="prepend")

    assert values.resolve(child) == (
        "child-first",
        "child-last",
        "left",
        "right",
        "root",
    )
    assert values.values(child, local=True) == ("child-first", "child-last")


def test_merge_preserves_entries_and_uses_first_visible_key() -> None:
    root = Scope("root")
    child = root.fork(name="child")
    values = Compose.merge()

    remove_root = values.add(root, {"shared": "root", "root": 1})
    remove_child = values.add(child, {"shared": "child", "child": 2})
    remove_later = values.add(child, {"shared": "later"})

    assert values.resolve(child) == {
        "shared": "child",
        "child": 2,
        "root": 1,
    }

    remove_override = values.add(
        child,
        {"shared": "override"},
        position="prepend",
    )
    assert values.resolve(child)["shared"] == "override"
    remove_override()
    remove_child()
    assert values.resolve(child)["shared"] == "later"
    remove_later()
    assert values.resolve(child) == {"shared": "root", "root": 1}
    remove_root()
    assert values.resolve(child) == {}


def test_compose_accepts_a_custom_resolver() -> None:
    root = Scope("root")
    child = root.fork(name="child")
    values = Compose[int, int](sum)
    values.add(root, 2)
    values.add(child, 3)

    assert values.resolve(child) == 5
    assert values.resolve(child, local=True) == 3


def test_entries_are_immutable_snapshots_with_exact_disposal() -> None:
    scope = Scope()
    values = Compose[str, tuple[str, ...]].collect()
    metadata = {"plugin": "example"}

    remove_first = values.add(scope, "same", metadata=metadata)
    remove_second = values.add(scope, "same", metadata=metadata)
    metadata["late"] = True

    entries = values.entries(scope)
    assert len(entries) == 2
    assert entries[0]["id"] is not entries[1]["id"]
    assert entries[0]["scope"] is scope
    assert entries[0]["identity"] is entries[1]["identity"]
    assert entries[0]["value"] == "same"
    assert entries[0]["metadata"] == {"plugin": "example"}
    assert isinstance(entries[0], MappingProxyType)
    assert isinstance(entries[0]["metadata"], MappingProxyType)
    with pytest.raises(TypeError):
        entries[0]["value"] = "changed"  # type: ignore[index]

    remove_second()
    remove_second()
    assert values.values(scope) == ("same",)
    remove_first()
    assert len(values) == 0


def test_bind_shares_one_compose_identity_only_in_that_compose() -> None:
    left = Scope("left")
    right = Scope("right")
    identity = object()
    shared = Compose[str, tuple[str, ...]].collect()
    independent = Compose[str, tuple[str, ...]].collect()

    shared.bind(left, right, identity=identity)
    shared.add(left, "left")
    shared.add(right, "right")
    independent.add(left, "independent")

    assert shared.resolve(left) == ("left", "right")
    assert shared.resolve(right, local=True) == ("left", "right")
    assert independent.resolve(right) == ()
    assert {entry["scope"] for entry in shared.entries(right)} == {left, right}
    assert all(entry["identity"] is identity for entry in shared.entries())


def test_bind_is_atomic_idempotent_and_rejects_rebinding() -> None:
    left = Scope("left")
    right = Scope("right")
    values = Compose[str, tuple[str, ...]].collect()
    first = object()
    second = object()

    values.bind(left, identity=first)
    values.bind(left, identity=first)
    with pytest.raises(ValueError, match="rebound"):
        values.bind(right, left, identity=second)

    values.bind(right, identity=first)
    values.add(right, "shared")
    assert values.resolve(left) == ("shared",)
    with pytest.raises(ValueError, match="at least one Scope"):
        values.bind(identity=first)
    with pytest.raises(TypeError, match="identities must be hashable"):
        values.bind(Scope(), identity=[])  # type: ignore[arg-type]


def test_read_does_not_bind_and_first_write_prevents_later_rebinding() -> None:
    scope = Scope()
    values = Compose[str, tuple[str, ...]].collect()
    identity = object()

    assert values.resolve(scope) == ()
    values.bind(scope, identity=identity)
    remove = values.add(scope, "value")
    remove()

    assert values.resolve(scope) == ()
    with pytest.raises(ValueError, match="rebound"):
        values.bind(scope, identity=object())
    values.add(scope, "new")
    assert values.entries(scope)[0]["identity"] is identity

    implicit = Scope()
    values.add(implicit, "implicit")
    with pytest.raises(ValueError, match="rebound"):
        values.bind(implicit, identity=identity)


def test_empty_binding_does_not_retain_an_unreferenced_scope() -> None:
    values = Compose[str, tuple[str, ...]].collect()
    scope = Scope()
    scope_ref = weakref.ref(scope)
    values.bind(scope, identity=object())

    del scope
    gc.collect()

    assert scope_ref() is None
    assert not values._scope_identities


def test_c3_lookup_visits_a_shared_identity_only_once() -> None:
    root = Scope("root")
    left = root.fork(name="left")
    right = root.fork(name="right")
    child = Scope("child", parents=(left, right))
    identity = object()
    values = Compose[str, tuple[str, ...]].collect()

    values.bind(left, right, identity=identity)
    values.add(left, "left")
    values.add(right, "right")
    values.add(root, "root")

    assert values.resolve(child) == ("left", "right", "root")


def test_compose_retains_scope_and_value_until_exact_disposal() -> None:
    class Value:
        pass

    values = Compose[Value, tuple[Value, ...]].collect()
    scope = Scope()
    value = Value()
    scope_ref = weakref.ref(scope)
    value_ref = weakref.ref(value)
    dispose = values.add(scope, value)

    del scope
    del value
    gc.collect()

    assert scope_ref() is not None
    assert value_ref() is not None
    assert len(values) == 1

    dispose()
    gc.collect()

    assert scope_ref() is None
    assert value_ref() is None
    assert len(values) == 0
    dispose()


def test_compose_disposer_does_not_retain_an_already_removed_value() -> None:
    class Value:
        pass

    values = Compose[Value, tuple[Value, ...]].collect()
    scope = Scope()
    value = Value()
    value_ref = weakref.ref(value)
    dispose = values.add(scope, value)

    dispose()
    del value
    gc.collect()

    assert value_ref() is None
    dispose()


def test_unrelated_scopes_can_share_one_compose_without_visibility_leaks() -> None:
    left = Scope("left")
    right = Scope("right")
    combined = Scope("combined", parents=(left, right))
    values = Compose[str, tuple[str, ...]].collect()
    values.add(left, "left")
    values.add(right, "right")

    assert values.resolve(left) == ("left",)
    assert values.resolve(right) == ("right",)
    assert values.resolve(combined) == ("left", "right")
    assert {entry["value"] for entry in values.entries()} == {"left", "right"}
    with pytest.raises(ValueError, match="requires a Scope"):
        values.entries(local=True)


def test_context_fork_shares_scope_unless_one_is_explicit() -> None:
    root = Context(schema=R)
    shared = root.fork()
    child_scope = root.scope.fork(name="child")
    isolated = root.fork(scope=child_scope)

    assert shared.scope is root.scope
    assert isolated.scope is child_scope
    assert isolated.parent is root


def test_context_data_and_scope_identity_are_orthogonal() -> None:
    ref = R.resolve("tools")
    left = Scope("left")
    right = Scope("right")
    combined = Scope("combined", parents=(left, right))
    root = Context(schema=R, scope=left)
    right_context = root.fork(scope=right)
    combined_context = root.fork(scope=combined)

    root.set(ref, "left")
    right_context.set(ref, "right")

    assert root.get(ref) == "left"
    assert right_context.get(ref) == "right"
    assert combined_context.get(ref) == "left"
    root.delete(ref)
    assert combined_context.get(ref) == "right"


def test_independent_context_roots_do_not_share_data_with_the_same_scope() -> None:
    ref = R.resolve("tools")
    scope = Scope("shared-identity")
    left = Context(schema=R, scope=scope)
    right = Context(schema=R, scope=scope)

    left.set(ref, "left")

    assert left.get(ref) == "left"
    assert not right.exists(ref)


def test_context_can_shadow_a_compose_as_an_ordinary_leaf() -> None:
    tools_ref = R.resolve("tools")
    root = Context(schema=R)
    inherited = Compose[str, tuple[str, ...]].collect()
    root.add(tools_ref, inherited)
    child = root.fork(scope=root.scope.fork(name="child"))

    inherited.add(root.scope, "root-tool")
    inherited.add(child.scope, "agent-tool")
    assert child.get(tools_ref) is inherited
    assert inherited.resolve(child.scope) == ("agent-tool", "root-tool")

    isolated = Compose[str, tuple[str, ...]].collect()
    child.add(tools_ref, isolated)
    isolated.add(child.scope, "isolated-tool")
    assert child.get(tools_ref) is isolated
    assert isolated.resolve(child.scope) == ("isolated-tool",)


def test_context_contribution_uses_bound_or_explicit_scope_and_is_owned() -> None:
    hooks_ref = R.resolve("hooks")
    root = Context(schema=R)
    hooks = Compose[str, tuple[str, ...]].collect()
    root.add(hooks_ref, hooks)
    child = root.fork(scope=root.scope.fork(name="child"))
    external = Scope("external")

    remove_bound = child.contribute(
        hooks_ref,
        "bound",
        metadata={"source": "child"},
        position="prepend",
    )
    child.contribute(hooks_ref, "external", scope=external)

    assert hooks.resolve(child.scope) == ("bound",)
    assert hooks.resolve(external) == ("external",)
    assert hooks.entries(child.scope)[0]["metadata"] == {"source": "child"}

    remove_bound()
    assert hooks.resolve(child.scope) == ()
    child.dispose()
    assert hooks.resolve(external) == ()


def test_flattened_context_shares_compose_but_not_scope_identity() -> None:
    ref = R.resolve("hooks")
    ctx = Context(schema=R)
    hooks = Compose[str, tuple[str, ...]].collect()
    ctx.add(ref, hooks)
    hooks.add(ctx.scope, "handler")

    snapshot = Context(ctx.flatten(), schema=ctx.schema)
    assert snapshot.get(ref) is hooks
    assert hooks.resolve(ctx.scope) == ("handler",)
    assert hooks.resolve(snapshot.scope) == ()
