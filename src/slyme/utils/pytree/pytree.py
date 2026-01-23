"""
Public PyTree API with C-extension fallback support.
"""

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
        PYTREE_ENGINE_REGISTRY,
    )

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
    "PYTREE_ENGINE_REGISTRY",
]
