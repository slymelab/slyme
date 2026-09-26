from __future__ import annotations

import builtins
import sys
from asyncio import CancelledError
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from slyme.utils.exception import (
    BaseExceptionGroup,
    ExceptionGroup,
    enrich_exception,
    exception_group,
)
from slyme.utils.tree import (
    AttributeKey,
    MappingKey,
    SequenceKey,
    TraverseAux,
    TreeAux,
    TreeDef,
    TreeHandler,
    TreeKey,
    TreeResolver,
    TreeRules,
    codify_key_path,
    flatten_with_key_path,
    get_element,
    iter_with_key_path,
)
from slyme.utils.tree import (
    flatten as tree_flatten,
)
from slyme.utils.tree import (
    iter as iter_leaves,
)
from slyme.utils.tree import (
    map as map_leaves,
)
from slyme.utils.tree.common import (
    DATA_RULES,
    flatten_mapping_proxy,
    unflatten_dict,
    unflatten_mapping_proxy,
)
from slyme.utils.warning import warning_once


@pytest.mark.parametrize(
    "value",
    [
        TreeKey(),
        SequenceKey(0),
        MappingKey("a"),
        AttributeKey("value"),
        TreeAux(),
        DATA_RULES.handlers[list],
        TraverseAux(parent=None, key_path=()),
        TreeResolver(lambda value: False),
        DATA_RULES,
        tree_flatten(1, rules=DATA_RULES)[1],
        tree_flatten([], rules=DATA_RULES)[1],
    ],
)
def test_tree_records_use_slots(value: object) -> None:
    assert not hasattr(value, "__dict__")


@pytest.mark.parametrize("slots", [False, True])
def test_tree_key_subclasses_choose_their_instance_storage(slots: bool) -> None:
    @dataclass(frozen=True, slots=slots)
    class LabeledKey(SequenceKey):
        label: str

    key = LabeledKey(0, "first")
    assert hasattr(key, "__dict__") is not slots
    assert key.label == "first"
    assert key.resolve([42]) == 42


def test_tree_keys_resolve_and_codify() -> None:
    class Record:
        value = 4

    assert SequenceKey(1).resolve([3, 4]) == 4
    assert SequenceKey(1).codify("root") == "root[1]"
    assert MappingKey("x").resolve({"x": 2}) == 2
    assert MappingKey("x").codify("root") == "root['x']"
    assert AttributeKey("value").resolve(Record()) == 4
    assert AttributeKey("value").codify("root") == "root.value"
    with pytest.raises(NotImplementedError, match="resolve"):
        TreeKey().resolve(None)
    with pytest.raises(NotImplementedError, match="codify"):
        TreeKey().codify("root")


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
def test_default_tree_round_trip_and_map(tree: Any) -> None:
    leaves, definition = tree_flatten(tree, rules=DATA_RULES)
    assert definition.unflatten(leaves) == tree
    assert list(iter_leaves(tree, rules=DATA_RULES)) == leaves
    assert map_leaves(
        lambda value: (value, value), tree, rules=DATA_RULES
    ) == definition.unflatten([(value, value) for value in leaves])


def test_tree_paths_iteration_and_leaf_override() -> None:
    tree = {"a": [10, 20], "b": (30,)}
    paths_and_leaves, definition = flatten_with_key_path(tree, rules=DATA_RULES)
    assert [leaf for _, leaf in paths_and_leaves] == [10, 20, 30]
    assert list(iter_with_key_path(tree, rules=DATA_RULES)) == paths_and_leaves
    assert [get_element(tree, path) for path, _ in paths_and_leaves] == [
        10,
        20,
        30,
    ]
    assert codify_key_path(paths_and_leaves[1][0], "tree") == "tree['a'][1]"
    assert definition.unflatten([1, 2, 3]) == {"a": [1, 2], "b": (3,)}
    leaves, _ = tree_flatten(
        tree,
        rules=DATA_RULES,
        resolver=TreeResolver(lambda value: isinstance(value, list)),
    )
    assert leaves == [[10, 20], 30]


def test_tree_definition_can_rebuild_independent_leaf_sequences() -> None:
    leaves, definition = tree_flatten([1, None, 2], rules=DATA_RULES)

    assert isinstance(definition, TreeDef)
    assert leaves == [1, None, 2]
    assert definition.unflatten(iter([10, None, 20])) == [10, None, 20]
    assert definition.unflatten(iter([30, None, 40])) == [30, None, 40]


@pytest.mark.parametrize("operation", [tree_flatten, flatten_with_key_path])
@pytest.mark.parametrize(
    "resolver",
    [
        None,
        TreeResolver(lambda value: False),
        TreeResolver(lambda value, aux: False, True),
    ],
)
def test_tree_rebuilds_empty_and_shared_containers_per_occurrence(
    operation, resolver
) -> None:
    shared = [None, ()]
    tree = [1, [], shared, {}, shared, 2]
    _, definition = operation(tree, rules=DATA_RULES, resolver=resolver)
    rebuilt = definition.unflatten(iter([10, 20, 30, 40]))
    assert rebuilt == [10, [], [20, ()], {}, [30, ()], 40]
    assert rebuilt[2] is not rebuilt[4]


@pytest.mark.parametrize("operation", [tree_flatten, flatten_with_key_path])
@pytest.mark.parametrize("resolver", [None, TreeResolver(lambda value: False)])
def test_tree_traversal_only_error_follows_child_reconstruction(
    operation, resolver
) -> None:
    @dataclass
    class Box:
        value: Any

    class Opaque(Box):
        pass

    rebuilt = []
    consumed = []

    def rebuild(children, aux):
        assert isinstance(children, tuple)
        (value,) = children
        rebuilt.append(value)
        return Box(value)

    def source():
        for value in [1, 2, 3]:
            consumed.append(value)
            yield value

    rules = TreeRules.merge(
        (
            TreeRules(
                {
                    Box: TreeHandler(
                        lambda box: (
                            [box.value],
                            TreeAux(children_keys=(AttributeKey("value"),)),
                        ),
                        rebuild,
                    ),
                    Opaque: TreeHandler(
                        lambda box: (
                            [box.value],
                            TreeAux(children_keys=(AttributeKey("value"),), cls=tuple),
                        ),
                        None,
                    ),
                }
            ),
            DATA_RULES,
        )
    )
    _, definition = operation(
        [Box(1), Opaque(Box(2)), Box(3)], rules=rules, resolver=resolver
    )
    with pytest.raises(TypeError, match="Opaque is registered for traversal only"):
        definition.unflatten(source())
    assert rebuilt == [1, 2]
    assert consumed == [1, 2]


@pytest.mark.parametrize("operation", [tree_flatten, flatten_with_key_path])
@pytest.mark.parametrize("resolver", [None, TreeResolver(lambda value: False)])
def test_tree_consumes_handler_children_depth_first(operation, resolver) -> None:
    @dataclass
    class Box:
        name: str
        children: tuple[Any, ...]

        def __getitem__(self, index: int) -> Any:
            return self.children[index]

    calls = []

    def expand(box):
        calls.append(("expand", box.name))

        def children():
            for index, value in enumerate(box.children):
                calls.append((box.name, index))
                yield value
            calls.append(("exhaust", box.name))

        keys = tuple(SequenceKey(index) for index in range(len(box.children)))
        return children(), TreeAux(children_keys=keys)

    rules = TreeRules({Box: TreeHandler(expand, lambda children, aux: tuple(children))})
    _, definition = operation(
        Box("outer", (Box("inner", (1,)), 2)), rules=rules, resolver=resolver
    )
    assert calls == [
        ("expand", "outer"),
        ("outer", 0),
        ("expand", "inner"),
        ("inner", 0),
        ("exhaust", "inner"),
        ("outer", 1),
        ("exhaust", "outer"),
    ]
    assert definition.unflatten([3, 4]) == ((3,), 4)


@pytest.mark.parametrize("cls", [None, tuple])
def test_tree_preserves_handler_aux_for_reconstruction(cls: type | None) -> None:
    @dataclass
    class Box:
        value: int

    aux = TreeAux(cls=cls)

    def rebuild(children, received_aux):
        assert received_aux is aux
        assert received_aux.cls is cls
        return Box(next(iter(children)))

    rules = TreeRules({Box: TreeHandler(lambda box: ((box.value,), aux), rebuild)})
    leaves, definition = tree_flatten(Box(1), rules=rules)

    assert isinstance(definition, TreeDef)
    assert leaves == [1]
    assert definition.unflatten([2]) == Box(2)


def test_resolver_leaf_bypasses_registered_handler_in_both_traversal_paths() -> None:
    value = [1, 2]

    def flatten(element):
        pytest.fail("An explicit leaf must not call its container handler.")

    rules = TreeRules({list: TreeHandler(flatten, None)})
    resolver = TreeResolver(lambda element: element is value)

    leaves, definition = tree_flatten(value, rules=rules, resolver=resolver)
    assert len(leaves) == 1 and leaves[0] is value
    assert definition.unflatten(leaves) is value
    assert list(iter_leaves(value, rules=rules, resolver=resolver)) == leaves
    paths, _ = flatten_with_key_path(value, rules=rules, resolver=resolver)
    assert paths == [((), value)]
    assert list(iter_with_key_path(value, rules=rules, resolver=resolver)) == paths


@pytest.mark.parametrize("value", [[], (), {}])
def test_empty_containers_keep_their_structure_without_yielding_leaves(value) -> None:
    leaves, definition = tree_flatten(value, rules=DATA_RULES)
    assert leaves == []
    rebuilt = definition.unflatten(leaves)
    assert type(rebuilt) is type(value)
    assert rebuilt == value
    assert list(iter_leaves(value, rules=DATA_RULES)) == []
    paths, _ = flatten_with_key_path(value, rules=DATA_RULES)
    assert paths == []
    assert list(iter_with_key_path(value, rules=DATA_RULES)) == []


def test_tree_custom_handlers_and_explicit_resolver_priority() -> None:
    @dataclass
    class Box:
        value: Any

    class ChildBox(Box):
        pass

    handler = TreeHandler(
        lambda box: ([box.value], TreeAux(children_keys=(AttributeKey("value"),))),
        lambda children, _: Box(next(iter(children))),
    )
    exact = TreeRules({Box: handler})
    leaves, definition = tree_flatten(Box(3), rules=exact)
    assert leaves == [3]
    assert definition.unflatten([5]) == Box(5)
    assert tree_flatten(ChildBox(1), rules=exact)[0] == [ChildBox(1)]

    override = TreeHandler(
        lambda box: ([box.value + 1], TreeAux()),
        lambda children, _: Box(next(iter(children)) - 1),
    )
    resolver = TreeResolver(lambda value: override if isinstance(value, Box) else False)
    fallback = TreeResolver(
        lambda value: (
            override
            if type(value) not in exact.handlers and isinstance(value, Box)
            else False
        )
    )
    assert tree_flatten(Box(3), rules=exact, resolver=resolver)[0] == [4]
    assert list(iter_leaves(Box(3), rules=exact, resolver=resolver)) == [4]
    assert tree_flatten(Box(3), rules=exact, resolver=fallback)[0] == [3]
    assert tree_flatten(ChildBox(1), rules=exact, resolver=fallback)[0] == [2]
    assert tree_flatten(1, rules=exact, resolver=fallback)[0] == [1]
    assert map_leaves(
        lambda value: value * 2, Box(3), rules=exact, resolver=resolver
    ) == Box(7)


@pytest.mark.parametrize("resolver", [None, TreeResolver(lambda value: False)])
def test_plain_traversal_does_not_construct_traverse_aux(monkeypatch, resolver) -> None:
    def unexpected(*args, **kwargs):
        pytest.fail("Value-only traversal must not construct TraverseAux.")

    monkeypatch.setattr(TraverseAux, "__init__", unexpected)
    value = [1, {"a": [2, 3]}]
    leaves, definition = tree_flatten(value, rules=DATA_RULES, resolver=resolver)
    assert leaves == [1, 2, 3]
    assert definition.unflatten(leaves) == value
    assert list(iter_leaves(value, rules=DATA_RULES, resolver=resolver)) == leaves
    assert map_leaves(
        lambda leaf: leaf + 1, value, rules=DATA_RULES, resolver=resolver
    ) == [2, {"a": [3, 4]}]


@pytest.mark.parametrize("keys", [None, (), (SequenceKey(0),)])
def test_tree_checks_child_keys_only_when_tracking_paths(keys) -> None:
    rules = TreeRules(
        {
            list: TreeHandler(
                lambda value: (iter(value), TreeAux(children_keys=keys)),
                lambda items, _: list(items),
            )
        }
    )
    tree = [1, [2]]
    leaves, definition = tree_flatten(tree, rules=rules)
    assert leaves == [1, 2]
    assert definition.unflatten(leaves) == tree
    assert list(iter_leaves(tree, rules=rules)) == leaves

    message = "children_keys is required" if keys is None else "Not enough keys"
    for operation in (flatten_with_key_path, iter_with_key_path):
        with pytest.raises(ValueError, match=message):
            list(operation(tree, rules=rules))
    resolver = TreeResolver(lambda value, aux: False, takes_aux=True)
    for operation in (tree_flatten, iter_leaves):
        with pytest.raises(ValueError, match=message):
            list(operation(tree, rules=rules, resolver=resolver))


@pytest.mark.parametrize("operation", [iter_leaves, iter_with_key_path])
def test_tree_iterator_resolves_only_consumed_elements(operation) -> None:
    tree = [1, [2], 3]
    seen = []

    def resolve(value):
        seen.append(value)
        return False

    iterator = operation(tree, rules=DATA_RULES, resolver=TreeResolver(resolve))
    assert seen == []
    assert next(iterator) == (1 if operation is iter_leaves else ((SequenceKey(0),), 1))
    assert seen == [tree, 1]
    assert next(iterator) == (
        2 if operation is iter_leaves else ((SequenceKey(1), SequenceKey(0)), 2)
    )
    assert seen == [tree, 1, tree[1], 2]
    iterator.close()
    assert seen == [tree, 1, tree[1], 2]


@pytest.mark.parametrize(
    "operation", [tree_flatten, flatten_with_key_path, iter_leaves, iter_with_key_path]
)
def test_resolver_aux_distinguishes_occurrences_by_parent_and_path(operation) -> None:
    shared = [1, 2]
    tree = {"opaque": shared, "expanded": shared}
    seen: list[TraverseAux] = []

    def resolve(value: Any, aux: TraverseAux) -> bool:
        seen.append(aux)
        return aux.key_path == (MappingKey("opaque"),)

    resolver = TreeResolver(resolve, takes_aux=True)
    result = operation(tree, rules=DATA_RULES, resolver=resolver)
    if operation in (tree_flatten, flatten_with_key_path):
        result, definition = result
        assert definition.unflatten([shared, 1, 2]) == tree
    else:
        result = list(result)
    if operation in (flatten_with_key_path, iter_with_key_path):
        assert [path for path, value in result] == [
            (MappingKey("opaque"),),
            (MappingKey("expanded"), SequenceKey(0)),
            (MappingKey("expanded"), SequenceKey(1)),
        ]
        result = [value for path, value in result]
    assert result == [shared, 1, 2]
    assert result[0] is shared
    assert [aux.key_path for aux in seen] == [
        (),
        (MappingKey("opaque"),),
        (MappingKey("expanded"),),
        (MappingKey("expanded"), SequenceKey(0)),
        (MappingKey("expanded"), SequenceKey(1)),
    ]
    assert seen[0].parent is None
    assert seen[1].parent is seen[2].parent is tree
    assert seen[3].parent is seen[4].parent is shared


@pytest.mark.parametrize("takes_aux", [False, True])
def test_resolver_errors_propagate_without_reinterpretation(takes_aux: bool) -> None:
    error = LookupError("resolver failure")

    def fail(value, aux=None):
        raise error

    resolver = TreeResolver(fail, takes_aux=takes_aux)
    for operation in (tree_flatten, iter_leaves):
        with pytest.raises(LookupError) as caught:
            list(operation([1], rules=DATA_RULES, resolver=resolver))
        assert caught.value is error


def test_tree_handler_can_support_traversal_without_reconstruction() -> None:
    @dataclass
    class Box:
        value: int

    rules = TreeRules.merge(
        (
            TreeRules(
                {
                    Box: TreeHandler(
                        lambda box: (
                            [box.value],
                            TreeAux(children_keys=(AttributeKey("value"),)),
                        ),
                        None,
                    )
                }
            ),
            DATA_RULES,
        )
    )
    tree = {"box": Box(3)}
    leaves, definition = tree_flatten(tree, rules=rules)
    assert leaves == [3]
    assert list(iter_leaves(tree, rules=rules)) == [3]
    paths_and_leaves = list(iter_with_key_path(tree, rules=rules))
    assert paths_and_leaves == [((MappingKey("box"), AttributeKey("value")), 3)]
    assert get_element(tree, paths_and_leaves[0][0]) == 3
    with pytest.raises(TypeError, match="Box is registered for traversal only"):
        definition.unflatten(leaves)
    with pytest.raises(TypeError, match="Box is registered for traversal only"):
        map_leaves(lambda value: value + 1, tree, rules=rules)


def test_tree_subclasses_can_define_their_own_reconstruction() -> None:
    @dataclass
    class Box:
        value: int

    @dataclass
    class ChildBox(Box):
        label: str

    rules = TreeRules(
        {
            Box: TreeHandler(
                lambda box: ([box.value], TreeAux()),
                lambda children, _: Box(next(iter(children))),
            )
        }
    )
    value = ChildBox(1, "child")
    leaves, definition = tree_flatten(value, rules=rules)
    assert len(leaves) == 1 and leaves[0] is value
    assert definition.unflatten(leaves) is value
    extended = TreeRules.merge(
        (
            TreeRules(
                {
                    ChildBox: TreeHandler(
                        lambda box: (
                            [box.value],
                            TreeAux(metadata={"label": box.label}),
                        ),
                        lambda children, aux: ChildBox(
                            next(iter(children)), aux.metadata["label"]
                        ),
                    )
                }
            ),
            rules,
        )
    )
    leaves, definition = tree_flatten(value, rules=extended)
    assert leaves == [1]
    rebuilt = definition.unflatten([2])
    assert type(rebuilt) is ChildBox
    assert rebuilt == ChildBox(2, "child")


@pytest.mark.parametrize(
    "base, payload", [(list, [1]), (dict, {"x": 1}), (tuple, (1,))]
)
def test_builtin_container_subclasses_remain_opaque(base: type, payload: Any) -> None:
    class CustomContainer(base):
        pass

    value = CustomContainer(payload)
    leaves, definition = tree_flatten(value, rules=DATA_RULES)
    assert len(leaves) == 1 and leaves[0] is value
    assert definition.unflatten(leaves) is value


@pytest.mark.parametrize("operation", [tree_flatten, flatten_with_key_path])
@pytest.mark.parametrize("resolver", [None, TreeResolver(lambda value: False)])
def test_tree_definition_rejects_wrong_leaf_counts(operation, resolver) -> None:
    _, definition = operation([1, 2], rules=DATA_RULES, resolver=resolver)
    with pytest.raises(ValueError, match="Too few"):
        definition.unflatten([1])
    with pytest.raises(ValueError, match="Too many"):
        definition.unflatten([1, 2, 3])


def test_mapping_proxy_helpers_and_aux_immutability() -> None:
    proxy = MappingProxyType({"a": 1, "b": 2})
    children, aux = flatten_mapping_proxy(proxy)
    assert list(children) == [1, 2]
    assert unflatten_mapping_proxy([3, 4], aux) == {"a": 3, "b": 4}
    assert isinstance(TreeAux(metadata={"x": 1}).metadata, MappingProxyType)
    with pytest.raises(ValueError, match="Missing keys"):
        unflatten_mapping_proxy([], TreeAux())
    with pytest.raises(ValueError, match="Missing keys"):
        unflatten_dict([], TreeAux())


def test_exception_group_preserves_members_and_message() -> None:
    errors = [ValueError("first"), RuntimeError("second")]
    group = exception_group("operations failed", errors)

    assert type(group) is ExceptionGroup
    assert group.message == "operations failed"
    assert group.exceptions == tuple(errors)
    assert all(
        actual is expected
        for actual, expected in zip(group.exceptions, errors, strict=True)
    )
    assert str(group) == "operations failed (2 sub-exceptions)"
    with pytest.raises(Exception) as caught:
        raise group
    assert caught.value is group
    if sys.version_info >= (3, 11):
        assert type(group) is builtins.ExceptionGroup

    errors.clear()
    assert len(group.exceptions) == 2


@pytest.mark.parametrize("error", [CancelledError(), KeyboardInterrupt(), SystemExit()])
def test_exception_group_preserves_base_exception_catch_semantics(
    error: BaseException,
) -> None:
    ordinary = ValueError("failure")
    group = exception_group("interrupted", [ordinary, error])

    assert type(group) is BaseExceptionGroup
    assert group.exceptions == (ordinary, error)
    assert not isinstance(group, Exception)
    with pytest.raises(BaseException) as caught:
        try:
            raise group
        except Exception:
            pytest.fail("Base exception groups must bypass except Exception.")
    assert caught.value is group
    if sys.version_info >= (3, 11):
        assert type(group) is builtins.BaseExceptionGroup


@pytest.mark.parametrize("error", [ValueError("failure"), CancelledError()])
def test_exception_group_preserves_nested_groups(error: BaseException) -> None:
    inner = exception_group("inner", (error,))
    outer = exception_group("outer", (inner,))

    assert inner.exceptions == (error,)
    assert outer.exceptions == (inner,)
    assert isinstance(outer, Exception) == isinstance(error, Exception)
    assert str(outer) == "outer (1 sub-exception)"


def test_exception_group_rejects_empty_members() -> None:
    with pytest.raises(ValueError):
        exception_group("empty", [])


@pytest.mark.parametrize("args", [(), ("failure",), (123,), ("failure", 123)])
def test_enrich_exception_updates_context_without_raising(
    args: tuple[Any, ...],
) -> None:
    error = ValueError(*args)
    assert enrich_exception(error, "extra context") is None
    if sys.version_info >= (3, 11):
        assert error.__notes__ == ["extra context"]
        assert error.args == args
    elif args and isinstance(args[0], str):
        assert error.args == (f"{args[0]} (extra context)", *args[1:])
    else:
        assert error.args == (*args, "(extra context)")


def test_enrich_exception_preserves_exception_and_traceback() -> None:
    failure = ValueError("failure")

    with pytest.raises(ValueError) as caught:
        try:
            raise failure
        except ValueError as error:
            traceback = error.__traceback__
            enrich_exception(error, "inner")
            enrich_exception(error, "outer")
            assert error.__traceback__ is traceback
            raise

    assert caught.value is failure
    assert caught.value.__traceback__ is traceback
    if sys.version_info >= (3, 11):
        assert failure.__notes__ == ["inner", "outer"]
    else:
        assert failure.args == ("failure (inner) (outer)",)


def test_warning_deduplication() -> None:
    warning_once.cache_clear()
    with pytest.warns(UserWarning, match="once") as recorded:
        warning_once("once")
        warning_once("once")
    assert len(recorded) == 1
