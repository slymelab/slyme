from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Literal

import pytest

from slyme.context import Context, Ref, Schema, Scope
from slyme.context.core import ContextPathError
from slyme.context.default import DATA_TREE_REF
from slyme.context.store import _MISSING
from slyme.node.eval import ref_evaluator
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.tree import TreeAux, TreeHandler, TreeRules


@pytest.mark.parametrize("key", ["missing.path", Ref("missing.path")])
def test_undeclared_path_errors_are_the_same_for_strings_and_refs(
    key: str | Ref[Any],
) -> None:
    ctx = Context()
    for operation in (
        lambda: ctx.resolve(key),
        lambda: ctx.resolve_entry(key),
        lambda: ctx.get(key, None),
        lambda: ctx.exists(key),
        lambda: ctx.set(key, 1),
        lambda: ctx.delete(key),
        lambda: ctx.bind(key, identity=object()),
        lambda: ctx.set_blocked(key, blocked=True),
        lambda: ctx.set_blocked(key, blocked=False),
    ):
        with pytest.raises(ContextPathError) as caught:
            operation()
        assert caught.value.args == ("Context path 'missing.path' is not declared.",)
        assert isinstance(caught.value.__cause__, KeyError)
        assert caught.value.__cause__.args == ("missing.path",)
    ctx.dispose()


def test_entry_resolution_without_a_role_does_not_materialize_config() -> None:
    ctx = Context()
    declaration = {"group": {"value": Schema.leaf()}}
    withdraw = ctx.declare(declaration)
    ctx.declare(declaration)
    entries = {path: ctx.resolve_entry(path) for path in ("", "group", "group.value")}
    withdraw()

    for path, entry in entries.items():
        assert entry._config is None
        assert ctx.resolve_entry(path) is entry
        assert ctx.resolve_entry(Ref(path), role=None) is entry
        assert ctx.resolve(path) is entry.ref
        assert ctx.resolve(Ref(path), role=None) is entry.ref
        assert entry._config is None

    assert ctx.resolve_entry("group", role="container") is entries["group"]
    assert entries["group"]._config is not None
    assert ctx.resolve(Ref("group.value"), role="leaf") is entries["group.value"].ref
    assert entries["group.value"]._config is not None
    ctx.dispose()


def test_container_reads_share_scope_visibility_and_empty_container_rules() -> None:
    root = Context()
    root.declare({"group": {"first": Schema.leaf(), "second": Schema.leaf()}})
    root.update({"group.first": False, "group.second": None})
    child = root.fork(scope=root.scope.fork())
    assert child.get("group").to_dict() == {"first": False, "second": None}
    assert tuple(child.keys("group")) == ("first", "second")
    assert child.to_dict("group") == {"first": False, "second": None}
    assert child.flatten() == root.flatten()
    assert child.get("group", "missing", local=True) == "missing"
    assert not child.exists("group", local=True)
    for operation in (
        lambda: child.get("group", local=True),
        lambda: child.keys("group", local=True),
        lambda: child.to_dict("group", local=True),
        lambda: child.get("group").flatten(local=True),
    ):
        with pytest.raises(ContextPathError, match="group"):
            operation()

    child.set("group.second", [])
    assert child.get("group", local=True).to_dict(local=True) == {"second": []}
    assert child.to_dict("group") == {"first": False, "second": []}
    assert root.to_dict("group") == {"first": False, "second": None}
    root.dispose()


@pytest.mark.parametrize(
    "missing", ["unset", "deleted", "unrelated", "local", "barrier"]
)
def test_missing_values_use_internal_sentinel_and_public_defaults(missing: str) -> None:
    root = Context()
    root.declare({"group": {"value": Schema.leaf(), "present": Schema.leaf()}})
    if missing != "unset":
        root.set("group.value", "inherited")
    if missing == "deleted":
        root.delete("group.value")
    if missing == "barrier":
        child = root.fork(scope=root.scope.fork())
        child.set_blocked("group.value", blocked=True)
    else:
        child = root.fork(
            scope=Scope() if missing == "unrelated" else root.scope.fork()
        )
    child.set("group.present", None)
    local = missing == "local"
    entry = child.resolve_entry("group.value")
    fallback = object()
    view = child.get("group")

    assert child._store.get(child.scope, entry, local=local) is _MISSING
    assert not child.exists(entry.ref, local=local)
    assert child.get(entry.ref, fallback, local=local) is fallback
    assert child.get(entry.ref, None, local=local) is None
    assert view.get("value", fallback, local=local) is fallback
    assert tuple(child.keys("group", local=local)) == ("present",)
    assert child.to_dict("group", local=local) == {"present": None}
    assert view.flatten(local=local) == {child.resolve("group.present"): None}
    with pytest.raises(ContextPathError, match="group.value") as caught:
        child.get(entry.ref, local=local)
    assert caught.value.__cause__ is None
    root.dispose()


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}])
def test_falsy_values_are_not_missing(value: Any) -> None:
    ctx = Context()
    ctx.declare({"group": {"value": Schema.leaf()}})
    ctx.set("group.value", value)
    entry = ctx.resolve_entry("group.value")
    assert ctx._store.get(ctx.scope, entry) is value
    assert ctx.get(entry.ref, object()) is value
    assert ctx.exists(entry.ref)
    assert ctx.exists("group")
    assert tuple(ctx.keys("group")) == ("value",)
    assert ctx.to_dict("group")["value"] is value
    ctx.dispose()


@pytest.mark.parametrize("operation", ["update", "update_tree"])
@pytest.mark.parametrize("failure", ["container", "undeclared", "register"])
def test_batch_write_preflight_leaves_all_bindings_unchanged(
    operation: str, failure: str
) -> None:
    schema = Schema(
        {"a": Schema.leaf(), "b": Schema.leaf(mode="register"), "group": {}}
    )
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"a": 1})
    ctx.register("b", 2)
    target = {"container": "group", "undeclared": "missing", "register": "b"}[failure]
    with pytest.raises(ContextPathError):
        if operation == "update":
            ctx.update({"a": 3, target: 4})
        else:
            ctx.update_tree(["a", target], [3, 4])
    assert ctx.to_dict() == {"$": ctx.get("$").to_dict(), "a": 1, "b": 2}
    ctx.dispose()


def test_update_normalizes_ref_and_string_keys_before_writing() -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"value": 1, schema.resolve("value"): 2})
    assert ctx.get("value") == 2
    ctx.update({"value": 3})
    assert ctx.get("value") == 3
    ctx.dispose()


def test_update_tree_uses_the_last_occurrence_of_each_resolved_path() -> None:
    ctx = Context()
    ctx.declare({"value": Schema.leaf()})
    ctx.update_tree(["value", ctx.resolve("value"), "value"], [1, 2, 3])
    assert ctx.get("value") == 3
    ctx.dispose()


def test_keys_follow_schema_order_and_omit_children_without_visible_leaves() -> None:
    root = Context()
    root.declare(
        {
            "group": {
                "first": {"unset": Schema.leaf(), "value": Schema.leaf()},
                "second": Schema.leaf(),
                "empty": {},
                "unset": Schema.leaf(),
            }
        }
    )
    root.set("group.second", None)
    root.set("group.first.value", False)
    child = root.fork(scope=root.scope.fork())
    child.set("group.second", [])
    view = child.get("group")
    assert tuple(child.keys("group")) == tuple(view.keys()) == ("first", "second")
    assert tuple(view.keys(local=True)) == ("second",)
    assert tuple(view.keys("first")) == ("value",)
    root.dispose()


@pytest.mark.parametrize("operation", ["set", "update", "update_tree"])
def test_assignment_cannot_create_an_empty_registration(operation: str) -> None:
    schema = Schema({"service": Schema.leaf(mode="register")})
    ctx = Context()
    ctx.declare(schema)
    initial_owned = tuple(ctx._lifecycle._owned)
    with pytest.raises(ContextPathError, match="register"):
        if operation == "set":
            ctx.set("service", object())
        elif operation == "update":
            ctx.update({"service": object()})
        else:
            ctx.update_tree(["service"], [object()])
    assert set(ctx._store._data) == {
        ctx.resolve_entry(ref.path) for ref in ctx.get("$").flatten()
    }
    assert tuple(ctx._lifecycle._owned) == initial_owned
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
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"group.value": 1})
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


def test_failed_update_keeps_the_explicitly_created_context_alive() -> None:
    root = Context()
    root.declare({"value": Schema.leaf(), "service": Schema.leaf(mode="register")})
    root.set("value", "original")
    child = root.fork()
    with pytest.raises(ContextPathError, match="register"):
        child.update({"value": "changed", "service": object()})
    assert root.get("value") == "original"
    assert child._lifecycle in root._lifecycle._owned
    assert root._store._scope_usages[root.scope].viewers == {root, child}
    child.update({"value": "valid"})
    assert root.get("value") == "valid"
    child.dispose()
    assert root._store._scope_usages[root.scope].viewers == {root}
    root.dispose()


def test_shared_identity_has_one_registration_and_allows_reinstallation() -> None:
    schema = Schema({"service": Schema.leaf(mode="register")})
    root = Context()
    root.declare(schema)
    root.register("service", "inherited")
    left = root.fork(scope=root.scope.fork())
    left.bind("service", identity="shared")
    left.set_blocked("service", blocked=True)
    right = root.fork(scope=root.scope.fork())
    right.bind("service", identity="shared")
    right.set_blocked("service", blocked=True)
    payload: list[str] = []
    remove = left.register("service", payload)
    with pytest.raises(ContextPathError, match="existing local"):
        right.register("service", object())
    assert not right._lifecycle._owned
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
    ctx = Context()
    ctx.declare(schema)

    class WriteOnRelease:
        def __del__(self) -> None:
            ctx.set("b", "reentrant")

    ctx.set("a", WriteOnRelease())
    ctx.update({"a": "updated", "b": "requested"})
    assert ctx.to_dict() == {
        "$": ctx.get("$").to_dict(),
        "a": "updated",
        "b": "requested",
    }
    ctx.dispose()


@pytest.mark.parametrize("failure", ["path", "iteration"])
def test_drop_validates_and_consumes_all_inputs_before_deleting(failure: str) -> None:
    schema = Schema({"group": {"a": Schema.leaf(), "b": Schema.leaf()}})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"group.a": 1, "group.b": 2})

    def refs() -> Iterable[str]:
        yield "group"
        if failure == "iteration":
            raise RuntimeError("input failed")
        yield "missing"

    error = RuntimeError if failure == "iteration" else ContextPathError
    with pytest.raises(error):
        ctx.drop(refs())
    assert ctx.to_dict() == {"$": ctx.get("$").to_dict(), "group": {"a": 1, "b": 2}}
    ctx.dispose()


@pytest.mark.parametrize("batch", [False, True])
def test_deleting_containers_only_removes_local_values(batch: bool) -> None:
    schema = Schema({"group": {"a": Schema.leaf(), "b": Schema.leaf()}})
    root = Context()
    root.declare(schema)
    root.update({"group.a": 1, "group.b": 2})
    child = root.fork(scope=root.scope.fork())
    child.update({"group.a": 3, "group.b": 4})
    if batch:
        child.drop(["group", "group.a", "group"])
    else:
        child.delete("group")
    assert child.to_dict(local=True) == {}
    assert (
        child.to_dict()
        == root.to_dict()
        == {"$": root.get("$").to_dict(), "group": {"a": 1, "b": 2}}
    )
    root.dispose()


def test_update_assigns_local_values_over_inheritance_and_barriers() -> None:
    schema = Schema({"service": Schema.leaf()})
    root = Context()
    root.declare(schema)
    root.update({"service": "root"})
    child = root.fork(scope=root.scope.fork())
    isolated = root.fork(scope=root.scope.fork())
    isolated.set_blocked("service", blocked=True)
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

    def flatten(box):
        events.append(("flatten", box.value))
        return iter((box.value,)), TreeAux()

    def unflatten(children, aux):
        value = next(iter(children))
        events.append(("unflatten", value))
        return Box(value)

    rules = TreeRules({Box: TreeHandler(flatten, unflatten)})
    schema = Schema({"value": Schema.leaf()})
    ref = schema.resolve("value")
    payload = [object()]
    ctx = Context()
    ctx.effect(lambda: ctx.get(DATA_TREE_REF).add(ctx.scope, rules))
    ctx.declare(schema)
    ctx.update({ref: payload})
    request = Box(ref)
    result = ctx.extract(request)
    assert result is not request
    assert result.value is payload
    assert events == [("flatten", ref), ("unflatten", payload)]
    ctx.dispose()


def test_extract_validates_all_refs_before_reading_values() -> None:
    schema = Schema({"empty": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    with pytest.raises(ContextPathError, match="undeclared"):
        ctx.extract([schema.resolve("empty"), Ref("undeclared")])
    with pytest.raises(BaseExceptionGroup) as caught:
        ref_evaluator(ctx, [schema.resolve("empty"), Ref("undeclared")])
    assert len(caught.value.exceptions) == 2
    assert all(isinstance(error, ContextPathError) for error in caught.value.exceptions)
    assert "empty" in str(caught.value.exceptions[0])
    assert "undeclared" in str(caught.value.exceptions[1])
    ctx.dispose()


def test_view_reads_do_not_require_tree_configuration() -> None:
    ctx = Context()
    ctx.declare({"group": {"empty": Schema.leaf(), "present": Schema.leaf()}})
    child = ctx.fork(scope=Scope())
    child.set("group.present", 1)
    assert not child.exists(DATA_TREE_REF)
    view = child.get("group")
    ref = ctx.resolve("group.present")

    assert view.get("present") == child.get(ref) == 1
    assert not view.exists("empty")
    assert view.get("empty", None) is None
    assert tuple(view.keys()) == ("present",)
    assert view.to_dict() == {"present": 1}
    assert view.flatten() == {ref: 1}
    with pytest.raises(ContextPathError, match="group.undeclared"):
        view.get("undeclared", None)
    with pytest.raises(ContextPathError, match="group.empty"):
        view.get("empty")
    ctx.dispose()


def test_ref_evaluator_uses_get_in_input_order_and_preserves_container_views() -> None:
    seen: list[Ref] = []

    class RecordingContext(Context):
        def get(self, ref, *args, **kwargs):
            seen.append(ref)
            return super().get(ref, *args, **kwargs)

    schema = Schema({"group": {"value": Schema.leaf()}})
    payload = [object()]
    ctx = RecordingContext()
    ctx.declare(schema)
    ctx.update({"group.value": payload})
    refs = [schema.resolve("group.value"), schema.resolve("group")]
    value, view = ref_evaluator(ctx, refs)
    assert seen == refs
    assert value is payload
    assert view.get("value") is payload
    ctx.dispose()


def test_view_flatten_preserves_visibility_and_context_lifetime_checks() -> None:
    root = Context()
    root.declare({"group": {"value": Schema.leaf()}})
    root.set("group.value", "inherited")
    child = root.fork(scope=root.scope.fork())
    view = child.get("group")
    ref = root.resolve("group.value")
    assert view.flatten() == {ref: "inherited"}
    with pytest.raises(ContextPathError):
        view.flatten(local=True)
    child.set(ref, "local")
    assert view.flatten(local=True) == {ref: "local"}
    child.delete(ref)
    assert view.flatten() == {ref: "inherited"}
    root.delete(ref)
    with pytest.raises(ContextPathError):
        view.flatten()
    root_view = root.get("")
    assert root_view.flatten() == root.get("$").flatten()
    child.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        view.flatten()
    root.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        root_view.flatten()


@pytest.mark.parametrize("mode", ["assign", "register"])
def test_barrier_changes_preserve_values_and_registration_tokens(
    mode: Literal["assign", "register"],
) -> None:
    root = Context()
    root.declare({"value": Schema.leaf(mode=mode)})
    if mode == "assign":
        root.set("value", "parent")
    else:
        root.register("value", "parent")
    child = root.fork(scope=root.scope.fork())
    child.set_blocked("value", blocked=True)
    assert not child.exists("value")
    if mode == "assign":
        child.set("value", "child")

        def remove() -> None:
            child.delete("value")

    else:
        remove = child.register("value", "child")
    child.set_blocked("value", blocked=False)
    assert child.get("value") == "child"
    remove()
    assert child.get("value") == "parent"
    child.set_blocked("value", blocked=True)
    assert not child.exists("value")
    child.set_blocked("value", blocked=False)
    assert child.get("value") == "parent"
    root.dispose()


def test_shared_identity_barrier_uses_each_readers_own_ancestry() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "root")
    left_parent = root.fork(scope=root.scope.fork())
    right_parent = root.fork(scope=root.scope.fork())
    left_parent.set("value", "left parent")
    right_parent.set("value", "right parent")
    left = left_parent.fork(scope=left_parent.scope.fork())
    right = right_parent.fork(scope=right_parent.scope.fork())
    owned = tuple(root._lifecycle._owned)
    left.bind("value", identity="shared")
    right.bind("value", identity="shared")
    assert tuple(root._lifecycle._owned) == owned
    assert left.get("value") == "left parent"
    assert right.get("value") == "right parent"

    left.set_blocked("value", blocked=True)
    assert not left.exists("value")
    assert not right.exists("value")
    right.set("value", "shared value")
    assert left.get("value") == "shared value"
    right.set_blocked("value", blocked=False)
    assert left.get("value") == "shared value"
    left.delete("value")
    assert left.get("value") == "left parent"
    assert right.get("value") == "right parent"
    root.dispose()


@pytest.mark.parametrize("inherited", [False, True])
def test_unblocking_unbound_leaf_does_not_allocate_or_choose_identity(
    inherited: bool,
) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    if inherited:
        root.set("value", "parent")
        root.set_blocked("value", blocked=True)
    child = root.fork(scope=root.scope.fork())
    entry = root.resolve_entry("value")
    child.set_blocked("value", blocked=False)
    assert not root._store._scope_usages[child.scope].entries
    if inherited:
        binding = root._store._data[entry]
        assert child.scope not in binding._scope_identities
        assert binding._get_data(root.scope).blocked
        assert child.get("value") == "parent"
    else:
        assert entry not in root._store._data
    child.bind("value", identity="explicit")
    child.set("value", "child")
    root.dispose()


def test_bind_prevalidates_paths_and_does_not_rollback_identity_conflicts() -> None:
    root = Context()
    root.declare({"a": Schema.leaf(), "b": Schema.leaf()})
    child = root.fork(scope=root.scope.fork())
    with pytest.raises(ContextPathError):
        child.bind("a", "missing", identity="shared")
    assert not root._store._scope_usages[child.scope].entries

    child.bind("b", identity="original")
    with pytest.raises(ValueError, match="cannot be rebound"):
        child.bind("a", "b", identity="shared")
    sibling = root.fork(scope=root.scope.fork())
    sibling.bind("a", identity="shared")
    child.set("a", "retained")
    assert sibling.get("a") == "retained"
    child.bind("b", identity="original")
    root.dispose()


@pytest.mark.parametrize("blocked", [False, True])
def test_disposed_context_rejects_barrier_and_binding_changes(blocked: bool) -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    child = root.fork(scope=root.scope.fork())
    child.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        child.set_blocked("value", blocked=blocked)
    with pytest.raises(RuntimeError, match="disposed"):
        child.bind("value", identity="shared")
    root.dispose()
