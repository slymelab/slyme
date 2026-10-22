"""Describe stored bindings, without invoking Nodes or composing Wrappers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from slyme.tree import AttributeKey, TreeAux, TreeHandler, TreeRules

from .core import Auto, Node, NodeElement, Wrapper

NODE_INSPECTION_TYPE_NAMES: Mapping[type, str] = {
    Node: "slyme.node",
    Wrapper: "slyme.wrapper",
    Auto: "slyme.auto",
}


def _flatten(value: NodeElement[..., Any]) -> tuple[Iterable[Any], TreeAux]:
    names: tuple[str, ...] = ("func", "params", "metadata")
    if isinstance(value, Node):
        names += ("wrappers",)
    return (
        (getattr(value, name) for name in names),
        TreeAux(children_keys=tuple(AttributeKey(name) for name in names)),
    )


NODE_INSPECTION_TREE_RULES = TreeRules(
    {
        Node: TreeHandler(_flatten, None),
        Wrapper: TreeHandler(_flatten, None),
        Auto: TreeHandler(
            lambda obj: ((obj.value,), TreeAux(children_keys=(AttributeKey("value"),))),
            None,
        ),
    }
)
