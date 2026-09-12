from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pytest

from slyme.context import Context, Ref, Schema
from slyme.context.core import ContextPathError
from slyme.node.eval import ref_evaluator
from slyme.utils.tree import TreeAux, TreeEngine


@pytest.mark.parametrize("operation", ["update", "update_tree"])
@pytest.mark.parametrize("failure", ["container", "undeclared", "nonreplaceable"])
def test_batch_write_preflight_leaves_all_bindings_unchanged(
    operation: str, failure: str
) -> None:
    schema = Schema(
        {"a": Schema.leaf(), "b": Schema.leaf(replaceable=False), "group": {}}
    )
    ctx = Context({"a": 1, "b": 2}, schema=schema)
    target = {"container": "group", "undeclared": "missing", "nonreplaceable": "b"}[
        failure
    ]
    with pytest.raises(ContextPathError):
        if operation == "update":
            ctx.update({"a": 3, target: 4})
        else:
            ctx.update_tree(["a", target], [3, 4])
    assert ctx.to_dict() == {"a": 1, "b": 2}
    ctx.dispose()


def test_update_normalizes_ref_and_string_keys_before_writing() -> None:
    schema = Schema({"value": Schema.leaf(replaceable=False)})
    ctx = Context(schema=schema)
    ctx.update({"value": 1, schema.resolve("value"): 2})
    assert ctx.get("value") == 2
    with pytest.raises(ContextPathError, match="non-replaceable"):
        ctx.update({"value": 3})
    assert ctx.get("value") == 2
    ctx.dispose()


def test_update_rechecks_replacement_policy_after_a_reentrant_write() -> None:
    schema = Schema({"a": Schema.leaf(), "b": Schema.leaf(replaceable=False)})
    ctx = Context(schema=schema)

    class WriteOnRelease:
        def __del__(self) -> None:
            ctx.set("b", "reentrant")

    ctx.set("a", WriteOnRelease())
    with pytest.raises(ContextPathError, match="non-replaceable"):
        ctx.update({"a": "updated", "b": "requested"})
    assert ctx.to_dict() == {"a": "updated", "b": "reentrant"}
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


def test_update_allows_new_nonreplaceable_local_values_over_inheritance_and_barriers() -> (
    None
):
    schema = Schema({"service": Schema.leaf(replaceable=False)})
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
    with pytest.raises(ContextPathError, match="empty"):
        ref_evaluator(ctx, [schema.resolve("empty"), Ref("undeclared")])
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
