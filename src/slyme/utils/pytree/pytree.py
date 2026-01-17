"""
Public PyTree API with C-extension fallback support.
"""

from collections.abc import Iterable, Callable, Iterator
from typing import Any, Optional
from ._pytree_py import KeyPath

try:
    from ._pytree_c import (
        PyTreeKey,
        SequenceKey,
        MappingKey,
        AttributeKey,
        PyTreeAux,
        PyTreeDef,
        LeafDef,
        ContainerDef,
        TraverseAux,
        PyTreeEngine,
        default_pytree_engine,
        PYTREE_ENGINE_REGISTRY,
    )
except (ImportError, ModuleNotFoundError):
    from ._pytree_py import (
        PyTreeKey,
        SequenceKey,
        MappingKey,
        AttributeKey,
        PyTreeAux,
        PyTreeDef,
        LeafDef,
        ContainerDef,
        TraverseAux,
        PyTreeEngine,
        default_pytree_engine,
        PYTREE_ENGINE_REGISTRY,
    )


def flatten(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> tuple[list[Any], PyTreeDef]:
    """Alias for default_pytree_engine.flatten."""
    return default_pytree_engine.flatten(tree, is_leaf=is_leaf)


def flatten_with_path(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> tuple[list[tuple[KeyPath, Any]], PyTreeDef]:
    """Alias for default_pytree_engine.flatten_with_path."""
    return default_pytree_engine.flatten_with_path(tree, is_leaf=is_leaf)


def iter(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> Iterator[Any]:
    """Alias for default_pytree_engine.iter."""
    return default_pytree_engine.iter(tree, is_leaf=is_leaf)


def iter_with_path(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> Iterator[tuple[KeyPath, Any]]:
    """Alias for default_pytree_engine.iter_with_path."""
    return default_pytree_engine.iter_with_path(tree, is_leaf=is_leaf)


def unflatten(treedef: PyTreeDef, leaves: Iterable[Any]) -> Any:
    """Alias for default_pytree_engine.unflatten."""
    return default_pytree_engine.unflatten(treedef, leaves)


def map(
    func: Callable[..., Any],
    tree: Any,
    *,
    is_leaf: Optional[Callable[[Any], bool]] = None,
) -> Any:
    """Alias for default_pytree_engine.map."""
    return default_pytree_engine.map(func, tree, is_leaf=is_leaf)


def get_entry(tree: Any, path: KeyPath) -> Any:
    """Alias for default_pytree_engine.get_entry."""
    return default_pytree_engine.get_entry(tree, path)


def codify_path(path: KeyPath, root_name: str = "tree") -> str:
    """Alias for default_pytree_engine.codify_path."""
    return default_pytree_engine.codify_path(path, root_name)


__all__ = [
    # Classes
    "KeyPath",
    "PyTreeKey",
    "SequenceKey",
    "MappingKey",
    "AttributeKey",
    "PyTreeAux",
    "PyTreeDef",
    "LeafDef",
    "ContainerDef",
    "TraverseAux",
    "PyTreeEngine",
    # Instances
    "default_pytree_engine",
    "PYTREE_ENGINE_REGISTRY",
    # Helper Functions
    "flatten",
    "flatten_with_path",
    "iter",
    "iter_with_path",
    "unflatten",
    "map",
    "get_entry",
    "codify_path",
]
