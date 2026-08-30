from __future__ import annotations

import sys
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from slyme.utils.exception import enrich_exception
from slyme.utils.pytree import (
    AttributeKey,
    MappingKey,
    PyTreeAux,
    PyTreeEngine,
    PyTreeKey,
    SequenceKey,
)
from slyme.utils.pytree.common import (
    flatten_mapping_proxy,
    unflatten_dict,
    unflatten_mapping_proxy,
)
from slyme.utils.pytree.core import _PyTreeHandler
from slyme.utils.registry import GeneralRegistry, Registry, TypeRegistry
from slyme.utils.warning import warning_once


def test_general_registry_operations_and_strictness() -> None:
    registry: GeneralRegistry[str, int] = GeneralRegistry("numbers")
    assert registry.register(1, key="one") == 1

    @registry.register(key="two")
    def two() -> int:
        return 2

    assert registry.get("one") == 1
    assert registry.get("two") is two
    assert registry.get("missing", 3) == 3
    assert list(registry.keys()) == ["one", "two"]
    assert list(registry.values()) == [1, two]
    assert list(registry.items()) == [("one", 1), ("two", two)]
    assert list(registry) == ["one", "two"]
    assert "one" in registry
    assert len(registry) == 2
    assert "numbers" not in repr(registry)

    with pytest.raises(ValueError, match="already exists"):
        registry.register(2, key="one")
    registry.register(2, key="one", strict=False)
    assert registry.get("one") == 2
    registry.unregister("one")
    assert "one" not in registry
    with pytest.raises(KeyError):
        registry.unregister("missing")
    registry.unregister("missing", strict=False)
    with pytest.raises(KeyError):
        registry.get("missing")
    with pytest.raises(ValueError, match="cannot be.*MISSING"):
        GeneralRegistry().register(1)


def test_named_registry_infers_function_name() -> None:
    registry: Registry[Any] = Registry("objects")

    @registry.register()
    def item() -> None:
        pass

    assert registry.get("item") is item
    with pytest.raises(ValueError, match="cannot correctly infer"):
        registry.register(object())


def test_type_registry_mro_lookup_collection_and_orthogonality() -> None:
    class Root:
        pass

    class Middle(Root):
        pass

    class Leaf(Middle):
        pass

    registry: TypeRegistry[Any, str] = TypeRegistry("types")
    registry.register("root", key=Root)
    registry.register("middle", key=Middle)

    assert registry.lookup(Leaf) == "middle"
    assert registry.lookup(Leaf, reverse=True) == "root"
    assert registry.lookup_cls(Leaf) is Middle
    assert registry.lookup_cls(str) is None
    assert registry.lookup(str, "fallback") == "fallback"
    assert list(registry.lookup_all_cls(Leaf)) == [Middle, Root]
    assert list(registry.lookup_all(Leaf)) == ["middle", "root"]
    assert list(registry.collect_cls(Root)) == [Root, Middle]
    assert list(registry.collect(Root)) == ["root", "middle"]
    with pytest.raises(KeyError, match="cannot be correctly resolved"):
        registry.lookup(str)
    with pytest.raises(TypeError, match="must be a class"):
        registry.register("bad", key="not-a-type")  # type: ignore[arg-type]

    orthogonal: TypeRegistry[Any, str] = TypeRegistry("orthogonal", orthogonal=True)
    orthogonal.register("middle", key=Middle)
    with pytest.raises(ValueError, match="parents"):
        orthogonal.register("leaf", key=Leaf)
    with pytest.raises(ValueError, match="children"):
        orthogonal.register("root", key=Root)


def test_pytree_keys_resolve_and_codify() -> None:
    class Record:
        value = 4

    assert SequenceKey(1).resolve([3, 4]) == 4
    assert SequenceKey(1).codify("root") == "root[1]"
    assert MappingKey("x").resolve({"x": 2}) == 2
    assert MappingKey("x").codify("root") == "root['x']"
    assert AttributeKey("value").resolve(Record()) == 4
    assert AttributeKey("value").codify("root") == "root.value"
    with pytest.raises(NotImplementedError, match="resolve"):
        PyTreeKey().resolve(None)
    with pytest.raises(NotImplementedError, match="codify"):
        PyTreeKey().codify("root")


@given(
    st.recursive(
        st.integers() | st.text(max_size=10) | st.none(),
        lambda children: (
            st.lists(children, max_size=4)
            | st.tuples(children, children)
            | st.dictionaries(
                st.integers(min_value=0, max_value=5), children, max_size=4
            )
        ),
        max_leaves=30,
    )
)
def test_default_pytree_round_trip_and_map(tree: Any) -> None:
    engine = PyTreeEngine()
    leaves, definition = engine.flatten(tree)
    assert engine.unflatten(definition, leaves) == tree
    assert list(engine.iter(tree)) == leaves
    assert engine.map(lambda value: (value, value), tree) == engine.unflatten(
        definition, [(value, value) for value in leaves]
    )


def test_pytree_paths_iteration_and_leaf_override() -> None:
    engine = PyTreeEngine()
    tree = {"a": [10, 20], "b": (30,)}
    paths_and_leaves, definition = engine.flatten_with_key_path(tree)

    assert [leaf for _, leaf in paths_and_leaves] == [10, 20, 30]
    assert list(engine.iter_with_key_path(tree)) == paths_and_leaves
    assert [engine.get_element(tree, path) for path, _ in paths_and_leaves] == [
        10,
        20,
        30,
    ]
    assert engine.codify_key_path(paths_and_leaves[1][0], "tree") == "tree['a'][1]"
    assert engine.unflatten(definition, [1, 2, 3]) == {"a": [1, 2], "b": (3,)}

    leaves, _ = engine.flatten(tree, is_leaf=lambda value, _: isinstance(value, list))
    assert leaves == [[10, 20], 30]
    with pytest.raises(TypeError, match="Invalid key_path"):
        engine.get_element(tree, ("bad",))  # type: ignore[arg-type]


def test_pytree_custom_handler_resolvers_and_inheritance() -> None:
    @dataclass
    class Box:
        value: Any

    def flatten_box(box: Box) -> tuple[list[Any], PyTreeAux]:
        return [box.value], PyTreeAux(children_keys=(AttributeKey("value"),))

    def unflatten_box(children: Any, _: PyTreeAux) -> Box:
        return Box(next(iter(children)))

    engine = PyTreeEngine(register_defaults=False)
    engine.register(Box, flatten_box, unflatten_box)
    leaves, definition = engine.flatten(Box(3))
    assert leaves == [3]
    assert engine.unflatten(definition, [5]) == Box(5)

    override = _PyTreeHandler(
        flatten=lambda box: ([box.value + 1], PyTreeAux()),
        unflatten=lambda children, _: Box(next(iter(children)) - 1),
    )
    engine.register_resolver(
        lambda value, _: override if isinstance(value, Box) else None,
        priority="pre",
    )
    assert engine.flatten(Box(3))[0] == [4]
    with pytest.raises(ValueError, match="Invalid priority"):
        engine.register_resolver(lambda _value, _aux: None, priority="bad")  # type: ignore[arg-type]

    class ChildBox(Box):
        pass

    exact_engine = PyTreeEngine(allow_inheritance=False, register_defaults=False)
    exact_engine.register(Box, flatten_box, unflatten_box)
    assert exact_engine.flatten(ChildBox(1))[0] == [ChildBox(1)]


def test_pytree_definition_rejects_wrong_leaf_counts_and_keys() -> None:
    engine = PyTreeEngine()
    _, definition = engine.flatten([1, 2])
    with pytest.raises(ValueError, match="Too few"):
        engine.unflatten(definition, [1])
    with pytest.raises(ValueError, match="Too many"):
        engine.unflatten(definition, [1, 2, 3])

    class Broken:
        pass

    broken = PyTreeEngine(register_defaults=False)
    broken.register(
        Broken,
        lambda _value: ([1], PyTreeAux(children_keys=())),
        lambda _children, _aux: Broken(),
    )
    with pytest.raises(ValueError, match="Not enough keys"):
        broken.flatten(Broken())
    with pytest.raises(ValueError, match="Not enough keys"):
        list(broken.iter(Broken()))


def test_mapping_proxy_helpers_and_aux_immutability() -> None:
    proxy = MappingProxyType({"a": 1, "b": 2})
    children, aux = flatten_mapping_proxy(proxy)
    assert list(children) == [1, 2]
    assert unflatten_mapping_proxy([3, 4], aux) == {"a": 3, "b": 4}
    assert isinstance(PyTreeAux(metadata={"x": 1}).metadata, MappingProxyType)
    with pytest.raises(ValueError, match="Missing keys"):
        unflatten_mapping_proxy([], PyTreeAux())
    with pytest.raises(ValueError, match="Missing keys"):
        unflatten_dict([], PyTreeAux())


def test_enrich_exception_and_warning_deduplication() -> None:
    with pytest.raises(ValueError) as exc_info:
        with enrich_exception("extra context"):
            raise ValueError("failure")
    if sys.version_info >= (3, 11):
        assert exc_info.value.__notes__ == ["extra context"]
    else:
        assert exc_info.value.args[0] == "failure (extra context)"

    warning_once.cache_clear()
    with pytest.warns(UserWarning, match="once") as recorded:
        warning_once("once")
        warning_once("once")
    assert len(recorded) == 1
