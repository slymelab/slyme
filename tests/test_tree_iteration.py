from __future__ import annotations

import sys
from typing import Any

import pytest

from slyme.tree import (
    SequenceKey,
    TreeAux,
    TreeHandler,
    TreeResolver,
    TreeRules,
    flatten,
    flatten_with_key_path,
    iter_with_key_path,
)
from slyme.tree import iter as iter_leaves
from slyme.tree.common import DATA_RULES


@pytest.mark.parametrize(
    "operation", [flatten, flatten_with_key_path, iter_leaves, iter_with_key_path]
)
@pytest.mark.parametrize(
    "resolver",
    [None, TreeResolver(lambda element, traverse_aux: False, takes_aux=True)],
)
def test_tree_traversal_exceeds_python_recursion_limit(operation, resolver) -> None:
    recursion_limit = sys.getrecursionlimit()
    depth = recursion_limit * 2 + 1
    leaf = object()
    tree: Any = leaf
    for _ in range(depth):
        tree = [tree]

    result = operation(tree, rules=DATA_RULES, resolver=resolver)
    if operation in (flatten, flatten_with_key_path):
        leaves, definition = result
        rebuilt = definition.unflatten([leaf])
        for _ in range(depth):
            assert type(rebuilt) is list
            assert len(rebuilt) == 1
            rebuilt = rebuilt[0]
        assert rebuilt is leaf
    else:
        leaves = list(result)

    if operation in (flatten_with_key_path, iter_with_key_path):
        assert leaves == [((SequenceKey(0),) * depth, leaf)]
    else:
        assert leaves == [leaf]
    assert sys.getrecursionlimit() == recursion_limit


@pytest.mark.parametrize("operation", [iter_leaves, iter_with_key_path])
@pytest.mark.parametrize("close_early", [False, True])
def test_tree_iterator_consumes_children_lazily_and_preserves_errors(
    operation, close_early: bool
) -> None:
    tree = [[1, 2], 3]
    calls = []
    error = LookupError("child iteration failed")

    def expand(values):
        name = "outer" if values is tree else "inner"

        def children():
            for index, value in enumerate(values):
                calls.append((name, index))
                if name == "outer" and index == 1:
                    raise error
                yield value

        return children(), TreeAux(
            children_keys=tuple(SequenceKey(index) for index in range(len(values)))
        )

    rules = TreeRules({list: TreeHandler(expand, None)})
    iterator = operation(tree, rules=rules)
    assert calls == []
    assert next(iterator) == (
        ((SequenceKey(0), SequenceKey(0)), 1) if operation is iter_with_key_path else 1
    )
    assert calls == [("outer", 0), ("inner", 0)]
    assert next(iterator) == (
        ((SequenceKey(0), SequenceKey(1)), 2) if operation is iter_with_key_path else 2
    )
    assert calls == [("outer", 0), ("inner", 0), ("inner", 1)]

    if close_early:
        iterator.close()
        assert calls == [("outer", 0), ("inner", 0), ("inner", 1)]
    else:
        with pytest.raises(LookupError) as caught:
            next(iterator)
        assert caught.value is error
        assert calls == [("outer", 0), ("inner", 0), ("inner", 1), ("outer", 1)]
