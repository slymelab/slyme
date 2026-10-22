from __future__ import annotations

import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pytest

from slyme.context import (
    INSPECTION_INTERPRETERS_REF,
    INSPECTION_TREE_RULES_REF,
    INSPECTION_TYPE_NAMES_REF,
    Compose,
    Context,
    ContextPathError,
    Metadata,
    Ref,
    Schema,
)
from slyme.context.inspection import resolve_inspection_options
from slyme.inspection import Interpretation, inspect
from slyme.node import Auto, Node, Wrapper, node, wrapper
from slyme.tree import (
    TreeAux,
    TreeHandler,
    TreeKey,
    TreeResolver,
    TreeRules,
)
from slyme.tree.common import DATA_RULES
from tests.compose_helpers import ValueLayer, collect_values


@pytest.fixture
def observe():
    """Inspect through a fresh configuration Scope for each observation."""
    root = Context()

    def inspect_value(
        value, *, tree_rules=None, interpreters=None, type_names=None, **kwargs
    ):
        child = root.derive()
        try:
            for ref, contribution in (
                (INSPECTION_TREE_RULES_REF, tree_rules),
                (INSPECTION_INTERPRETERS_REF, interpreters),
                (INSPECTION_TYPE_NAMES_REF, type_names),
            ):
                if contribution is not None:
                    child.effect(
                        lambda ref=ref, contribution=contribution: child.get(
                            ref
                        ).register(child.scope, contribution)
                    )
            options = {**resolve_inspection_options(child), **kwargs}
            return inspect(value, **options)
        finally:
            child.dispose()

    try:
        yield inspect_value
    finally:
        root.dispose()


def record(document, ref):
    return document["objects"][ref["$ref"]]


def children(document, ref, role=None):
    return [
        {
            key: display_value(document, value) if key in ("key", "value") else value
            for key, value in entry.items()
        }
        for entry in record(document, ref)["entries"]
        if role is None or entry["role"] == role
    ]


def display_value(document, ref):
    """Test convenience only: inline primitive payloads when reading relations."""
    data = record(document, ref)
    if "value" in data and type(data["value"]) in (type(None), bool, int, float, str):
        return data["value"]
    return ref


def field(document, ref, name):
    return next(
        entry["value"]
        for entry in children(document, ref, "child")
        if key_value(document, entry["key"]) == name
    )


def key_value(document, key):
    if isinstance(key, dict):
        data = record(document, key)
        if data.get("type") in (
            "slyme.tree.attribute_key",
            "slyme.tree.mapping_key",
            "slyme.tree.sequence_key",
        ):
            return children(document, key, "child")[0]["value"]
    return key


def observation_values(document, ref):
    return [
        next(
            entry["value"]
            for entry in children(document, obs["value"])
            if entry["role"] == "value"
        )
        for obs in children(document, ref, "interpretation")
        if record(document, obs["value"])["attributes"]["state"] == "resolved"
    ]


def assert_graph(document):
    values = [document["root"]]
    for obj in document["objects"].values():
        for entry in obj["entries"]:
            values.append(entry["value"])
            if "key" in entry:
                values.append(entry["key"])
    assert all(
        value["$ref"] in document["objects"]
        for value in values
        if isinstance(value, dict)
    )


def test_semantic_types_are_exact_explicit_and_independent_of_labels(observe) -> None:
    class Value:
        pass

    class Child(Value):
        pass

    value = Value()
    type_names = {Value: "example.value"}
    first = observe(value, type_names=type_names)
    Value.__module__ = "renamed.module"
    Value.__qualname__ = "Renamed"
    second = observe(value, type_names=type_names)
    before = record(first, first["root"])
    after = record(second, second["root"])
    assert before["type"] == after["type"] == "example.value"
    assert before["label"] != after["label"]
    assert before["kind"] == "opaque"
    for unknown in (Child(), object()):
        document = observe(unknown, type_names=type_names)
        assert "type" not in record(document, document["root"])
    document = inspect(value, tree_rules=DATA_RULES, type_names=type_names)
    assert record(document, document["root"])["type"] == "example.value"
    document = observe(42, type_names={int: "example.integer"})
    assert record(document, document["root"])["type"] == "example.integer"
    assert record(document, document["root"])["value"] == 42


def test_builtin_semantic_types_and_user_override(observe) -> None:
    from slyme.context.inspection import CONTEXT_INSPECTION_TYPE_NAMES
    from slyme.node.inspection import NODE_INSPECTION_TYPE_NAMES

    assert set(NODE_INSPECTION_TYPE_NAMES.values()) == {
        "slyme.node",
        "slyme.wrapper",
        "slyme.auto",
    }
    assert CONTEXT_INSPECTION_TYPE_NAMES[Ref] == "slyme.ref"

    @node
    def task(ctx, **kwargs):
        pytest.fail("Node body")

    graph = task(value=Auto(Ref("input")))
    document = observe(graph)
    assert {obj.get("type") for obj in document["objects"].values()} >= {
        "slyme.node",
        "slyme.auto",
        "slyme.ref",
    }
    overridden = observe(graph, type_names={Node: "app.task"})
    assert record(overridden, overridden["root"])["type"] == "app.task"
    plain = inspect(graph, tree_rules=DATA_RULES)
    assert "type" not in record(plain, plain["root"])


@pytest.mark.parametrize(
    "environments, count",
    [
        (["".join(["environment", " value"]), "environment value"], 2),
        ([int("1000"), int("1000")], 2),
        ([float("1.25"), float("1.25")], 2),
        ([None, None], 1),
        ([True, 1, 1.0, "1"], 4),
        ([False, 0, 0.0, -0.0], 4),
        ([float("nan"), float("-nan")], 2),
        ([float("inf"), float("inf"), float("-inf")], 3),
        ([[], []], 2),
    ],
)
def test_environment_cache_uses_identity_for_all_types(observe, environments, count):
    @dataclass
    class InEnvironment:
        env: object

    class Value:
        pass

    value = Value()
    calls = []

    def interpret_value(value, env):
        calls.append(env)
        return Interpretation(len(calls))

    document = observe(
        [InEnvironment(env) for env in environments],
        interpreters={
            InEnvironment: lambda item, env: Interpretation(value, env=item.env),
            Value: interpret_value,
        },
        type_names={Value: "example.value"},
    )
    assert len(calls) == count
    source = next(
        {"$ref": key}
        for key, obj in document["objects"].items()
        if obj.get("type") == "example.value"
    )
    assert observation_values(document, source) == list(range(1, count + 1))
    assert_graph(document)


def test_equal_but_distinct_environment_override_is_an_environment_change(
    observe,
) -> None:
    env = "".join(["environment", " value"])
    replacement = "environment value"
    assert env == replacement and env is not replacement
    document = observe(
        object(),
        env=env,
        interpreters={object: lambda value, env: Interpretation(42, env=replacement)},
    )
    observation = children(document, document["root"], "interpretation")[0]["value"]
    assert "value_env" in {entry["role"] for entry in children(document, observation)}


@pytest.mark.parametrize("env, replacement", [(1, 1.0), (True, 1), (0.0, -0.0)])
def test_numeric_environment_changes_retain_original_values(observe, env, replacement):
    document = observe(
        object(),
        env=env,
        interpreters={object: lambda value, env: Interpretation(42, env=replacement)},
    )
    observation = children(document, document["root"], "interpretation")[0]["value"]
    fields = {
        entry["role"]: entry["value"] for entry in children(document, observation)
    }
    for role, original in (("env", env), ("value_env", replacement)):
        actual = fields[role]
        assert actual is original
    assert fields["value"] == 42


def relationship_document(document):
    """Snapshot relationships only, excluding raw payloads and display labels."""
    ids = {}
    pending = []

    def value(item):
        if not isinstance(item, dict):
            return item
        old = item["$ref"]
        if old not in ids:
            ids[old] = str(len(ids))
            pending.append(old)
        return {"$ref": ids[old]}

    result = {**document, "root": value(document["root"]), "objects": {}}
    for old in pending:
        obj = document["objects"][old]
        entries = [
            {key: item if key == "role" else value(item) for key, item in entry.items()}
            for entry in obj["entries"]
        ]
        result["objects"][ids[old]] = {
            **{key: item for key, item in obj.items() if key not in ("label", "value")},
            "entries": entries,
        }
    return result


def assert_relationship_example(name, document):
    expected = json.loads(
        (Path(__file__).parent / "fixtures" / "inspection" / f"{name}.json").read_text()
    )
    assert_graph(document)
    assert relationship_document(document) == expected


def test_relationship_node_auto_ref(observe) -> None:
    @node
    def task(ctx, **kwargs):
        pytest.fail("Node body")

    ctx = Context()
    try:
        ctx.declare({"input": Schema.leaf()})
        ctx.set("input", 42)
        assert_relationship_example(
            "node-auto-ref", observe(task(value=Auto(Ref("input"))), env=ctx)
        )
    finally:
        ctx.dispose()


def test_relationship_shared_cycle(observe) -> None:
    shared = {"value": 42}
    root = [shared, shared]
    root.append(root)
    assert_relationship_example("shared-cycle", observe(root))


def test_relationship_multiple_environments(observe) -> None:
    @dataclass
    class InEnvironment:
        env: str

    shared = Ref("input")
    document = observe(
        [InEnvironment("first"), InEnvironment("second")],
        type_names={InEnvironment: "example.in_environment"},
        interpreters={
            InEnvironment: lambda item, env: Interpretation(shared, env=item.env),
            Ref: lambda ref, env: Interpretation(env),
        },
    )
    assert_relationship_example("multiple-environments", document)


def test_relationship_incomplete_results(observe) -> None:
    @dataclass
    class Value:
        available: bool

    document = observe(
        [Value(False), Value(True), "long text"],
        type_names={Value: "example.value"},
        interpreters={
            Value: lambda value, env: Interpretation(None) if value.available else None
        },
    )
    assert_relationship_example("incomplete-results", document)


def test_values_shared_identity_and_cycles(observe) -> None:
    shared = {"$ref": "business value", 3: False, None: 4.5}
    root = [shared, shared, MappingProxyType(shared)]
    root.append(root)
    document = observe(root)
    items = children(document, document["root"])
    assert items[0]["value"] == items[1]["value"]
    assert items[3]["value"] == document["root"]
    assert field(document, items[0]["value"], "$ref") == "business value"
    assert field(document, items[2]["value"], 3) is False
    assert_graph(document)


def test_deep_graph_uses_no_recursive_traversal(observe) -> None:
    root = current = []
    for _ in range(2500):
        child = []
        current.append(child)
        current = child
    document = observe(root)
    assert len(document["objects"]) == 5002  # Containers, keys and shared index zero.
    assert_graph(document)


def test_opaque_objects_do_not_run_repr_getattr_iter_or_await(observe) -> None:
    class Opaque:
        def __repr__(self):
            pytest.fail("repr")

        def __getattr__(self, name):
            pytest.fail("getattr")

        def __iter__(self):
            pytest.fail("iter")

        def __await__(self):
            pytest.fail("await")

    document = observe(Opaque())
    assert record(document, document["root"])["kind"] == "opaque"
    assert_graph(document)


def test_node_and_wrapper_bindings_expand_without_temporary_compose_edges(
    observe,
) -> None:
    @node
    def task(ctx, **kwargs):
        pytest.fail("Node body")

    @wrapper
    def around(ctx, wrapped, call_next, **kwargs):
        pytest.fail("Wrapper body")

    nested = task()
    inner = around(child=nested)
    outer = around(other=inner)
    root = task(child=inner)
    root.add_wrappers(outer)
    before = observe(root)
    temporary = Wrapper.compose([inner], wrapped=root, call_next=root)
    assert observe(root) == before
    assert record(observe(temporary), {"$ref": "0"})["kind"] == "opaque"
    wrapped_ref = field(before, field(before, before["root"], "params"), "child")
    node_ref = field(before, field(before, wrapped_ref, "params"), "child")
    assert record(before, node_ref)["label"].endswith(".Node")
    other_wrapper = children(before, field(before, before["root"], "wrappers"))[0][
        "value"
    ]
    assert field(before, field(before, other_wrapper, "params"), "other") == wrapped_ref
    assert_graph(before)


def test_auto_nested_refs_and_compose_queries_expand_dynamic_nodes(observe) -> None:
    @node
    def task(ctx, **kwargs):
        pytest.fail("Node body")

    root = Context()
    root.declare({"task": Schema.leaf(), "missing": Schema.leaf()})
    leaf = task(value=19)
    root.set("task", leaf)
    composition = Compose(factory=ValueLayer, query=collect_values)
    remove = composition.register(root.scope, task(next=Auto({"node": Ref("task")})))
    graph = task(items=Auto({"refs": [Ref("task")], "composition": composition}))
    document = observe(graph, env=root)
    assert any(
        key_value(document, entry.get("key")) == "value"
        and display_value(document, entry["value"]) == 19
        for obj in document["objects"].values()
        for entry in obj["entries"]
    )
    # An env annotation references the Context but does not expand all its bindings.
    contexts = [
        obj
        for obj in document["objects"].values()
        if obj.get("label", "").endswith(".Context")
    ]
    assert len(contexts) == 1 and contexts[0]["entries"] == []
    assert_graph(document)
    with pytest.raises(ContextPathError):
        observe(task(items=Auto(Ref("missing"))), env=root)
    remove()
    root.dispose()


def test_same_ref_and_shared_container_are_observed_in_each_environment(
    observe,
) -> None:
    @dataclass
    class InContext:
        value: object
        env: object

    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", "root")
    derived = root.derive()
    derived.set("value", "derived")
    shared_ref = Ref("value")
    shared_tree = {"items": [shared_ref, shared_ref]}
    inputs = [InContext(shared_tree, root), InContext(shared_tree, derived)]
    document = observe(
        inputs,
        interpreters={
            InContext: lambda value, env: Interpretation(value.value, env=value.env)
        },
    )
    refs = [
        ref
        for ref, obj in document["objects"].items()
        if obj.get("label", "").endswith(".Ref")
    ]
    assert len(refs) == 1
    assert observation_values(document, {"$ref": refs[0]}) == ["root", "derived"]
    assert_graph(document)
    root.dispose()


def test_queries_run_once_per_observation_and_caches_do_not_survive_the_call(
    observe,
) -> None:
    ctx = Context()
    calls = []

    def query(layers):
        calls.append(None)
        return collect_values(layers)

    composition = Compose(factory=ValueLayer, query=query)
    remove = composition.register(ctx.scope, 1)
    first = observe([composition, composition], env=ctx)
    assert len(calls) == 1
    remove()
    second = observe([composition, composition], env=ctx)
    assert len(calls) == 2
    assert first != second

    for env in (None, ctx):
        document = observe(Compose(factory=ValueLayer), env=env)
        assert observation_values(document, document["root"]) == []
    document = observe(
        composition, env=ctx, interpreters={Compose: lambda value, env: None}
    )
    assert len(calls) == 2
    assert observation_values(document, document["root"]) == []
    ctx.dispose()


def test_context_and_view_choose_their_own_environment(observe) -> None:
    root = Context()
    root.declare({"settings": {"value": Schema.leaf()}})
    root.set("settings.value", "root")
    child = root.derive()
    child.set("settings.value", "child")
    view = child.get("settings")
    assert view.context is child and view.path == "settings"
    document = observe(view, env=root)
    projection = observation_values(document, document["root"])[0]
    assert children(document, projection)[0]["value"] == "child"
    assert not any(
        key_value(document, entry.get("key")) == "children"
        for obj in document["objects"].values()
        for entry in obj["entries"]
    )

    document = observe(root)
    assert field(document, document["root"], "state") == "active"
    assert len(children(document, field(document, document["root"], "children"))) == 1
    assert_graph(document)
    root.dispose()
    assert root.state == child.state == "disposed"
    with pytest.raises(RuntimeError):
        observe(root)


def test_interpretation_inherits_overrides_and_clears_environment(observe) -> None:
    class Value:
        pass

    class Link:
        pass

    token = object()
    value = Value()
    link = Link()
    observed_envs = []

    def read_env(value, env):
        observed_envs.append(env)
        return Interpretation("observed")

    document = observe(
        link,
        env=token,
        interpreters={
            Value: read_env,
            Link: lambda link, env: Interpretation(value),
        },
    )
    assert observed_envs == [token]
    assert_graph(document)
    # A direct self observation must terminate; explicit None is not inheritance.
    seen = []

    def interpret(value, env):
        seen.append(env)
        return Interpretation(value, env=None)

    document = observe(value, env=token, interpreters={Value: interpret})
    assert seen == [token, None]
    assert len(children(document, document["root"], "interpretation")) == 2
    assert_graph(document)


def test_none_result_and_unavailable_interpretation_are_distinct(observe) -> None:
    class Available:
        pass

    class Unavailable:
        pass

    document = observe(
        [Available(), Unavailable()],
        interpreters={
            Available: lambda value, env: Interpretation(None),
            Unavailable: lambda value, env: None,
        },
    )
    refs = children(document, document["root"])
    assert observation_values(document, refs[0]["value"]) == [None]
    assert observation_values(document, refs[1]["value"]) == []


def test_resolver_only_controls_children_and_rules_match_exact_types(observe) -> None:
    class Box:
        pass

    class SubBox(Box):
        pass

    handler = TreeHandler(lambda value: ((1,), TreeAux()), None)
    tree_rules = TreeRules({Box: handler})
    interpreters = {Box: lambda value, env: Interpretation(2)}
    document = observe(
        Box(),
        tree_rules=tree_rules,
        interpreters=interpreters,
        tree_resolver=TreeResolver(lambda value: True),
    )
    assert observation_values(document, document["root"]) == [2]
    assert children(document, document["root"], "child") == []
    document = observe(SubBox(), tree_rules=tree_rules, interpreters=interpreters)
    assert record(document, document["root"])["kind"] == "opaque"
    with pytest.raises(ValueError, match="takes_aux=False"):
        observe(
            [], tree_resolver=TreeResolver(lambda value, aux: False, takes_aux=True)
        )


def test_custom_metadata_rules_and_custom_tree_keys_do_not_need_path_resolution(
    observe,
) -> None:
    @dataclass(frozen=True)
    class Description(Metadata):
        text: str

    class Key(TreeKey):
        def resolve(self, element):
            pytest.fail("resolve")

        def codify(self, element):
            pytest.fail("codify")

    tree_rules = TreeRules(
        {
            Description: TreeHandler(
                lambda value: ((value.text,), TreeAux(children_keys=(Key(),))), None
            )
        }
    )

    @node(metadata={"description": Description("example")})
    def task(ctx):
        return 1

    document = observe(task(), tree_rules=tree_rules)
    description = field(
        document, field(document, document["root"], "metadata"), "description"
    )
    assert children(document, description)[0]["value"] == "example"
    assert children(document, description)[0]["role"] == "child"
    assert_graph(document)


@pytest.mark.parametrize(
    "failure_at", ["handler", "iterator", "interpreter", "resolver"]
)
def test_adapter_errors_propagate_without_stringifying(observe, failure_at) -> None:
    class Error(Exception):
        def __str__(self):
            pytest.fail("exception str")

    class Broken:
        pass

    error = Error()
    cause = ValueError("original cause")

    def fail():
        raise error from cause

    def flatten(value):
        if failure_at == "handler":
            fail()

        def values():
            yield 1
            if failure_at == "iterator":
                fail()

        return values(), TreeAux()

    def interpret(value, env):
        if failure_at == "interpreter":
            fail()

    def resolve(value):
        if failure_at == "resolver":
            fail()
        return False

    with pytest.raises(Error) as caught:
        observe(
            Broken(),
            tree_rules=TreeRules({Broken: TreeHandler(flatten, None)}),
            tree_resolver=TreeResolver(resolve),
            interpreters={Broken: interpret},
        )
    assert caught.value is error
    assert error.__cause__ is cause
    notes = getattr(error, "__notes__", error.args)
    operation = "interpretation" if failure_at == "interpreter" else "structure"
    assert any(f"Inspection {operation}:" in note for note in notes)


def test_base_exceptions_are_not_swallowed(observe) -> None:
    class Stop:
        pass

    def interpret(value, env):
        raise KeyboardInterrupt()

    with pytest.raises(KeyboardInterrupt):
        observe(Stop(), interpreters={Stop: interpret})


@pytest.mark.parametrize(
    "limits",
    [
        {"budget": 1},
        {"budget": 5},
        {"budget": 12},
        {"budget": 20, "max_depth": 2},
    ],
)
def test_limits_make_explicit_truncations_without_dangling_references(
    observe, limits
) -> None:
    class Fresh:
        pass

    document = observe(
        [Fresh(), [[], [], []]],
        interpreters={Fresh: lambda value, env: Interpretation(Fresh())},
        **limits,
    )
    assert (
        len(document["objects"])
        + sum(len(obj["entries"]) for obj in document["objects"].values())
        <= limits["budget"]
    )
    assert any(
        obj.get("attributes", {}).get("truncated")
        for obj in document["objects"].values()
    )
    assert_graph(document)


def test_infinite_children_and_fresh_interpretations_are_bounded(observe) -> None:
    class Infinite:
        pass

    class Fresh:
        pass

    document = observe(
        Infinite(),
        tree_rules=TreeRules(
            {Infinite: TreeHandler(lambda value: (itertools.count(), TreeAux()), None)}
        ),
        budget=21,
    )
    assert len(children(document, document["root"])) == 10
    calls = []

    def interpret(value, env):
        calls.append(value)
        return Interpretation(Fresh())

    document = observe(Fresh(), interpreters={Fresh: interpret}, budget=20)
    assert len(calls) <= 10
    assert_graph(document)


def test_raw_values_are_retained_without_serialization_or_truncation(
    observe,
) -> None:
    values = [None, True, 2**80, float("nan"), float("inf"), float("-inf"), "x" * 20000]
    document = observe(values)
    assert "value" not in record(document, document["root"])
    entries = record(document, document["root"])["entries"]
    for entry, value in zip(entries, values, strict=True):
        data = record(document, entry["value"])
        assert data["value"] is value
        assert data["kind"] == "opaque"
        assert "attributes" not in data
    assert_graph(document)


def test_raw_mutable_value_is_not_a_copy_of_observed_structure(observe):
    value = [1]
    document = observe(value, raw_value_types={list, int})
    value.append(2)
    assert record(document, document["root"])["value"] is value
    assert len(children(document, document["root"])) == 1


def test_raw_value_retention_is_exact_and_independent_of_dispatch():
    class Text(str):
        pass

    value = Text("content")
    rules = TreeRules({Text: TreeHandler(lambda _: ((42,), TreeAux()), None)})
    options = {
        "tree_rules": rules,
        "interpreters": {Text: lambda value, env: Interpretation("meaning")},
        "type_names": {Text: "example.text"},
    }
    without = inspect(value, **options)
    inherited = inspect(value, **options, raw_value_types={str})
    retained = inspect(value, **options, raw_value_types={Text})
    for document in (without, inherited, retained):
        source = record(document, document["root"])
        assert source["type"] == "example.text"
        assert [entry["role"] for entry in source["entries"]] == [
            "child",
            "interpretation",
        ]
    assert "value" not in record(without, without["root"])
    assert "value" not in record(inherited, inherited["root"])
    assert record(retained, retained["root"])["value"] is value
    assert relationship_document(without) == relationship_document(retained)


def test_omitted_values_are_not_retained_by_the_returned_graph():
    import weakref

    class Value:
        pass

    value = Value()
    reference = weakref.ref(value)
    document = inspect(value, tree_rules=TreeRules())
    del value
    assert reference() is None
    assert "value" not in record(document, document["root"])


def test_retention_does_not_validate_json_values(observe):
    document = observe(float("nan"))
    with pytest.raises(ValueError):
        json.dumps(document, allow_nan=False)


def test_value_and_environment_identity_never_require_hashing_or_equality():
    class Value:
        def __eq__(self, other):
            pytest.fail("equality")

        def __hash__(self):
            pytest.fail("hashing")

    value, env = Value(), Value()
    calls = []

    def interpret(item, current_env):
        calls.append((item, current_env))
        return Interpretation(item, env=current_env)

    document = inspect(
        value,
        env=env,
        tree_rules=TreeRules(),
        interpreters={Value: interpret},
        raw_value_types={Value},
    )
    assert len(calls) == 1
    observation = children(document, document["root"], "interpretation")[0]["value"]
    fields = {
        entry["role"]: entry["value"] for entry in children(document, observation)
    }
    assert record(document, fields["env"])["value"] is env
    assert fields["value"] == document["root"]
    assert "value_env" not in fields


def test_rules_can_replace_defaults_and_inspect_has_no_framework_defaults(
    observe,
) -> None:
    document = observe(
        [1, 2],
        tree_rules=TreeRules({list: TreeHandler(lambda value: ((), TreeAux()), None)}),
    )
    assert children(document, document["root"]) == []
    document = inspect(Ref("value"), tree_rules=DATA_RULES)
    assert record(document, document["root"])["kind"] == "opaque"


@pytest.mark.parametrize("limits", [{"budget": 0}, {"budget": -1}, {"max_depth": -1}])
def test_invalid_limits_are_rejected(observe, limits) -> None:
    with pytest.raises(ValueError):
        observe([], **limits)
