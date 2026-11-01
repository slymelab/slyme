"""Inspect explicit nested composition through normal Node parameters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from slyme.tree import AttributeKey, TreeAux, TreeHandler, TreeRules

from .core import Auto, Node

NODE_INSPECTION_TYPE_NAMES: Mapping[type, str] = {
    Node: "slyme.node",
    Auto: "slyme.auto",
}


def _flatten(value: Node[..., Any]) -> tuple[Iterable[Any], TreeAux]:
    names = ("func", "params", "metadata", "inject_ctx", "result_to")
    return (
        (getattr(value, name) for name in names),
        TreeAux(children_keys=tuple(AttributeKey(name) for name in names)),
    )


NODE_INSPECTION_TREE_RULES = TreeRules(
    {
        Node: TreeHandler(_flatten, None),
        Auto: TreeHandler(
            lambda obj: ((obj.value,), TreeAux(children_keys=(AttributeKey("value"),))),
            None,
        ),
    }
)
