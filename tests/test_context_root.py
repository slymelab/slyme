from __future__ import annotations

import pytest

from slyme.context import Context, Ref, Schema
from slyme.context.core import ContextPathError, ContextView
from slyme.node import Auto, node


@pytest.mark.parametrize("path", [".", ".value", "value.", "value..child"])
def test_root_path_does_not_allow_empty_segments_inside_other_paths(path: str) -> None:
    with pytest.raises(ValueError, match="Invalid Ref path"):
        Ref(path)


@pytest.mark.parametrize("key", ["", Ref("")])
@pytest.mark.parametrize("local", [False, True])
def test_empty_root_is_a_readable_view(key: str | Ref, local: bool) -> None:
    ctx = Context()
    view = ctx.get(key, None, local=local)
    assert isinstance(view, ContextView)
    assert ctx.exists(key, local=local)
    assert ctx.keys(key, local=local) == ()
    assert ctx.to_dict(key, local=local) == {}
    assert view.exists("", local=local)
    assert view.get("", local=local) == view
    assert view.keys(local=local) == ()
    assert view.to_dict(local=local) == {}
    assert view.flatten(local=local) == {}
    assert ctx.extract({"root": key}, local=local) == {"root": view}
    assert not ctx._data
    ctx.delete(key)
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
    assert view.keys() == ("group",)
    assert view.get("group.value") == 3
    assert view.get(Ref("group.value")) == 3
    assert view.to_dict() == {"group": {"value": 3}}
    assert view.flatten() == {Ref("group.value"): 3}

    nested = view.get("group")
    assert nested.get("") == nested
    with pytest.raises(ContextPathError, match="outside"):
        nested.get(Ref(""))

    ctx.delete("")
    assert view.to_dict() == {}
    assert view.keys() == ()
    assert view.flatten() == {}
    assert not ctx.exists("group")
    with pytest.raises(ContextPathError):
        nested.to_dict()
    remove()
    assert ctx.get("") == view
    assert ctx.schema.resolve("") == Ref("")
    assert view.to_dict() == {}
    ctx.dispose()


@pytest.mark.parametrize("key", ["", Ref("")])
@pytest.mark.parametrize(
    "operation", ["set", "add", "update", "update_tree", "isolate"]
)
def test_root_rejects_leaf_operations_without_partial_writes(
    key: str | Ref, operation: str
) -> None:
    ctx = Context({"value": 1}, schema=Schema({"value": Schema.leaf()}))
    with pytest.raises(ContextPathError, match="container, not a leaf"):
        if operation == "update":
            ctx.update({"value": 2, key: {}})
        elif operation == "update_tree":
            ctx.update_tree(["value", key], [2, {}])
        elif operation == "isolate":
            ctx.isolate(key)
        else:
            getattr(ctx, operation)(key, {})
    assert ctx.to_dict() == {"value": 1}
    assert not ctx._owned
    ctx.dispose()


@pytest.mark.parametrize("key", ["", Ref("")])
@pytest.mark.parametrize("batch", [False, True])
def test_root_deletion_only_changes_local_scope_values(
    key: str | Ref, batch: bool
) -> None:
    schema = Schema({"group": {"value": Schema.leaf()}, "other": Schema.leaf()})
    root = Context({"group.value": 1, "other": 2}, schema=schema)
    child = root.fork(scope=root.scope.fork())
    shared = child.fork()
    sibling = root.fork(scope=root.scope.fork())
    child.update({"group.value": 3, "other": 4})
    sibling.set("group.value", 5)
    bindings = dict(root._data)

    if batch:
        child.drop([key, "group", "group.value", key])
    else:
        child.delete(key)
    assert child.to_dict(local=True) == shared.to_dict(local=True) == {}
    assert (
        child.to_dict()
        == shared.to_dict()
        == root.to_dict()
        == {
            "group": {"value": 1},
            "other": 2,
        }
    )
    assert sibling.to_dict() == {"group": {"value": 5}, "other": 2}
    assert dict(root._data) == bindings
    assert child.get("").to_dict(local=True) == {}
    assert child.get("").to_dict() == root.to_dict()

    root.delete(key)
    assert root.to_dict() == child.to_dict() == {}
    assert sibling.get("group.value") == 5
    root.dispose()


def test_root_deletion_respects_shared_identities_and_preserves_isolation() -> None:
    root = Context(
        {"service": "root"},
        schema=Schema({"service": Schema.leaf(), "local": Schema.leaf()}),
    )
    identity = object()
    left = root.isolate("service", identity=identity)
    right = root.isolate("service", identity=identity)
    left.set("service", "shared")
    right.set("local", "right")
    left.delete("")
    assert not left.exists("service")
    assert not right.exists("service")
    assert right.get("local") == "right"
    assert root.get("service") == "root"
    right.set("service", "new")
    assert left.get("service") == "new"
    root.dispose()


def test_root_deletion_does_not_dispose_effects_or_revoke_later_values() -> None:
    ctx = Context(schema=Schema({"value": Schema.leaf()}))
    events = []
    ctx.effect(lambda: lambda: events.append("cleanup"))
    undo = ctx.add("value", "old")
    ctx.delete("")
    assert events == []
    ctx.set("value", "new")
    undo()
    assert ctx.get("value") == "new"
    ctx.dispose()
    assert events == ["cleanup"]


def test_auto_can_inject_the_root_view() -> None:
    @node
    def snapshot(ctx, /, *, data):
        return data.to_dict()

    ctx = Context()
    graph = snapshot(data=Auto(Ref("")))
    assert graph(ctx) == {}
    remove = ctx.declare({"value": Schema.leaf()})
    ctx.set("value", 3)
    assert graph(ctx) == {"value": 3}
    remove()
    assert graph(ctx) == {}
    ctx.dispose()
