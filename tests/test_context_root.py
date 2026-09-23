from __future__ import annotations

import pytest

from slyme.context import Context, Ref, Schema, ScopeBinding
from slyme.context.core import ContextPathError, ContextView
from slyme.node import Auto, node


@pytest.mark.parametrize("path", [".", ".value", "value.", "value..child"])
def test_root_path_does_not_allow_empty_segments_inside_other_paths(path: str) -> None:
    with pytest.raises(ValueError, match="Invalid Ref path"):
        Ref(path)


@pytest.mark.parametrize("key", ["", Ref("")])
@pytest.mark.parametrize("local", [False, True])
def test_root_with_defaults_is_a_readable_view(key: str | Ref, local: bool) -> None:
    ctx = Context()
    view = ctx.get(key, None, local=local)
    assert isinstance(view, ContextView)
    assert ctx.exists(key, local=local)
    assert ctx.keys(key, local=local) == ("$",)
    assert ctx.to_dict(key, local=local) == {"$": ctx.get("$").to_dict()}
    assert view.exists("", local=local)
    assert view.get("", local=local) == view
    assert view.keys(local=local) == ("$",)
    assert view.to_dict(local=local) == {"$": ctx.get("$").to_dict()}
    assert view.flatten(local=local) == ctx.get("$").flatten()
    assert ctx.extract({"root": key}, local=local) == {"root": view}
    assert len(ctx._store._data) == 3
    with pytest.raises(ContextPathError, match="register"):
        ctx.delete(key)
    with pytest.raises(ContextPathError, match="register"):
        ctx.drop([key])
    assert ctx.get(key) == view
    ctx.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        view.to_dict()
    with pytest.raises(RuntimeError, match="disposed"):
        ctx.get(key)


def test_root_view_remains_live_across_declaration_value_and_cleanup_changes() -> None:
    ctx = Context()
    view = ctx.get("")
    remove = ctx.declare({"group": {"value": Schema.leaf()}})
    ctx.set("group.value", 3)
    assert view.keys() == ("$", "group")
    assert view.get("group.value") == 3
    assert ctx.get(Ref("group.value")) == 3
    assert view.to_dict() == {"$": ctx.get("$").to_dict(), "group": {"value": 3}}
    assert view.flatten() == {**ctx.get("$").flatten(), Ref("group.value"): 3}

    nested = view.get("group")
    assert nested.get("") == nested
    assert nested.exists("")
    assert tuple(nested.keys("")) == tuple(nested.keys()) == ("value",)
    assert nested.to_dict("") == nested.to_dict() == {"value": 3}

    ctx.delete("group")
    assert view.to_dict() == {"$": ctx.get("$").to_dict()}
    assert view.keys() == ("$",)
    assert view.flatten() == ctx.get("$").flatten()
    assert not ctx.exists("group")
    with pytest.raises(ContextPathError):
        nested.to_dict()
    remove()
    assert ctx.get("") == view
    assert ctx.resolve("") == Ref("")
    assert view.to_dict() == {"$": ctx.get("$").to_dict()}
    ctx.dispose()


@pytest.mark.parametrize("key", ["", Ref("")])
@pytest.mark.parametrize(
    "operation", ["set", "register", "update", "update_tree", "bind", "block"]
)
def test_root_rejects_leaf_operations_without_partial_writes(
    key: str | Ref, operation: str
) -> None:
    ctx = Context()
    ctx.declare(Schema({"value": Schema.leaf()}))
    initial_effects = tuple(ctx._lifecycle._effects)
    ctx.update({"value": 1})
    with pytest.raises(ContextPathError, match="container, not a leaf"):
        if operation == "update":
            ctx.update({"value": 2, key: {}})
        elif operation == "update_tree":
            ctx.update_tree(["value", key], [2, {}])
        elif operation == "bind":
            ctx.derive(bindings={key: ScopeBinding()})
        elif operation == "block":
            ctx.derive(bindings={key: ScopeBinding(blocked=True)})
        else:
            getattr(ctx, operation)(key, {})
    assert ctx.to_dict() == {"$": ctx.get("$").to_dict(), "value": 1}
    assert tuple(ctx._lifecycle._effects) == initial_effects
    ctx.dispose()


@pytest.mark.parametrize("batch", [False, True])
def test_root_deletion_rejects_inherited_default_registrations(batch: bool) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    child = root.fork(scope=root.scope.fork())
    child.set("value", "local")
    events = []
    child.effect(lambda: lambda: events.append("cleanup"))
    before = child.flatten()
    with pytest.raises(ContextPathError, match="register"):
        if batch:
            child.drop(["value", ""])
        else:
            child.delete("")
    assert child.flatten() == before
    assert events == []
    child.delete("value")
    child.set("value", "new")
    assert child.get("value") == "new"
    root.dispose()
    assert events == ["cleanup"]


def test_auto_can_inject_the_root_view() -> None:
    @node
    def snapshot(ctx, /, *, data):
        return data.to_dict()

    ctx = Context()
    graph = snapshot(data=Auto(Ref("")))
    assert graph(ctx) == {"$": ctx.get("$").to_dict()}
    remove = ctx.declare({"value": Schema.leaf()})
    ctx.set("value", 3)
    assert graph(ctx) == {"$": ctx.get("$").to_dict(), "value": 3}
    remove()
    assert graph(ctx) == {"$": ctx.get("$").to_dict()}
    ctx.dispose()
