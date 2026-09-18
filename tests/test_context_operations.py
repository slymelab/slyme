from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from slyme.context import Context, Ref, Schema
from slyme.context.core import ContextPathError
from slyme.node.eval import ref_evaluator
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.tree import TreeAux, TreeEngine


@pytest.mark.parametrize("operation", ["update", "update_tree"])
@pytest.mark.parametrize("failure", ["container", "undeclared", "register"])
def test_batch_write_preflight_leaves_all_bindings_unchanged(
    operation: str, failure: str
) -> None:
    schema = Schema(
        {"a": Schema.leaf(), "b": Schema.leaf(mode="register"), "group": {}}
    )
    ctx = Context({"a": 1}, schema=schema)
    ctx.register("b", 2)
    target = {"container": "group", "undeclared": "missing", "register": "b"}[failure]
    with pytest.raises(ContextPathError):
        if operation == "update":
            ctx.update({"a": 3, target: 4})
        else:
            ctx.update_tree(["a", target], [3, 4])
    assert ctx.to_dict() == {"a": 1, "b": 2}
    ctx.dispose()


def test_update_normalizes_ref_and_string_keys_before_writing() -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context(schema=schema)
    ctx.update({"value": 1, schema.resolve("value"): 2})
    assert ctx.get("value") == 2
    ctx.update({"value": 3})
    assert ctx.get("value") == 3
    ctx.dispose()


@pytest.mark.parametrize("operation", ["set", "update", "update_tree"])
def test_assignment_cannot_create_an_empty_registration(operation: str) -> None:
    schema = Schema({"service": Schema.leaf(mode="register")})
    ctx = Context(schema=schema)
    with pytest.raises(ContextPathError, match="register"):
        if operation == "set":
            ctx.set("service", object())
        elif operation == "update":
            ctx.update({"service": object()})
        else:
            ctx.update_tree(["service"], [object()])
    assert not ctx._data
    assert not ctx._owned
    ctx.dispose()


@pytest.mark.parametrize("operation", ["delete", "drop"])
@pytest.mark.parametrize("target", ["group.service", "group", ""])
@pytest.mark.parametrize("installed", [False, True])
def test_deletion_rejects_registration_fields_before_changing_any_value(
    operation: str, target: str, installed: bool
) -> None:
    schema = Schema(
        {"group": {"value": Schema.leaf(), "service": Schema.leaf(mode="register")}}
    )
    ctx = Context({"group.value": 1}, schema=schema)
    if installed:
        ctx.register("group.service", None)
    before = ctx.flatten()
    with pytest.raises(ContextPathError, match="register"):
        if operation == "delete":
            ctx.delete(target)
        else:
            ctx.drop(["group.value", target])
    assert ctx.flatten() == before
    ctx.dispose()


def test_initializer_rejects_registration_without_leaking_a_scope_viewer() -> None:
    schema = Schema({"value": Schema.leaf(), "service": Schema.leaf(mode="register")})
    root = Context({"value": "original"}, schema=schema)
    with pytest.raises(ContextPathError, match="register"):
        Context({"value": "changed", "service": object()}, parent=root)
    assert root.get("value") == "original"
    assert not root._owned
    assert root._scope_usage[root.scope].viewers == {root}
    root.dispose()


def test_shared_identity_has_one_registration_and_allows_reinstallation() -> None:
    schema = Schema({"service": Schema.leaf(mode="register")})
    root = Context(schema=schema)
    root.register("service", "inherited")
    left = root.isolate("service", identity="shared")
    right = root.isolate("service", identity="shared")
    payload: list[str] = []
    remove = left.register("service", payload)
    with pytest.raises(ContextPathError, match="existing local"):
        right.register("service", object())
    assert not right._owned
    right.get("service").append("mutable payload")
    assert payload == ["mutable payload"]
    remove()
    assert not left.exists("service")
    assert not right.exists("service")
    right.register("service", "new")
    remove()
    left.dispose()
    assert right.get("service") == "new"
    assert root.get("service") == "inherited"
    root.dispose()


def test_update_applies_assignments_in_order_after_a_reentrant_write() -> None:
    schema = Schema({"a": Schema.leaf(), "b": Schema.leaf()})
    ctx = Context(schema=schema)

    class WriteOnRelease:
        def __del__(self) -> None:
            ctx.set("b", "reentrant")

    ctx.set("a", WriteOnRelease())
    ctx.update({"a": "updated", "b": "requested"})
    assert ctx.to_dict() == {"a": "updated", "b": "requested"}
    ctx.dispose()


@pytest.mark.parametrize("failure", ["path", "iteration"])
def test_drop_validates_and_consumes_all_inputs_before_deleting(failure: str) -> None:
    schema = Schema({"group": {"a": Schema.leaf(), "b": Schema.leaf()}})
    ctx = Context({"group.a": 1, "group.b": 2}, schema=schema)

    def refs() -> Iterable[str]:
        yield "group"
        if failure == "iteration":
            raise RuntimeError("input failed")
        yield "missing"

    error = RuntimeError if failure == "iteration" else ContextPathError
    with pytest.raises(error):
        ctx.drop(refs())
    assert ctx.to_dict() == {"group": {"a": 1, "b": 2}}
    ctx.dispose()


@pytest.mark.parametrize("batch", [False, True])
def test_deleting_containers_only_removes_local_values(batch: bool) -> None:
    schema = Schema({"group": {"a": Schema.leaf(), "b": Schema.leaf()}})
    root = Context({"group.a": 1, "group.b": 2}, schema=schema)
    child = root.fork(scope=root.scope.fork())
    child.update({"group.a": 3, "group.b": 4})
    if batch:
        child.drop(["group", "group.a", "group"])
    else:
        child.delete("group")
    assert child.to_dict(local=True) == {}
    assert child.to_dict() == root.to_dict() == {"group": {"a": 1, "b": 2}}
    root.dispose()


def test_update_assigns_local_values_over_inheritance_and_barriers() -> None:
    schema = Schema({"service": Schema.leaf()})
    root = Context({"service": "root"}, schema=schema)
    child = root.fork(scope=root.scope.fork())
    isolated = root.isolate("service")
    child.update({"service": "child"})
    isolated.update({"service": "isolated"})
    assert child.get("service") == "child"
    assert isolated.get("service") == "isolated"
    isolated.drop(["service"])
    assert not isolated.exists("service")
    isolated.update({"service": "new"})
    assert isolated.get("service") == "new"
    assert root.get("service") == "root"
    root.dispose()


def test_extract_traverses_custom_containers_once_and_reconstructs_only_final_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Box:
        def __init__(self, value: Any):
            self.value = value

    events: list[tuple[str, Any]] = []
    engine = TreeEngine("test_extract", register_defaults=True)

    def flatten(box):
        events.append(("flatten", box.value))
        return iter((box.value,)), TreeAux()

    def unflatten(children, aux):
        value = next(iter(children))
        events.append(("unflatten", value))
        return Box(value)

    engine.register(Box, flatten, unflatten)
    monkeypatch.setattr("slyme.context.core.CTX_EVAL_ENGINE", engine)
    schema = Schema({"value": Schema.leaf()})
    ref = schema.resolve("value")
    payload = [object()]
    ctx = Context({ref: payload}, schema=schema)
    request = Box(ref)
    result = ctx.extract(request)
    assert result is not request
    assert result.value is payload
    assert events == [("flatten", ref), ("unflatten", payload)]
    ctx.dispose()


def test_extract_validates_all_refs_before_reading_values() -> None:
    schema = Schema({"empty": Schema.leaf()})
    ctx = Context(schema=schema)
    with pytest.raises(ContextPathError, match="undeclared"):
        ctx.extract([schema.resolve("empty"), Ref("undeclared")])
    with pytest.raises(BaseExceptionGroup) as caught:
        ref_evaluator(ctx, [schema.resolve("empty"), Ref("undeclared")])
    assert len(caught.value.exceptions) == 2
    assert all(isinstance(error, ContextPathError) for error in caught.value.exceptions)
    assert "empty" in str(caught.value.exceptions[0])
    assert "undeclared" in str(caught.value.exceptions[1])
    ctx.dispose()


def test_ref_evaluator_uses_get_in_input_order_and_preserves_container_views() -> None:
    seen: list[Ref] = []

    class RecordingContext(Context):
        def get(self, ref, *args, **kwargs):
            seen.append(ref)
            return super().get(ref, *args, **kwargs)

    schema = Schema({"group": {"value": Schema.leaf()}})
    payload = [object()]
    ctx = RecordingContext({"group.value": payload}, schema=schema)
    refs = [schema.resolve("group.value"), schema.resolve("group")]
    value, view = ref_evaluator(ctx, refs)
    assert seen == refs
    assert value is payload
    assert view.get("value") is payload
    ctx.dispose()
