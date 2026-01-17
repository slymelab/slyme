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
    flatten,
    flatten_with_path,
    iter,
    iter_with_path,
    unflatten,
    map,
    get_entry,
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
    "flatten",
    "flatten_with_path",
    "iter",
    "iter_with_path",
    "unflatten",
    "map",
    "get_entry",
    "codify_path",
]
