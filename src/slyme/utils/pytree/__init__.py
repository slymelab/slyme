"""
slyme pytree utility module.
"""

from .pytree import (
    # Types & Keys
    KeyPath,
    PyTreeKey,
    SequenceKey,
    MappingKey,
    AttributeKey,
    PyTreeAux,
    # Structure & Engine
    PyTreeDef,
    LeafDef,
    ContainerDef,
    TraverseAux,
    PyTreeEngine,
    # Instances
    default_pytree_engine,
    PYTREE_ENGINE_REGISTRY,
    # Functions
    tree_flatten,
    tree_flatten_with_path,
    tree_iter,
    tree_iter_with_path,
    tree_unflatten,
    tree_map,
    get_element,
    codify_path,
)

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
