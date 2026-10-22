from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
from random import Random

import pytest

from slyme.inspection import Interpretation, inspect
from slyme.tree import TreeAux, TreeHandler, TreeKey, TreeResolver, TreeRules


@dataclass
class Item:
    children: tuple = ()


RULES = TreeRules({Item: TreeHandler(lambda value: (value.children, TreeAux()), None)})


def record(graph, ref):
    return graph["objects"][ref["$ref"]]


def assert_reachable(graph):
    pending = [graph["root"]["$ref"]]
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        seen.add(key)
        for entry in graph["objects"][key]["entries"]:
            pending.append(entry["value"]["$ref"])
            if "key" in entry:
                pending.append(entry["key"]["$ref"])
    assert seen == set(graph["objects"])


@pytest.mark.parametrize("budget", range(1, 40))
def test_every_budget_keeps_all_records_reachable(budget):
    shared = Item((None,))
    source = Item((shared, shared))
    graph = inspect(
        source,
        tree_rules=RULES,
        interpreters={Item: lambda value, env: Interpretation(value, env=shared)},
        budget=budget,
    )
    assert (
        len(graph["objects"])
        + sum(len(data["entries"]) for data in graph["objects"].values())
        <= budget
    )
    assert_reachable(graph)


def test_exact_budget_retains_last_complete_relation():
    # Root, child, index, and their one relation.
    graph = inspect(Item((None,)), tree_rules=RULES, budget=4)
    assert len(graph["objects"]) == 3
    assert len(record(graph, graph["root"])["entries"]) == 1
    assert all("attributes" not in data for data in graph["objects"].values())
    assert_reachable(graph)


@pytest.mark.parametrize("value", [None, Item()])
def test_budget_one_does_not_truncate_a_value_without_relations(value):
    graph = inspect(value, tree_rules=RULES, budget=1)
    assert len(graph["objects"]) == 1
    assert record(graph, graph["root"]).get("attributes", {}) == {}
    assert record(graph, graph["root"])["kind"] == (
        "object" if isinstance(value, Item) else "opaque"
    )


@pytest.mark.parametrize("self_reference", [False, True])
def test_key_and_value_identity_is_counted_once(self_reference):
    source = TreeKey()
    shared = source if self_reference else TreeKey()
    budget = 2 if self_reference else 3
    graph = inspect(
        source,
        tree_rules=TreeRules(
            {
                TreeKey: TreeHandler(
                    lambda value: (
                        ((shared,), TreeAux(children_keys=(shared,)))
                        if value is source
                        else ((), TreeAux())
                    ),
                    None,
                )
            }
        ),
        budget=budget,
    )
    entry = record(graph, graph["root"])["entries"][0]
    assert entry["key"] == entry["value"]
    assert len(graph["objects"]) + 1 == budget
    assert all("attributes" not in data for data in graph["objects"].values())
    assert_reachable(graph)


@pytest.mark.parametrize("shared_env", [False, True])
def test_interpretation_budget_counts_only_forward_relations(shared_env):
    source = Item()
    env = source if shared_env else object()
    budget = 5 if shared_env else 6
    graph = inspect(
        source,
        env=env,
        tree_rules=TreeRules(),
        interpreters={Item: lambda value, env: Interpretation(value)},
        budget=budget,
    )
    observation = record(graph, record(graph, graph["root"])["entries"][0]["value"])
    assert observation["attributes"] == {"state": "resolved"}
    assert [entry["role"] for entry in observation["entries"]] == ["env", "value"]
    assert observation["entries"][-1]["value"] == graph["root"]
    assert (
        len(graph["objects"])
        + sum(len(data["entries"]) for data in graph["objects"].values())
        == budget
    )
    assert_reachable(graph)


@pytest.mark.parametrize("resolved", [False, True])
def test_full_observation_budget_checks_actual_interpretation_result(resolved):
    calls = []
    source = Item()

    def interpret(value, env):
        calls.append(value)
        return Interpretation(value) if resolved else None

    graph = inspect(
        source,
        tree_rules=TreeRules(),
        interpreters={Item: interpret},
        budget=5,
    )
    assert len(calls) == 1 and calls[0] is source
    observation = record(graph, record(graph, graph["root"])["entries"][0]["value"])
    assert observation["attributes"] == (
        {"state": "resolved", "truncated": True, "reason": "budget"}
        if resolved
        else {"state": "unavailable"}
    )
    assert [entry["role"] for entry in observation["entries"]] == ["env"]
    assert (
        len(graph["objects"])
        + sum(len(data["entries"]) for data in graph["objects"].values())
        == 5
    )
    assert_reachable(graph)


@pytest.mark.parametrize("selection", ["skip", "fallback", "interpret"])
def test_full_budget_checks_whether_interpretation_is_needed(selection):
    calls = []

    def forbidden(value, env):
        pytest.fail("interpreter called without room for its observation")

    def resolve(value, env):
        calls.append(value)
        if selection == "interpret":
            return forbidden
        return selection == "skip"

    source = Item((None,))
    graph = inspect(source, tree_rules=RULES, interpreter_resolver=resolve, budget=4)
    assert len(calls) == 3 and calls[0] is source
    assert calls[1:] == [None, 0]
    attributes = record(graph, graph["root"]).get("attributes", {})
    assert attributes == (
        {"truncated": True, "reason": "budget"} if selection == "interpret" else {}
    )
    assert_reachable(graph)


def test_repeated_edges_consume_budget_even_when_objects_are_shared():
    key = TreeKey()
    source, child = Item(), object()
    rules = TreeRules(
        {
            Item: TreeHandler(
                lambda _: ((child,) * 1000, TreeAux(children_keys=(key,) * 1000)), None
            )
        }
    )
    graph = inspect(source, tree_rules=rules, budget=13)
    assert len(graph["objects"]) == 3
    assert len(record(graph, graph["root"])["entries"]) == 10
    assert record(graph, graph["root"])["attributes"]["reason"] == "budget"
    assert_reachable(graph)


@pytest.mark.parametrize("value", [None, Item(), Item((None,))])
def test_depth_zero_marks_only_omitted_children(value):
    calls = []

    def resolve(item):
        calls.append(item)
        return False

    graph = inspect(
        value,
        tree_rules=RULES,
        raw_value_types={Item, type(None)},
        max_depth=0,
        tree_resolver=TreeResolver(resolve),
    )
    assert len(calls) == 1 and calls[0] is value
    assert len(graph["objects"]) == 1
    root = record(graph, graph["root"])
    assert root["value"] is value
    assert root["entries"] == []
    assert root.get("attributes", {}) == (
        {"truncated": True, "reason": "max_depth"}
        if isinstance(value, Item) and value.children
        else {}
    )


@pytest.mark.parametrize("resolved", [False, True])
@pytest.mark.parametrize("budget", [None, 5])
def test_depth_zero_checks_actual_interpretation_result(resolved, budget):
    calls = []
    source, result = Item(), Item()

    def interpret(value, env):
        calls.append(value)
        return Interpretation(result) if resolved else None

    graph = inspect(
        source,
        tree_rules=RULES,
        interpreters={Item: interpret},
        max_depth=0,
        budget=budget,
    )
    assert len(calls) == 1 and calls[0] is source
    root = record(graph, graph["root"])
    assert "attributes" not in root
    observation = record(graph, root["entries"][0]["value"])
    assert observation["attributes"] == (
        {"state": "resolved", "truncated": True, "reason": "max_depth"}
        if resolved
        else {"state": "unavailable"}
    )
    assert [entry["role"] for entry in observation["entries"]] == ["env"]
    assert_reachable(graph)


def test_structure_and_interpretation_each_add_one_depth():
    child, projected, hidden = Item(), Item(), Item()
    root = Item((child,))
    tree_calls, interpret_calls = [], []

    def flatten(value):
        tree_calls.append(value)
        return value.children, TreeAux()

    def interpret(value, env):
        interpret_calls.append(value)
        return Interpretation(projected if value is root else hidden)

    graph = inspect(
        root,
        tree_rules=TreeRules({Item: TreeHandler(flatten, None)}),
        interpreters={Item: interpret},
        raw_value_types={Item},
        max_depth=1,
    )
    assert len(tree_calls) == len(interpret_calls) == 3
    assert tree_calls[0] is interpret_calls[0] is root
    assert tree_calls[1] is interpret_calls[1] is child
    assert tree_calls[2] is interpret_calls[2] is projected
    retained = [data["value"] for data in graph["objects"].values() if "value" in data]
    assert any(value is child for value in retained)
    assert any(value is projected for value in retained)
    assert not any(value is hidden for value in retained)
    assert_reachable(graph)


def test_breadth_first_visits_shared_value_through_shallowest_path():
    shared = Item(("visible",))
    root = Item((Item((shared,)), shared))
    calls = []

    def flatten(value):
        calls.append(value)
        return value.children, TreeAux()

    graph = inspect(
        root,
        tree_rules=TreeRules({Item: TreeHandler(flatten, None)}),
        raw_value_types={Item, str},
        max_depth=2,
    )
    assert sum(value is shared for value in calls) == 1
    shared_record = next(
        data for data in graph["objects"].values() if data.get("value") is shared
    )
    assert "attributes" not in shared_record
    assert record(graph, shared_record["entries"][0]["value"])["value"] == "visible"
    assert_reachable(graph)


def test_shared_structure_propagates_depth_under_each_environment():
    shared = Item((Item(),))
    branch = Item()
    root = Item((shared, branch))
    calls = []

    def interpret(value, env):
        calls.append((value, env))
        return Interpretation(shared, env="other") if value is branch else None

    inspect(
        root,
        tree_rules=RULES,
        interpreters={Item: interpret},
        max_depth=3,
        env="initial",
    )
    assert any(value is shared and env == "other" for value, env in calls)
    assert any(value is shared.children[0] and env == "initial" for value, env in calls)
    assert any(value is shared.children[0] and env == "other" for value, env in calls)


def test_key_interpretation_can_complete_at_depth_limit():
    key = TreeKey()
    calls = []
    graph = inspect(
        Item(),
        tree_rules=TreeRules(
            {
                Item: TreeHandler(
                    lambda _: ((None,), TreeAux(children_keys=(key,))), None
                )
            }
        ),
        interpreters={TreeKey: lambda value, env: calls.append(value)},
        max_depth=1,
    )
    assert len(calls) == 1 and calls[0] is key
    key_ref = record(graph, graph["root"])["entries"][0]["key"]
    key_record = record(graph, key_ref)
    assert "attributes" not in key_record
    observation = record(graph, key_record["entries"][0]["value"])
    assert observation["attributes"] == {"state": "unavailable"}


def test_cached_structure_does_not_follow_children_past_depth_in_another_env():
    child = Item()
    shared = Item((child,))
    branch = Item()
    source = Item((shared, branch))
    initial, other = object(), object()
    calls = []

    def interpret(value, env):
        calls.append((value, env))
        return Interpretation(shared, env=other) if value is branch else None

    graph = inspect(
        source,
        env=initial,
        tree_rules=RULES,
        interpreters={Item: interpret},
        raw_value_types={Item},
        max_depth=2,
    )
    assert any(value is shared and env is other for value, env in calls)
    assert any(value is child and env is initial for value, env in calls)
    assert not any(value is child and env is other for value, env in calls)
    shared_record = next(
        data for data in graph["objects"].values() if data.get("value") is shared
    )
    assert shared_record["attributes"]["reason"] == "max_depth"
    assert any(entry["role"] == "child" for entry in shared_record["entries"])
    assert_reachable(graph)


@pytest.mark.parametrize("budget", [None, 5, 7])
def test_environment_change_at_depth_boundary_keeps_annotations_unexpanded(budget):
    source, other_env, hidden = Item(), Item(), Item()
    calls = []

    def interpret(value, env):
        calls.append(value)
        return Interpretation(hidden, env=other_env)

    graph = inspect(
        source,
        tree_rules=RULES,
        interpreters={Item: interpret},
        raw_value_types={Item},
        max_depth=0,
        budget=budget,
    )
    assert len(calls) == 1 and calls[0] is source
    observation = record(graph, record(graph, graph["root"])["entries"][0]["value"])
    assert observation["attributes"]["reason"] == (
        "budget" if budget == 5 else "max_depth"
    )
    assert [entry["role"] for entry in observation["entries"]] == (
        ["env"] if budget == 5 else ["env", "value_env"]
    )
    assert not any(data.get("value") is hidden for data in graph["objects"].values())
    assert_reachable(graph)


def test_infinite_children_at_depth_boundary_consume_only_first_omitted_child():
    calls = []

    def children():
        calls.append("first")
        yield Item()
        pytest.fail("continued after encountering an omitted child")

    graph = inspect(
        Item(),
        tree_rules=TreeRules(
            {Item: TreeHandler(lambda _: (children(), TreeAux()), None)}
        ),
        max_depth=0,
    )
    assert calls == ["first"]
    assert len(graph["objects"]) == 1
    assert record(graph, graph["root"])["attributes"]["reason"] == "max_depth"


@pytest.mark.parametrize(
    "stage",
    [
        "tree_resolver",
        "tree_handler",
        "child_iterator",
        "interpreter_resolver",
        "interpreter",
    ],
)
def test_depth_boundary_does_not_swallow_callback_errors(stage):
    failure = ValueError(stage)

    def fail(*args):
        raise failure

    def children():
        raise failure
        yield  # Make the failure occur during iteration.

    handler = TreeHandler(
        fail if stage == "tree_handler" else lambda _: (children(), TreeAux()), None
    )
    with pytest.raises(ValueError) as caught:
        inspect(
            Item(),
            tree_rules=TreeRules({Item: handler})
            if stage in {"tree_handler", "child_iterator"}
            else TreeRules(),
            tree_resolver=TreeResolver(fail) if stage == "tree_resolver" else None,
            interpreter_resolver=fail if stage == "interpreter_resolver" else None,
            interpreters={Item: fail} if stage == "interpreter" else None,
            max_depth=0,
        )
    assert caught.value is failure


def test_unlimited_defaults_do_not_truncate_large_graphs():
    graph = inspect(Item(tuple(object() for _ in range(10001))), tree_rules=RULES)
    assert len(graph["objects"]) > 10000
    assert len(record(graph, graph["root"])["entries"]) == 10001
    assert all(
        not data.get("attributes", {}).get("truncated")
        for data in graph["objects"].values()
    )
    assert_reachable(graph)


@pytest.mark.parametrize("seed", range(8))
@pytest.mark.parametrize("max_depth", [None, 0, 1, 3])
@pytest.mark.parametrize("budget", [None, 1, 11, 40])
def test_cyclic_graphs_preserve_identity_environment_and_limits(
    seed, max_depth, budget
):
    rng = Random(seed)
    values = [Item() for _ in range(5)]
    envs = [object(), object()]
    indices = {id(value): index for index, value in enumerate(values)}
    env_indices = {id(env): index for index, env in enumerate(envs)}
    children = [
        tuple(rng.randrange(5) for _ in range(rng.randrange(4))) for _ in values
    ]
    targets = {
        (i, e): (rng.randrange(5), rng.randrange(2)) for i in range(5) for e in range(2)
    }
    for value, indices_ in zip(values, children, strict=True):
        value.children = tuple(values[index] for index in indices_)
    expanded, interpreted = [], []

    def flatten(value):
        expanded.append(indices[id(value)])
        return value.children, TreeAux()

    def interpret(value, env):
        pair = (indices[id(value)], env_indices[id(env)])
        interpreted.append(pair)
        target, target_env = targets[pair]
        return Interpretation(values[target], env=envs[target_env])

    graph = inspect(
        values[0],
        env=envs[0],
        tree_rules=TreeRules({Item: TreeHandler(flatten, None)}),
        interpreters={Item: interpret},
        raw_value_types={Item},
        max_depth=max_depth,
        budget=budget,
    )
    assert_reachable(graph)
    assert all(count == 1 for count in Counter(expanded).values())
    assert all(count == 1 for count in Counter(interpreted).values())
    retained = [
        id(data["value"]) for data in graph["objects"].values() if "value" in data
    ]
    assert len(retained) == len(set(retained))
    if budget is not None:
        assert (
            len(graph["objects"])
            + sum(len(data["entries"]) for data in graph["objects"].values())
            <= budget
        )
        return

    pending = deque([(0, 0, 0)])
    seen, expected = set(), []
    while pending:
        index, env, depth = pending.popleft()
        pair = (index, env)
        if pair in seen:
            continue
        seen.add(pair)
        expected.append(pair)
        if max_depth is not None and depth >= max_depth:
            continue
        pending.extend((child, env, depth + 1) for child in children[index])
        target, target_env = targets[pair]
        pending.append((target, target_env, depth + 1))
    assert interpreted == expected
    assert set(expanded) == {index for index, _ in expected}


def test_environment_annotation_does_not_schedule_self_interpretation():
    source, initial_env, next_env = Item(), Item(), Item()
    calls = []

    def interpret(value, env):
        calls.append((value, env))
        return Interpretation(initial_env, env=next_env) if value is source else None

    inspect(
        source,
        env=initial_env,
        tree_rules=TreeRules(),
        interpreters={Item: interpret},
    )
    assert len(calls) == 2
    assert calls[0][0] is source and calls[0][1] is initial_env
    assert calls[1][0] is initial_env and calls[1][1] is next_env


def test_shared_structure_preserves_value_then_key_visit_order():
    source, value, key = Item(), Item(), TreeKey()
    initial_env, next_env = object(), object()
    calls = []

    def flatten(item):
        return (
            ((value,), TreeAux(children_keys=(key,)))
            if item is source
            else ((), TreeAux())
        )

    def interpret(item, env):
        calls.append((item, env))
        if item is source and env is initial_env:
            return Interpretation(source, env=next_env)
        return None

    inspect(
        source,
        env=initial_env,
        tree_rules=TreeRules({Item: TreeHandler(flatten, None)}),
        interpreters={Item: interpret, TreeKey: interpret},
    )
    for env in (initial_env, next_env):
        observed = [item for item, current_env in calls if current_env is env]
        assert len(observed) == 3
        assert all(a is b for a, b in zip(observed, (source, value, key), strict=True))
