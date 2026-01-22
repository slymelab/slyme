from collections.abc import Iterable, Callable, Iterator
from typing import Any, Optional
from ._pytree_py import (
    PyTreeKey,
    SequenceKey,
    MappingKey,
    AttributeKey,
    KeyPath,
    PyTreeAux,
    PyTreeDef,
    LeafDef,
    ContainerDef,
    TraverseAux,
    PyTreeEngine,
    default_pytree_engine,
    PYTREE_ENGINE_REGISTRY,
)

def tree_flatten(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> tuple[list[Any], PyTreeDef]: ...
def tree_flatten_with_path(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> tuple[list[tuple[KeyPath, Any]], PyTreeDef]: ...
def tree_iter(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> Iterator[Any]: ...
def tree_iter_with_path(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> Iterator[tuple[KeyPath, Any]]: ...
def tree_unflatten(treedef: PyTreeDef, leaves: Iterable[Any]) -> Any: ...
def tree_map(
    func: Callable[..., Any],
    tree: Any,
    *,
    is_leaf: Optional[Callable[[Any], bool]] = None,
) -> Any: ...
def get_element(tree: Any, path: KeyPath) -> Any: ...
def codify_path(path: KeyPath, root_name: str = "tree") -> str: ...

__all__ = [
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
    "default_pytree_engine",
    "PYTREE_ENGINE_REGISTRY",
    "tree_flatten",
    "tree_flatten_with_path",
    "tree_iter",
    "tree_iter_with_path",
    "tree_unflatten",
    "tree_map",
    "get_element",
    "codify_path",
]
