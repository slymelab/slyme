from __future__ import annotations

import gc
import weakref
from collections import Counter
from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from slyme.inspection import Interpretation, inspect
from slyme.tree import TreeAux, TreeHandler, TreeKey, TreeResolver, TreeRules


@dataclass(frozen=True, eq=False)
class Vertex(TreeKey):
    index: int


ROW = st.tuples(
    st.lists(st.tuples(st.integers(0, 3), st.integers(0, 3)), max_size=3),
    st.integers(-2, 3),  # -2: skip interpretation; -1: unavailable.
    st.integers(0, 3),
    st.booleans(),  # Skip structural expansion.
)


@settings(max_examples=300, deadline=None, derandomize=True)
@given(
    rows=st.lists(ROW, min_size=4, max_size=4),
    max_depth=st.one_of(st.none(), st.integers(0, 3)),
    budget=st.one_of(st.none(), st.integers(1, 80)),
)
def test_observation_relations_match_finite_graph_model(rows, max_depth, budget):
    vertices = [Vertex(index) for index in range(4)]
    expanded, interpreted = [], []

    def flatten(value):
        expanded.append(value.index)
        edges = rows[value.index][0]
        return (
            tuple(vertices[child] for child, _ in edges),
            TreeAux(children_keys=tuple(vertices[key] for _, key in edges)),
        )

    def interpret(value, env):
        interpreted.append((value.index, env.index))
        _, target, target_env, _ = rows[value.index]
        return (
            None
            if target == -1
            else Interpretation(vertices[target], env=vertices[target_env])
        )

    graph = inspect(
        vertices[0],
        env=vertices[0],
        tree_rules=TreeRules({Vertex: TreeHandler(flatten, None)}),
        tree_resolver=TreeResolver(lambda value: rows[value.index][3]),
        interpreters={Vertex: interpret},
        interpreter_resolver=lambda value, env: rows[value.index][1] == -2,
        raw_value_types={Vertex},
        max_depth=max_depth,
        budget=budget,
    )
    objects = graph["objects"]

    def index(ref):
        return objects[ref["$ref"]]["value"].index

    # Compute reachability by fixed-point relaxation, independently of traversal order.
    distances = {(0, 0): 0}
    changed = True
    while changed:
        changed = False
        for (value, env), depth in list(distances.items()):
            if max_depth is not None and depth >= max_depth:
                continue
            edges, target, target_env, skip_tree = rows[value]
            successors = [] if skip_tree else [(v, env) for edge in edges for v in edge]
            if target >= 0:
                successors.append((target, target_env))
            for pair in successors:
                if depth + 1 < distances.get(pair, float("inf")):
                    distances[pair] = depth + 1
                    changed = True

    assert all(count == 1 for count in Counter(expanded).values())
    assert all(count == 1 for count in Counter(interpreted).values())
    assert set(interpreted) <= set(distances)
    actual_pairs, owned_observations, raw_indices = set(), [], []
    for data in objects.values():
        if "value" not in data:
            continue
        value = data["value"].index
        raw_indices.append(value)
        edges, target, target_env, skip_tree = rows[value]
        children = [entry for entry in data["entries"] if entry["role"] == "child"]
        actual_edges = [
            (index(entry["value"]), index(entry["key"])) for entry in children
        ]
        allowed_edges = [] if skip_tree else edges
        assert actual_edges == allowed_edges[: len(actual_edges)]
        if budget is None:
            depths = [depth for (v, _), depth in distances.items() if v == value]
            can_expand = depths and (max_depth is None or min(depths) < max_depth)
            assert actual_edges == (allowed_edges if can_expand else [])
        for entry in data["entries"]:
            if entry["role"] != "interpretation":
                continue
            owned_observations.append(entry["value"]["$ref"])
            observation = objects[entry["value"]["$ref"]]
            parts = {part["role"]: part["value"] for part in observation["entries"]}
            assert len(parts) == len(observation["entries"])
            pair = (value, index(parts["env"]))
            assert pair not in actual_pairs
            actual_pairs.add(pair)
            assert pair in distances and target != -2
            assert observation["attributes"]["state"] == (
                "unavailable" if target == -1 else "resolved"
            )
            if target == -1:
                assert set(parts) == {"env"}
                assert not observation["attributes"].get("truncated")
                continue
            if "value_env" in parts:
                assert index(parts["value_env"]) == target_env
                assert target_env != pair[1]
            if "value" in parts:
                assert index(parts["value"]) == target
                assert index(parts.get("value_env", parts["env"])) == target_env
                assert max_depth is None or distances[pair] < max_depth
            else:
                assert observation["attributes"]["truncated"]
            if budget is None:
                beyond_depth = max_depth is not None and distances[pair] >= max_depth
                assert ("value" not in parts) == beyond_depth
                assert ("value_env" in parts) == (target_env != pair[1])
                assert observation["attributes"].get("truncated", False) == beyond_depth

    assert len(raw_indices) == len(set(raw_indices))
    assert len(owned_observations) == len(set(owned_observations))
    assert set(owned_observations) == {
        key for key, data in objects.items() if data["kind"] == "interpretation"
    }
    assert actual_pairs == set(interpreted)
    if budget is None:
        assert actual_pairs == {pair for pair in distances if rows[pair[0]][1] != -2}
        assert set(expanded) == {v for v, _ in distances if not rows[v][3]}
    else:
        assert (
            len(objects) + sum(len(data["entries"]) for data in objects.values())
            <= budget
        )

    reachable = {graph["root"]["$ref"]}
    pending = list(reachable)
    while pending:
        for entry in objects[pending.pop()]["entries"]:
            for field in ("value", "key"):
                if field in entry:
                    key = entry[field]["$ref"]
                    assert key in objects
                    if key not in reachable:
                        reachable.add(key)
                        pending.append(key)
    assert reachable == set(objects)


def test_temporary_environments_survive_traversal_but_not_the_returned_graph():
    class Environment:
        pass

    source = object()
    references = []

    def new_environment():
        env = Environment()
        references.append(weakref.ref(env))
        return env

    def interpret(value, env):
        assert all(reference() is not None for reference in references)
        if len(references) < 5:
            return Interpretation(source, env=new_environment())
        return None

    graph = inspect(
        source,
        env=new_environment(),
        tree_rules=TreeRules(),
        interpreters={object: interpret},
    )
    gc.collect()
    assert len(references) == 5
    assert all(reference() is None for reference in references)
    observations = [
        data for data in graph["objects"].values() if data["kind"] == "interpretation"
    ]
    assert len(observations) == 5
    assert all("value" not in data for data in graph["objects"].values())
