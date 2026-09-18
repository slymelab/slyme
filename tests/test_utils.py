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

from slyme.context.default import DATA_RULES
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
    TreeAux,
    TreeEngine,
    TreeHandler,
    TreeKey,
    TreeRules,
)
from slyme.utils.tree.common import (
    flatten_mapping_proxy,
    unflatten_dict,
    unflatten_mapping_proxy,
)
from slyme.utils.warning import warning_once


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
    leaves, definition = TreeEngine.flatten(tree, rules=DATA_RULES)
    assert TreeEngine.unflatten(definition, leaves) == tree
    assert list(TreeEngine.iter(tree, rules=DATA_RULES)) == leaves
    assert TreeEngine.map(
        lambda value: (value, value), tree, rules=DATA_RULES
    ) == TreeEngine.unflatten(definition, [(value, value) for value in leaves])


def test_tree_paths_iteration_and_leaf_override() -> None:
    tree = {"a": [10, 20], "b": (30,)}
    paths_and_leaves, definition = TreeEngine.flatten_with_key_path(
        tree, rules=DATA_RULES
    )
    assert [leaf for _, leaf in paths_and_leaves] == [10, 20, 30]
    assert (
        list(TreeEngine.iter_with_key_path(tree, rules=DATA_RULES)) == paths_and_leaves
    )
    assert [TreeEngine.get_element(tree, path) for path, _ in paths_and_leaves] == [
        10,
        20,
        30,
    ]
    assert TreeEngine.codify_key_path(paths_and_leaves[1][0], "tree") == "tree['a'][1]"
    assert TreeEngine.unflatten(definition, [1, 2, 3]) == {"a": [1, 2], "b": (3,)}
    leaves, _ = TreeEngine.flatten(
        tree, rules=DATA_RULES, is_leaf=lambda value, _: isinstance(value, list)
    )
    assert leaves == [[10, 20], 30]


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
    leaves, definition = TreeEngine.flatten(Box(3), rules=exact)
    assert leaves == [3]
    assert TreeEngine.unflatten(definition, [5]) == Box(5)
    assert TreeEngine.flatten(ChildBox(1), rules=exact)[0] == [ChildBox(1)]

    override = TreeHandler(
        lambda box: ([box.value + 1], TreeAux()),
        lambda children, _: Box(next(iter(children)) - 1),
    )
    pre = TreeRules(
        exact.handlers,
        pre_resolvers=(lambda value, _: override if isinstance(value, Box) else None,),
    )
    post = TreeRules(
        exact.handlers,
        post_resolvers=(lambda value, _: override if isinstance(value, Box) else None,),
    )
    assert TreeEngine.flatten(Box(3), rules=pre)[0] == [4]
    assert TreeEngine.flatten(Box(3), rules=post)[0] == [3]
    assert TreeEngine.flatten(ChildBox(1), rules=post)[0] == [2]
    assert TreeEngine.flatten(1, rules=post)[0] == [1]
    assert TreeEngine.flatten(Box(3), rules=pre, is_leaf=lambda *_: True)[0] == [Box(3)]


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
    leaves, definition = TreeEngine.flatten(tree, rules=rules)
    assert leaves == [3]
    assert list(TreeEngine.iter(tree, rules=rules)) == [3]
    paths_and_leaves = list(TreeEngine.iter_with_key_path(tree, rules=rules))
    assert paths_and_leaves == [((MappingKey("box"), AttributeKey("value")), 3)]
    assert TreeEngine.get_element(tree, paths_and_leaves[0][0]) == 3
    with pytest.raises(TypeError, match="Box is registered for traversal only"):
        TreeEngine.unflatten(definition, leaves)
    with pytest.raises(TypeError, match="Box is registered for traversal only"):
        TreeEngine.map(lambda value: value + 1, tree, rules=rules)


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
    leaves, definition = TreeEngine.flatten(value, rules=rules)
    assert len(leaves) == 1 and leaves[0] is value
    assert TreeEngine.unflatten(definition, leaves) is value
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
    leaves, definition = TreeEngine.flatten(value, rules=extended)
    assert leaves == [1]
    rebuilt = TreeEngine.unflatten(definition, [2])
    assert type(rebuilt) is ChildBox
    assert rebuilt == ChildBox(2, "child")


@pytest.mark.parametrize(
    "base, payload", [(list, [1]), (dict, {"x": 1}), (tuple, (1,))]
)
def test_builtin_container_subclasses_remain_opaque(base: type, payload: Any) -> None:
    class CustomContainer(base):
        pass

    value = CustomContainer(payload)
    leaves, definition = TreeEngine.flatten(value, rules=DATA_RULES)
    assert len(leaves) == 1 and leaves[0] is value
    assert TreeEngine.unflatten(definition, leaves) is value


def test_tree_definition_rejects_wrong_leaf_counts_and_keys() -> None:
    _, definition = TreeEngine.flatten([1, 2], rules=DATA_RULES)
    with pytest.raises(ValueError, match="Too few"):
        TreeEngine.unflatten(definition, [1])
    with pytest.raises(ValueError, match="Too many"):
        TreeEngine.unflatten(definition, [1, 2, 3])

    class Broken:
        pass

    broken = TreeRules(
        {
            Broken: TreeHandler(
                lambda _value: ([1], TreeAux(children_keys=())),
                lambda _children, _aux: Broken(),
            )
        }
    )
    with pytest.raises(ValueError, match="Not enough keys"):
        TreeEngine.flatten(Broken(), rules=broken)
    with pytest.raises(ValueError, match="Not enough keys"):
        list(TreeEngine.iter(Broken(), rules=broken))


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
