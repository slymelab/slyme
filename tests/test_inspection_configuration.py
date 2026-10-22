from __future__ import annotations

import json
from dataclasses import dataclass

import pytest

from slyme.context import (
    INSPECTION_INTERPRETERS_REF,
    INSPECTION_RAW_VALUE_TYPES_REF,
    INSPECTION_TREE_RULES_REF,
    INSPECTION_TYPE_NAMES_REF,
    Context,
    ContextPathError,
    Ref,
    Schema,
    Scope,
    ScopeBinding,
)
from slyme.context.inspection import resolve_inspection_options
from slyme.inspection import Interpretation, inspect
from slyme.inspection.common import SCALAR_TYPES
from slyme.node import Auto, node
from slyme.tree import TreeAux, TreeHandler, TreeRules


@pytest.fixture
def root():
    ctx = Context()
    try:
        yield ctx
    finally:
        ctx.dispose()


class Value:
    pass


def root_record(document):
    return document["objects"][document["root"]["$ref"]]


def test_raw_value_types_are_scoped_contributions_and_snapshots(root):
    compose = root.get(INSPECTION_RAW_VALUE_TYPES_REF)
    assert set(compose.resolve(root.scope)) == SCALAR_TYPES
    child = root.derive()
    selected = {Value: None}
    remove = child.effect(lambda: compose.register(child.scope, selected))
    options = resolve_inspection_options(child)
    selected.clear()
    assert set(options["raw_value_types"]) == SCALAR_TYPES | {Value}
    assert set(compose.resolve(root.scope)) == SCALAR_TYPES
    value = Value()
    assert root_record(inspect(value, **options))["value"] is value
    assert "value" not in root_record(
        inspect(value, **resolve_inspection_options(root))
    )
    remove()
    assert "value" not in root_record(
        inspect(value, **resolve_inspection_options(child))
    )
    assert root_record(inspect(value, **options))["value"] is value
    child.effect(lambda: compose.register(child.scope, {Value: None}))
    child.dispose()
    assert set(compose.resolve(child.scope)) == SCALAR_TYPES


def test_raw_value_types_can_replace_inherited_defaults(root):
    compose = root.get(INSPECTION_RAW_VALUE_TYPES_REF)
    child = root.derive(bindings={compose: ScopeBinding(blocked=True)})
    remove = child.effect(lambda: compose.register(child.scope, {str: None}))
    assert resolve_inspection_options(child)["raw_value_types"] == {str: None}
    assert (
        root_record(inspect("text", **resolve_inspection_options(child)))["value"]
        == "text"
    )
    assert "value" not in root_record(inspect(42, **resolve_inspection_options(child)))
    remove()
    assert resolve_inspection_options(child)["raw_value_types"] == {}
    assert set(compose.resolve(root.scope)) == SCALAR_TYPES


def test_raw_value_retention_can_be_unrestricted_without_changing_context_defaults(
    root,
):
    value = Value()
    options = resolve_inspection_options(root)
    graph = inspect(
        value,
        tree_rules=options["tree_rules"],
        interpreters=options["interpreters"],
        type_names=options["type_names"],
        raw_value_types=None,
    )
    assert root_record(graph)["value"] is value
    assert set(options["raw_value_types"]) == SCALAR_TYPES
    assert "value" not in root_record(
        inspect(value, **resolve_inspection_options(root))
    )


def test_default_options_allow_json_for_node_and_context_graphs(root):
    @node
    def task(ctx, **kwargs):
        pytest.fail("Node execution")

    root.declare({"input": Schema.leaf()})
    root.set("input", 42)
    for value in (root, task(value=Auto(Ref("input")))):
        graph = inspect(value, **resolve_inspection_options(root), env=root)
        assert json.loads(json.dumps(graph, allow_nan=False)) == graph
        assert "value" not in root_record(graph)


@pytest.mark.parametrize(
    "ref, make, entries, builtin",
    [
        (
            INSPECTION_TREE_RULES_REF,
            lambda label: TreeRules(
                {Value: TreeHandler(lambda _: ((label,), TreeAux()), None)}
            ),
            lambda value: value.handlers,
            Ref,
        ),
        (
            INSPECTION_INTERPRETERS_REF,
            lambda label: {Value: lambda value, env: Interpretation(label)},
            lambda value: value,
            Ref,
        ),
        (
            INSPECTION_TYPE_NAMES_REF,
            lambda label: {Value: label},
            lambda value: value,
            Ref,
        ),
        (
            INSPECTION_RAW_VALUE_TYPES_REF,
            lambda label: {Value: None},
            lambda value: value,
            str,
        ),
    ],
)
def test_configuration_is_root_owned_scoped_and_reversible(
    root, ref, make, entries, builtin
):
    other = Context()
    try:
        compose = root.get(ref)
        assert compose is not other.get(ref)
        assert builtin in entries(compose.resolve(root.scope))
        left, right = root.derive(), root.derive()
        joined = root.fork(scope=Scope(parents=(left.scope, right.scope)))
        first, second = make("first"), make("second")
        remove = left.effect(lambda: compose.register(left.scope, first))
        right.effect(lambda: compose.register(right.scope, second))
        assert entries(compose.resolve(joined.scope))[Value] is entries(first)[Value]
        assert Value not in entries(compose.resolve(root.scope))
        assert Value not in entries(other.get(ref).resolve(other.scope))
        with pytest.raises(ValueError, match="already registered"):
            compose.register(left.scope, second)
        remove()
        assert entries(compose.resolve(joined.scope))[Value] is entries(second)[Value]
        right.dispose()
        assert Value not in entries(compose.resolve(joined.scope))
        root.dispose()
        assert not tuple(compose.layers())
    finally:
        other.dispose()


def test_empty_compositions_do_not_reinstall_defaults(root):
    refs = (
        INSPECTION_TREE_RULES_REF,
        INSPECTION_INTERPRETERS_REF,
        INSPECTION_TYPE_NAMES_REF,
        INSPECTION_RAW_VALUE_TYPES_REF,
    )
    isolated = root.derive(
        bindings={root.get(ref): ScopeBinding(blocked=True) for ref in refs}
    )
    value = Ref("value")
    document = inspect(value, **resolve_inspection_options(isolated), env=root)
    assert root_record(document)["kind"] == "opaque"
    assert root_record(document)["entries"] == []
    assert "type" not in root_record(document)
    assert resolve_inspection_options(isolated)["raw_value_types"] == {}
    assert "value" not in root_record(
        inspect(42, **resolve_inspection_options(isolated))
    )
    assert (
        root_record(inspect(value, **resolve_inspection_options(root)))["type"]
        == "slyme.ref"
    )

    type_names = isolated.get(INSPECTION_TYPE_NAMES_REF)
    remove = isolated.effect(
        lambda: type_names.register(isolated.scope, {Ref: "custom.ref"})
    )
    assert (
        root_record(inspect(value, **resolve_inspection_options(isolated)))["type"]
        == "custom.ref"
    )
    remove()
    assert "type" not in root_record(
        inspect(value, **resolve_inspection_options(isolated))
    )


def test_unrelated_scope_does_not_fall_back_to_root_configuration(root):
    detached = root.fork(scope=Scope())
    with pytest.raises(ContextPathError):
        inspect([], **resolve_inspection_options(detached))


def test_interpretation_environment_does_not_select_configuration(root):
    @dataclass
    class Link:
        env: Context

    other = Context()
    try:
        other.declare({"value": Schema.leaf()})
        other.set("value", 42)
        type_names = other.get(INSPECTION_TYPE_NAMES_REF)
        modified = other.derive()
        modified.effect(lambda: type_names.register(modified.scope, {Ref: "other.ref"}))
        interpreters = root.get(INSPECTION_INTERPRETERS_REF)
        root.effect(
            lambda: interpreters.register(
                root.scope,
                {Link: lambda value, env: Interpretation(Ref("value"), env=value.env)},
            )
        )
        document = inspect(Link(modified), **resolve_inspection_options(root))
        assert any(
            obj.get("type") == "slyme.ref" for obj in document["objects"].values()
        )
        assert not any(
            obj.get("type") == "other.ref" for obj in document["objects"].values()
        )
        assert any(
            entry["role"] == "value"
            and document["objects"][entry["value"]["$ref"]].get("value") == 42
            for obj in document["objects"].values()
            for entry in obj["entries"]
        )
        observed = inspect(modified, **resolve_inspection_options(root))
        assert root_record(observed)["type"] == "slyme.context"
        other.dispose()
        with pytest.raises(RuntimeError):
            inspect(other, **resolve_inspection_options(root))
    finally:
        other.dispose()


def test_configuration_is_captured_before_traversal(root):
    interpreters = root.get(INSPECTION_INTERPRETERS_REF)
    type_names = root.get(INSPECTION_TYPE_NAMES_REF)
    remove_interpreter = root.effect(
        lambda: interpreters.register(
            root.scope, {Value: lambda value, env: Interpretation(42)}
        )
    )
    remove_type = root.effect(
        lambda: type_names.register(root.scope, {Value: "example.value"})
    )

    def children(value):
        remove_interpreter()
        remove_type()
        return (), TreeAux()

    root.effect(
        lambda: root.get(INSPECTION_TREE_RULES_REF).register(
            root.scope, TreeRules({Value: TreeHandler(children, None)})
        )
    )
    first = root_record(inspect(Value(), **resolve_inspection_options(root)))
    assert first["type"] == "example.value"
    assert any(entry["role"] == "interpretation" for entry in first["entries"])
    second = root_record(inspect(Value(), **resolve_inspection_options(root)))
    assert "type" not in second
    assert second["entries"] == []


def test_options_only_resolve_configuration_and_can_outlive_its_owner(root):
    calls = []

    def interpret(value, env):
        calls.append(env)
        return Interpretation(42)

    root.effect(
        lambda: root.get(INSPECTION_INTERPRETERS_REF).register(
            root.scope, {Value: interpret}
        )
    )
    options = resolve_inspection_options(root)
    assert type(options) is dict
    assert set(options) == {
        "tree_rules",
        "interpreters",
        "type_names",
        "raw_value_types",
    }
    assert calls == []
    root.dispose()
    document = inspect(Value(), **options)
    assert calls == [None]
    assert any(
        entry["role"] == "interpretation" for entry in root_record(document)["entries"]
    )


def test_options_are_independent_shallow_snapshots(root):
    first = resolve_inspection_options(root)
    first["type_names"][Ref] = "custom.ref"
    first["interpreters"].clear()
    first["raw_value_types"].clear()
    second = resolve_inspection_options(root)
    assert second["type_names"][Ref] == "slyme.ref"
    assert Ref in second["interpreters"]
    assert set(second["raw_value_types"]) == SCALAR_TYPES
    assert first["tree_rules"].handlers[Ref] is second["tree_rules"].handlers[Ref]
