from __future__ import annotations

import gc
import weakref
from types import MappingProxyType

import pytest

import slyme.context as context_module
from slyme.context import Compose, Context, Schema

R = Schema({"hooks": Schema.leaf(), "tools": Schema.leaf()})


def test_compose_is_the_only_public_composition_type() -> None:
    assert context_module.Compose is Compose
    assert not hasattr(context_module, "Value")


def test_one_uses_context_and_entry_precedence() -> None:
    root = Context()
    agent = root.fork()
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
    root = Context()
    left = root.fork()
    right = root.fork()
    child = left.fork(right)
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
    root = Context()
    child = root.fork()
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
    root = Context()
    child = root.fork()
    values = Compose[int, int](sum)
    values.add(root, 2)
    values.add(child, 3)

    assert values.resolve(child) == 5
    assert values.resolve(child, local=True) == 3


def test_entries_are_immutable_snapshots_with_exact_disposal() -> None:
    ctx = Context()
    values = Compose[str, tuple[str, ...]].collect()
    metadata = {"plugin": "example"}

    remove_first = values.add(ctx, "same", metadata=metadata)
    remove_second = values.add(ctx, "same", metadata=metadata)
    metadata["late"] = True

    entries = values.entries(ctx)
    assert len(entries) == 2
    assert entries[0]["id"] is not entries[1]["id"]
    assert entries[0]["context"] is ctx
    assert entries[0]["value"] == "same"
    assert entries[0]["metadata"] == {"plugin": "example"}
    assert isinstance(entries[0], MappingProxyType)
    assert isinstance(entries[0]["metadata"], MappingProxyType)
    with pytest.raises(TypeError):
        entries[0]["value"] = "changed"  # type: ignore[index]

    remove_second()
    remove_second()
    assert values.values(ctx) == ("same",)
    remove_first()
    assert len(values) == 0


def test_compose_uses_weak_context_keys() -> None:
    values = Compose[str, tuple[str, ...]].collect()
    ctx = Context()
    ctx_ref = weakref.ref(ctx)
    dispose = values.add(ctx, "temporary")

    assert len(values) == 1
    del ctx
    gc.collect()

    assert ctx_ref() is None
    assert len(values) == 0
    dispose()


def test_compose_disposer_does_not_retain_removed_value() -> None:
    class Value:
        pass

    values = Compose[Value, tuple[Value, ...]].collect()
    ctx = Context()
    value = Value()
    value_ref = weakref.ref(value)
    dispose = values.add(ctx, value)

    dispose()
    del value
    gc.collect()

    assert value_ref() is None
    dispose()


def test_unrelated_context_trees_do_not_share_visible_entries() -> None:
    left = Context()
    right = Context()
    values = Compose[str, tuple[str, ...]].collect()
    values.add(left, "left")
    values.add(right, "right")

    assert values.resolve(left) == ("left",)
    assert values.resolve(right) == ("right",)
    assert {entry["value"] for entry in values.entries()} == {"left", "right"}
    with pytest.raises(ValueError, match="requires a Context"):
        values.entries(local=True)
    with pytest.raises(ValueError, match="position"):
        values.add(left, "bad", position="middle")  # type: ignore[arg-type]


def test_context_branches_can_share_an_explicit_ancestor() -> None:
    root = Context(schema=R)
    shared = root.fork()
    left = shared.fork()
    right = shared.fork()
    values = Compose[str, tuple[str, ...]].collect()

    values.add(shared, "shared")
    values.add(left, "left")

    assert values.resolve(left) == ("left", "shared")
    assert values.resolve(right) == ("shared",)


def test_context_can_shadow_a_compose_as_an_ordinary_leaf() -> None:
    tools_ref = R.resolve("tools")
    root = Context(schema=R)
    inherited = Compose[str, tuple[str, ...]].collect()
    root.add(tools_ref, inherited)
    child = root.fork()

    inherited.add(root, "root-tool")
    inherited.add(child, "agent-tool")
    assert child.get(tools_ref) is inherited
    assert inherited.resolve(child) == ("agent-tool", "root-tool")

    isolated = Compose[str, tuple[str, ...]].collect()
    child.add(tools_ref, isolated)
    isolated.add(child, "isolated-tool")
    assert child.get(tools_ref) is isolated
    assert isolated.resolve(child) == ("isolated-tool",)


def test_flattened_context_shares_compose_but_not_scope_identity() -> None:
    ref = R.resolve("hooks")
    ctx = Context(schema=R)
    hooks = Compose[str, tuple[str, ...]].collect()
    ctx.add(ref, hooks)
    hooks.add(ctx, "handler")

    snapshot = Context(ctx.flatten(), schema=ctx.schema)
    assert snapshot.get(ref) is hooks
    assert hooks.resolve(ctx) == ("handler",)
    assert hooks.resolve(snapshot) == ()
