from __future__ import annotations

import pytest

from slyme.tree import (
    AttributeKey,
    MappingKey,
    TraverseAux,
    TreeAux,
    TreeHandler,
    TreeResolver,
    TreeRules,
    flatten_one,
)
from slyme.tree.common import DATA_RULES


def test_leaf_empty_container_and_immediate_children_are_distinct() -> None:
    assert flatten_one(object(), rules=DATA_RULES) is None
    _, children, aux = flatten_one([], rules=DATA_RULES)
    assert list(children) == []
    assert aux.children_keys == ()
    child = {"next": []}
    _, children, aux = flatten_one({"child": child}, rules=DATA_RULES)
    assert next(iter(children)) is child
    assert aux.children_keys == (MappingKey("child"),)


def test_aux_resolver_requires_and_receives_explicit_context() -> None:
    calls = []

    def resolve(value, aux):
        calls.append((value, aux))
        return False

    root = [[1]]
    resolver = TreeResolver(resolve, takes_aux=True)
    with pytest.raises(ValueError, match="traverse_aux is required"):
        flatten_one(root, rules=DATA_RULES, resolver=resolver)
    assert calls == []

    root_aux = TraverseAux(parent=None, key_path=())
    _, children, _ = flatten_one(
        root, rules=DATA_RULES, resolver=resolver, traverse_aux=root_aux
    )
    assert list(children) == [[1]]
    assert calls == [(root, root_aux)]
    assert calls[0][1] is root_aux

    aux = TraverseAux(parent=root, key_path=(AttributeKey("child"),))
    flatten_one(root[0], rules=DATA_RULES, resolver=resolver, traverse_aux=aux)
    assert calls[-1][1] is aux
    assert len(calls) == 2


def test_resolver_can_override_and_suppress_a_handler() -> None:
    value = object()
    aux = TreeAux(metadata={"tag": "retained"})
    handler = TreeHandler(lambda obj: ((obj,), aux), None)
    result = flatten_one(
        value, rules=TreeRules(), resolver=TreeResolver(lambda obj: handler)
    )
    actual_handler, children, actual_aux = result
    assert actual_handler is handler
    assert next(iter(children)) is value
    assert actual_aux is aux
    assert (
        flatten_one([], rules=DATA_RULES, resolver=TreeResolver(lambda obj: True))
        is None
    )


def test_selected_handler_supports_reconstruction_without_repeating_dispatch() -> None:
    calls = []

    def resolve(value):
        calls.append(value)
        return handler

    aux = TreeAux(metadata={"label": "resolved"})

    def expand(value):
        def children():
            calls.append("children")
            yield from value

        return children(), aux

    handler = TreeHandler(
        expand,
        lambda values, aux: (aux.metadata["label"], tuple(values)),
    )
    value = [1, 2]
    selected, children, actual_aux = flatten_one(
        value, rules=DATA_RULES, resolver=TreeResolver(resolve)
    )
    assert calls == [value]
    assert selected.unflatten(children, actual_aux) == ("resolved", (1, 2))
    assert calls == [value, "children"]
