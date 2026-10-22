from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import pytest

from slyme.inspection import Interpretation, inspect
from slyme.inspection.common import SCALAR_TYPES
from slyme.tree import (
    AttributeKey,
    TreeAux,
    TreeHandler,
    TreeKey,
    TreeResolver,
    TreeRules,
)
from slyme.tree.common import DATA_RULES


class Value:
    pass


@pytest.mark.parametrize(
    "value", [None, False, 42, "text", [], {}, Value(), lambda: None]
)
def test_raw_value_types_none_retains_any_original_value(value):
    document = inspect(value, tree_rules=TreeRules(), raw_value_types=None)
    assert record(document, document["root"])["value"] is value
    for options in ({}, {"raw_value_types": ()}):
        document = inspect(value, tree_rules=TreeRules(), **options)
        assert "value" not in record(document, document["root"])


def test_unrestricted_raw_values_preserve_cycles_and_interpretation_values():
    source = Value()
    env = object()
    result = object()
    values = [source]
    values.append(values)
    document = inspect(
        values,
        env=env,
        tree_rules=DATA_RULES,
        interpreters={Value: lambda value, env: Interpretation(result)},
        raw_value_types=None,
    )
    root = record(document, document["root"])
    assert root["value"] is values
    assert root["entries"][1]["value"] == document["root"]
    source_record = record(document, root["entries"][0]["value"])
    observation = record(document, source_record["entries"][0]["value"])
    assert "value" not in observation
    assert record(document, observation["entries"][0]["value"])["value"] is env
    assert record(document, observation["entries"][1]["value"])["value"] is result
    assert all(
        "value" in data
        for data in document["objects"].values()
        if data is not observation
    )


def record(document, ref):
    return document["objects"][ref["$ref"]]


def payload(document, ref):
    return record(document, ref)["value"]


def child_values(document, ref):
    return [
        payload(document, entry["value"])
        for entry in record(document, ref)["entries"]
        if entry["role"] == "child"
    ]


def observations(document, ref):
    return [
        record(document, entry["value"])
        for entry in record(document, ref)["entries"]
        if entry["role"] == "interpretation"
    ]


@pytest.mark.parametrize("value", [[], {}, lambda: None, Value()])
def test_structure_representation_depends_on_handler_not_python_type(value):
    document = inspect(
        value,
        raw_value_types=SCALAR_TYPES,
        tree_rules=TreeRules(),
        tree_resolver=TreeResolver(
            lambda item: (
                TreeHandler(lambda _: ((42,), TreeAux()), None)
                if item is value
                else False
            )
        ),
    )
    assert record(document, document["root"])["kind"] == "object"
    assert child_values(document, document["root"]) == [42]
    opaque = inspect(value, raw_value_types=SCALAR_TYPES, tree_rules=TreeRules())
    assert record(opaque, opaque["root"])["kind"] == "opaque"


def test_tree_keys_preserve_identity_and_use_both_dispatch_paths():
    class Key(TreeKey):
        def resolve(self, element):
            pytest.fail("key resolution")

        def codify(self, parent_expr):
            pytest.fail("key formatting")

    key = Key()
    source = Value()
    calls = []

    def interpret(value, env):
        calls.append((value, env))
        return Interpretation(source)

    document = inspect(
        source,
        env="env",
        raw_value_types=SCALAR_TYPES,
        tree_rules=TreeRules(
            {
                Value: TreeHandler(
                    lambda _: ((1, 2), TreeAux(children_keys=(key, key))), None
                ),
                Key: TreeHandler(lambda _: (("key data",), TreeAux()), None),
            }
        ),
        interpreters={Key: interpret},
    )
    entries = record(document, document["root"])["entries"]
    assert entries[0]["key"] == entries[1]["key"]
    key_ref = entries[0]["key"]
    assert child_values(document, key_ref) == ["key data"]
    assert (
        observations(document, key_ref)[0]["entries"][-1]["value"] == document["root"]
    )
    assert calls == [(key, "env")]


def test_standard_key_descriptions_are_optional_overridable_rules():
    from slyme.inspection.common import (
        KEY_INSPECTION_TREE_RULES,
        KEY_INSPECTION_TYPE_NAMES,
    )

    key = AttributeKey("name")
    opaque = inspect(key, raw_value_types=SCALAR_TYPES, tree_rules=TreeRules())
    assert record(opaque, opaque["root"])["kind"] == "opaque"
    described = inspect(
        key,
        raw_value_types=SCALAR_TYPES,
        tree_rules=KEY_INSPECTION_TREE_RULES,
        type_names=KEY_INSPECTION_TYPE_NAMES,
    )
    assert record(described, described["root"])["type"] == "slyme.tree.attribute_key"
    assert child_values(described, described["root"]) == ["name"]
    overridden = inspect(
        key,
        raw_value_types=SCALAR_TYPES,
        tree_rules=KEY_INSPECTION_TREE_RULES,
        tree_resolver=TreeResolver(lambda _: True),
    )
    assert record(overridden, overridden["root"])["kind"] == "opaque"


@pytest.mark.parametrize("stop_tree", [False, True])
@pytest.mark.parametrize("stop_interpret", [False, True])
def test_resolvers_independently_control_structure_and_interpretation(
    stop_tree, stop_interpret
):
    calls = []

    def interpret(value, env):
        calls.append(env)
        return Interpretation("meaning")

    document = inspect(
        Value(),
        env="environment",
        raw_value_types=SCALAR_TYPES,
        tree_rules=TreeRules(
            {Value: TreeHandler(lambda _: (("stored",), TreeAux()), None)}
        ),
        tree_resolver=TreeResolver(lambda value: stop_tree),
        interpreters={Value: interpret},
        interpreter_resolver=lambda value, env: stop_interpret,
    )
    assert child_values(document, document["root"]) == ([] if stop_tree else ["stored"])
    assert len(observations(document, document["root"])) == (0 if stop_interpret else 1)
    assert calls == ([] if stop_interpret else ["environment"])


def test_interpreter_resolver_overrides_or_supplies_an_interpreter():
    first, second = Value(), Value()

    def override(value, env):
        return Interpretation("override:" + env)

    def fallback(value, env):
        return Interpretation("fallback:" + env)

    def select(value, env):
        return override if value is first else False

    for rules in ({}, {Value: fallback}):
        document = inspect(
            [first, second],
            env="env",
            raw_value_types=SCALAR_TYPES,
            tree_rules=DATA_RULES,
            interpreters=rules,
            interpreter_resolver=select,
        )
        refs = [
            entry["value"] for entry in record(document, document["root"])["entries"]
        ]
        assert (
            payload(
                document, observations(document, refs[0])[0]["entries"][-1]["value"]
            )
            == "override:env"
        )
        if rules:
            assert (
                payload(
                    document, observations(document, refs[1])[0]["entries"][-1]["value"]
                )
                == "fallback:env"
            )
        else:
            assert observations(document, refs[1]) == []


def test_both_relation_types_continue_with_their_own_environments():
    @dataclass
    class Item:
        name: str
        children: tuple = ()

    shared = Item("shared")
    extra = Item("extra")
    projected = Item("projected", (shared,))
    root = Item("root", (shared,))
    tree_calls, interpret_calls, resolver_calls = [], [], []

    def flatten(value):
        tree_calls.append(value.name)
        return value.children, TreeAux()

    def interpret(value, env):
        interpret_calls.append((value.name, env))
        if value is root:
            return Interpretation(projected, env="other")
        if value is projected:
            return Interpretation({"extra": extra})
        return Interpretation(env)

    def select(value, env):
        if isinstance(value, Item):
            resolver_calls.append((value.name, env))
        return False

    document = inspect(
        root,
        env="initial",
        raw_value_types=SCALAR_TYPES,
        tree_rules=TreeRules.merge(
            (TreeRules({Item: TreeHandler(flatten, None)}), DATA_RULES)
        ),
        interpreters={Item: interpret},
        interpreter_resolver=select,
    )
    assert Counter(tree_calls) == Counter(root=1, shared=1, projected=1, extra=1)
    expected = Counter(
        [
            ("root", "initial"),
            ("shared", "initial"),
            ("projected", "other"),
            ("shared", "other"),
            ("extra", "other"),
        ]
    )
    assert Counter(interpret_calls) == Counter(resolver_calls) == expected
    source = record(document, document["root"])
    shared_ref = next(
        entry["value"] for entry in source["entries"] if entry["role"] == "child"
    )
    assert [
        payload(document, obs["entries"][-1]["value"])
        for obs in observations(document, shared_ref)
    ] == ["initial", "other"]


def test_interpreter_resolver_can_stop_one_environment_without_stopping_structure():
    value = Value()
    calls = []

    def interpret(value, env):
        calls.append(env)
        return Interpretation(value, env="stopped")

    document = inspect(
        value,
        env="initial",
        raw_value_types=SCALAR_TYPES,
        tree_rules=TreeRules(
            {Value: TreeHandler(lambda _: (("child",), TreeAux()), None)}
        ),
        interpreters={Value: interpret},
        interpreter_resolver=lambda value, env: env == "stopped",
    )
    assert calls == ["initial"]
    assert len(observations(document, document["root"])) == 1
    assert (
        observations(document, document["root"])[0]["entries"][-1]["value"]
        == document["root"]
    )
    assert child_values(document, document["root"]) == ["child"]


def test_interpreter_resolver_failure_propagates_without_stringifying():
    class Failure(Exception):
        def __str__(self):
            pytest.fail("exception formatting")

    error = Failure()

    def select(value, env):
        if isinstance(value, Value):
            raise error
        return False

    with pytest.raises(Failure) as caught:
        inspect(
            Value(),
            env="failed-env",
            tree_rules=TreeRules(),
            interpreter_resolver=select,
        )
    assert caught.value is error
    notes = getattr(error, "__notes__", error.args)
    assert any("Inspection interpretation:" in note for note in notes)


def test_interpreter_resolver_does_not_swallow_control_exceptions():
    class Stop(BaseException):
        pass

    def select(value, env):
        raise Stop()

    with pytest.raises(Stop):
        inspect(
            Value(),
            raw_value_types=SCALAR_TYPES,
            tree_rules=TreeRules(),
            interpreter_resolver=select,
        )


@pytest.mark.parametrize(
    "value", [None, True, 42, 2**100, float("nan"), float("inf"), "x" * 10000]
)
def test_scalars_use_both_resolvers_and_keep_raw_identity(value):
    tree_calls, interpret_calls = [], []

    def tree_select(item):
        tree_calls.append(item)
        return (
            TreeHandler(lambda _: ((value,), TreeAux()), None)
            if item is value
            else False
        )

    def interpret_select(item, env):
        interpret_calls.append((item, env))
        return (lambda item, env: Interpretation(item)) if item is value else False

    document = inspect(
        value,
        raw_value_types=SCALAR_TYPES,
        tree_rules=TreeRules(),
        tree_resolver=TreeResolver(tree_select),
        interpreter_resolver=interpret_select,
        type_names={type(value): "example.scalar"},
    )
    source = record(document, document["root"])
    assert source["value"] is value
    assert source["type"] == "example.scalar"
    assert source["entries"][0]["value"] == document["root"]
    assert (
        observations(document, document["root"])[0]["entries"][-1]["value"]
        == document["root"]
    )
    assert sum(item is value for item in tree_calls) == 1
    assert sum(item is value for item, env in interpret_calls) == 1


def test_scalar_exact_type_rules_are_not_bypassed():
    document = inspect(
        "source",
        raw_value_types=SCALAR_TYPES,
        tree_rules=TreeRules({str: TreeHandler(lambda _: ((None,), TreeAux()), None)}),
        interpreters={str: lambda value, env: Interpretation(True)},
    )
    assert child_values(document, document["root"]) == [None]
    observation = observations(document, document["root"])[0]
    assert payload(document, observation["entries"][-1]["value"]) is True
