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

def flatten(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> tuple[list[Any], PyTreeDef]: ...
def flatten_with_path(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> tuple[list[tuple[KeyPath, Any]], PyTreeDef]: ...
def iter_flatten(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> Iterator[Any]: ...
def iter_flatten_with_path(
    tree: Any, *, is_leaf: Optional[Callable[[Any], bool]] = None
) -> Iterator[tuple[KeyPath, Any]]: ...
def unflatten(treedef: PyTreeDef, leaves: Iterable[Any]) -> Any: ...
def map(
    func: Callable[..., Any],
    tree: Any,
    *,
    is_leaf: Optional[Callable[[Any], bool]] = None,
) -> Any: ...
def get_entry(tree: Any, path: KeyPath) -> Any: ...
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
    "flatten",
    "flatten_with_path",
    "iter_flatten",
    "iter_flatten_with_path",
    "unflatten",
    "map",
    "get_entry",
    "codify_path",
]
