"""
Common utilities and shared logic for PyTree operations.
"""

from typing import Any, Iterable, cast
from types import MappingProxyType
from .core import PyTreeAux, MappingKey


def flatten_mapping_proxy(data: MappingProxyType) -> tuple[Iterable[Any], PyTreeAux]:
    """Flatten MappingProxyType."""
    keys = tuple(data.keys())
    rich_keys = tuple(MappingKey(k) for k in keys)
    children = (data[k] for k in keys)
    return children, PyTreeAux(children_keys=rich_keys)


def unflatten_mapping_proxy(
    children: Iterable[Any], aux: PyTreeAux
) -> MappingProxyType:
    """Unflatten to MappingProxyType."""
    if aux.children_keys is None:
        raise ValueError("Missing keys for MappingProxyType unflattening.")
    raw_keys = [k.key for k in cast("Iterable[MappingKey]", aux.children_keys)]
    return MappingProxyType(dict(zip(raw_keys, children)))


def flatten_dict(data: dict) -> tuple[Iterable[Any], PyTreeAux]:
    """Flatten dict."""
    keys = tuple(data.keys())
    # Wrap keys in DictKey for path tracking.
    rich_keys = tuple(MappingKey(k) for k in keys)
    # Yield values as children.
    children = (data[k] for k in keys)
    return children, PyTreeAux(children_keys=rich_keys)


def unflatten_dict(children: Iterable[Any], tree_aux: PyTreeAux) -> dict:
    """Unflatten to dict."""
    if tree_aux.children_keys is None:
        raise ValueError("Missing keys in TreeAux for dict unflattening.")
    # Unwrap DictKey to get raw keys.
    raw_keys = [k.key for k in cast("Iterable[MappingKey]", tree_aux.children_keys)]
    return dict(zip(raw_keys, children))
