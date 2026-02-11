"""
PyTree engine configuration and logic for Node.
"""

import types
from typing import Any, Iterable, cast
from slyme.utils.pytree import (
    PyTreeEngine,
    PYTREE_ENGINE_REGISTRY,
    PyTreeAux,
    AttributeKey,
    MappingKey,
)
from .core import (
    NodeDef,
    NodeExpressionDef,
    NodeWrapperDef,
    NodeExec,
    NodeExpressionExec,
    NodeWrapperExec,
)


# 1. Standard Engine: Preserves Types (Def -> Def, List -> List)
# Used for inspection, rendering, and validation.
NODE_PYTREE_ENGINE = PyTreeEngine("node_engine")
PYTREE_ENGINE_REGISTRY.register(NODE_PYTREE_ENGINE, key="node_engine")

# 2. Prepare Engine: Transforms Types (Def -> Exec, List -> Tuple, Dict -> MappingProxy)
# Used for compiling the definition tree into an execution tree.
NODE_PREPARE_PYTREE_ENGINE = PyTreeEngine("node_prepare")
PYTREE_ENGINE_REGISTRY.register(NODE_PREPARE_PYTREE_ENGINE, key="node_prepare")


# =============================================================================
# 1. NodeDef Logic
# =============================================================================


def _flatten_node_def(obj: NodeDef) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Flatten NodeDef into children (wrappers + kwargs values) and metadata.
    """
    # 1. Wrappers (List[NodeWrapperDef]) treated as a single child container
    children = [obj.wrappers]
    rich_keys = [AttributeKey("wrappers")]

    # 2. Kwargs
    for k, v in obj._kwargs.items():
        children.append(v)
        rich_keys.append(MappingKey(k))

    metadata = {"func": obj._func, "specs": obj._specs}
    return tuple(children), PyTreeAux(
        keys=tuple(rich_keys), metadata=metadata, cls=NodeDef
    )


def _unflatten_node_def(children: Iterable[Any], aux: PyTreeAux) -> NodeDef:
    """
    Reconstruct NodeDef (Standard Engine).
    """
    children_iter = iter(children)
    keys_iter = iter(aux.keys)

    # 1. Wrappers
    _ = next(keys_iter)
    wrappers = next(children_iter)

    # 2. Kwargs
    kwargs = {}
    for key in keys_iter:
        raw_key = cast("MappingKey", key).key
        val = next(children_iter)
        kwargs[raw_key] = val

    return NodeDef(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        kwargs=kwargs,
    )


def _unflatten_node_def_to_exec(children: Iterable[Any], aux: PyTreeAux) -> NodeExec:
    """
    Transform NodeDef into NodeExec (Prepare Engine).
    """
    children_iter = iter(children)
    keys_iter = iter(aux.keys)

    # 1. Wrappers
    # Note: In Prepare Engine, the list of wrappers is converted to a tuple recursively.
    _ = next(keys_iter)
    wrappers = cast(tuple, next(children_iter))

    # 2. Kwargs
    kwargs = {}
    for key in keys_iter:
        raw_key = cast("MappingKey", key).key
        val = next(children_iter)
        kwargs[raw_key] = val

    return NodeExec(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        kwargs=kwargs,
    )


# =============================================================================
# 2. NodeExpressionDef Logic
# =============================================================================


def _flatten_expression_def(
    obj: NodeExpressionDef,
) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Flatten NodeExpressionDef.
    """
    keys = tuple(obj._kwargs.keys())
    children = tuple(obj._kwargs.values())
    rich_keys = tuple(MappingKey(k) for k in keys)
    metadata = {"func": obj._func, "specs": obj._specs}
    return children, PyTreeAux(
        keys=rich_keys, metadata=metadata, cls=NodeExpressionDef
    )


def _unflatten_expression_def(
    children: Iterable[Any], aux: PyTreeAux
) -> NodeExpressionDef:
    """
    Reconstruct NodeExpressionDef (Standard Engine).
    """
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeExpressionDef(
        func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs
    )


def _unflatten_expression_def_to_exec(
    children: Iterable[Any], aux: PyTreeAux
) -> NodeExpressionExec:
    """
    Transform NodeExpressionDef into NodeExpressionExec (Prepare Engine).
    """
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeExpressionExec(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        kwargs=kwargs,
    )


# =============================================================================
# 3. NodeWrapperDef Logic
# =============================================================================


def _flatten_wrapper_def(obj: NodeWrapperDef) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Flatten NodeWrapperDef.
    """
    keys = tuple(obj._kwargs.keys())
    children = tuple(obj._kwargs.values())
    rich_keys = tuple(MappingKey(k) for k in keys)
    metadata = {"func": obj._func, "specs": obj._specs}
    return children, PyTreeAux(keys=rich_keys, metadata=metadata, cls=NodeWrapperDef)


def _unflatten_wrapper_def(children: Iterable[Any], aux: PyTreeAux) -> NodeWrapperDef:
    """
    Reconstruct NodeWrapperDef (Standard Engine).
    """
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeWrapperDef(
        func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs
    )


def _unflatten_wrapper_def_to_exec(
    children: Iterable[Any], aux: PyTreeAux
) -> NodeWrapperExec:
    """
    Transform NodeWrapperDef into NodeWrapperExec (Prepare Engine).
    """
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeWrapperExec(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        kwargs=kwargs,
    )


# =============================================================================
# 4. Exec Types Logic (Completeness)
# =============================================================================
# Although Exec types are usually the output of the Prepare engine,
# we register them in both engines to support re-entrant processing or inspection.


def _flatten_node_exec(obj: NodeExec) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    rich_keys = [AttributeKey("wrappers")]
    for k, v in obj._kwargs.items():
        children.append(v)
        rich_keys.append(MappingKey(k))
    metadata = {"func": obj._func, "specs": obj._specs}
    return tuple(children), PyTreeAux(
        keys=tuple(rich_keys), metadata=metadata, cls=NodeExec
    )


def _unflatten_node_exec(children: Iterable[Any], aux: PyTreeAux) -> NodeExec:
    children_iter = iter(children)
    keys_iter = iter(aux.keys)
    _ = next(keys_iter)
    wrappers = next(children_iter)
    kwargs = {}
    for key in keys_iter:
        raw_key = cast("MappingKey", key).key
        val = next(children_iter)
        kwargs[raw_key] = val
    return NodeExec(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        kwargs=kwargs,
    )


def _flatten_expression_exec(
    obj: NodeExpressionExec,
) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(obj._kwargs.keys())
    children = tuple(obj._kwargs.values())
    rich_keys = tuple(MappingKey(k) for k in keys)
    metadata = {"func": obj._func, "specs": obj._specs}
    return children, PyTreeAux(
        keys=rich_keys, metadata=metadata, cls=NodeExpressionExec
    )


def _unflatten_expression_exec(
    children: Iterable[Any], aux: PyTreeAux
) -> NodeExpressionExec:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeExpressionExec(
        func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs
    )


def _flatten_wrapper_exec(obj: NodeWrapperExec) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(obj._kwargs.keys())
    children = tuple(obj._kwargs.values())
    rich_keys = tuple(MappingKey(k) for k in keys)
    metadata = {"func": obj._func, "specs": obj._specs}
    return children, PyTreeAux(keys=rich_keys, metadata=metadata, cls=NodeWrapperExec)


def _unflatten_wrapper_exec(
    children: Iterable[Any], aux: PyTreeAux
) -> NodeWrapperExec:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeWrapperExec(
        func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs
    )


# =============================================================================
# 5. Container Logic (Prepare Transformations)
# =============================================================================


def _flatten_list(l: list) -> tuple[Iterable[Any], PyTreeAux]:
    return iter(l), PyTreeAux()


def _unflatten_to_tuple(children: Iterable[Any], _: PyTreeAux) -> tuple:
    return tuple(children)


def _flatten_tuple(t: tuple) -> tuple[Iterable[Any], PyTreeAux]:
    return iter(t), PyTreeAux()


def _unflatten_tuple(children: Iterable[Any], _: PyTreeAux) -> tuple:
    return tuple(children)


def _flatten_dict(d: dict) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(d.keys())
    rich_keys = tuple(MappingKey(k) for k in keys)
    children = (d[k] for k in keys)
    return children, PyTreeAux(keys=rich_keys)


def _unflatten_to_mapping_proxy(
    children: Iterable[Any], aux: PyTreeAux
) -> types.MappingProxyType:
    raw_keys = [k.key for k in cast("Iterable[MappingKey]", aux.keys)]
    return types.MappingProxyType(dict(zip(raw_keys, children)))


# =============================================================================
# 6. Registrations
# =============================================================================

# --- NODE_PYTREE_ENGINE (Def -> Def, Exec -> Exec) ---
NODE_PYTREE_ENGINE.register(
    NodeDef, _flatten_node_def, _unflatten_node_def, strict=True
)
NODE_PYTREE_ENGINE.register(
    NodeExpressionDef, _flatten_expression_def, _unflatten_expression_def, strict=True
)
NODE_PYTREE_ENGINE.register(
    NodeWrapperDef, _flatten_wrapper_def, _unflatten_wrapper_def, strict=True
)

NODE_PYTREE_ENGINE.register(
    NodeExec, _flatten_node_exec, _unflatten_node_exec, strict=True
)
NODE_PYTREE_ENGINE.register(
    NodeExpressionExec,
    _flatten_expression_exec,
    _unflatten_expression_exec,
    strict=True,
)
NODE_PYTREE_ENGINE.register(
    NodeWrapperExec, _flatten_wrapper_exec, _unflatten_wrapper_exec, strict=True
)


# --- NODE_PREPARE_PYTREE_ENGINE (Def -> Exec, Mutables -> Immutables) ---

# 1. Custom Containers
NODE_PREPARE_PYTREE_ENGINE.register(list, _flatten_list, _unflatten_to_tuple)
NODE_PREPARE_PYTREE_ENGINE.register(tuple, _flatten_tuple, _unflatten_tuple)
NODE_PREPARE_PYTREE_ENGINE.register(
    dict, _flatten_dict, _unflatten_to_mapping_proxy
)

# 2. Def -> Exec Transformations
NODE_PREPARE_PYTREE_ENGINE.register(
    NodeDef, _flatten_node_def, _unflatten_node_def_to_exec, strict=True
)
NODE_PREPARE_PYTREE_ENGINE.register(
    NodeExpressionDef,
    _flatten_expression_def,
    _unflatten_expression_def_to_exec,
    strict=True,
)
NODE_PREPARE_PYTREE_ENGINE.register(
    NodeWrapperDef,
    _flatten_wrapper_def,
    _unflatten_wrapper_def_to_exec,
    strict=True,
)

# 3. Exec Identity (Exec -> Exec)
# Ensures that if an Exec node is encountered during preparation, it is preserved (deeply).
NODE_PREPARE_PYTREE_ENGINE.register(
    NodeExec, _flatten_node_exec, _unflatten_node_exec, strict=True
)
NODE_PREPARE_PYTREE_ENGINE.register(
    NodeExpressionExec,
    _flatten_expression_exec,
    _unflatten_expression_exec,
    strict=True,
)
NODE_PREPARE_PYTREE_ENGINE.register(
    NodeWrapperExec, _flatten_wrapper_exec, _unflatten_wrapper_exec, strict=True
)
