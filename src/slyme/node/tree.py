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
NODE_PYTREE_ENGINE = PyTreeEngine("node_engine")
PYTREE_ENGINE_REGISTRY.register(NODE_PYTREE_ENGINE, key="node_engine")

# 2. Freeze Engine: Transforms Types (Def -> Exec, List -> Tuple)
FREEZE_PYTREE_ENGINE = PyTreeEngine("freeze_engine")


# --- Flatten Logic (Shared) ---

def _flatten_node_def(obj: NodeDef) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    rich_keys = [AttributeKey("wrappers")]
    for k, v in obj._kwargs.items():
        children.append(v)
        rich_keys.append(MappingKey(k))
    metadata = {"func": obj._func, "specs": obj._specs}
    return tuple(children), PyTreeAux(
        keys=tuple(rich_keys), metadata=metadata, cls=NodeDef
    )

def _flatten_element_def(
    obj: Any
) -> tuple[Iterable[Any], PyTreeAux]:
    # Handles both NodeExpressionDef and NodeWrapperDef
    keys = tuple(obj._kwargs.keys())
    children = tuple(obj._kwargs.values())
    rich_keys = tuple(MappingKey(k) for k in keys)
    metadata = {"func": obj._func, "specs": obj._specs}
    return children, PyTreeAux(keys=rich_keys, metadata=metadata, cls=type(obj))


# --- Unflatten Logic for Standard Engine (Preserves Type) ---

def _unflatten_node_def(children: Iterable[Any], aux: PyTreeAux) -> NodeDef:
    children_iter = iter(children)
    keys_iter = iter(aux.keys)
    _ = next(keys_iter)
    wrappers = next(children_iter)
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

def _unflatten_element_def(children: Iterable[Any], aux: PyTreeAux) -> Any:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    # aux.cls is either NodeExpressionDef or NodeWrapperDef
    return aux.cls(func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs)


# --- Unflatten Logic for Freeze Engine (Transforms Type) ---

def _unflatten_def_to_exec_node(children: Iterable[Any], aux: PyTreeAux) -> NodeExec:
    children_iter = iter(children)
    keys_iter = iter(aux.keys)
    
    # 1. Wrappers: Already converted to tuple by FREEZE_PYTREE_ENGINE recursion
    _ = next(keys_iter)
    wrappers = cast(tuple, next(children_iter))

    # 2. Kwargs: Recursively frozen children
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

def _unflatten_def_to_exec_expression(children: Iterable[Any], aux: PyTreeAux) -> NodeExpressionExec:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeExpressionExec(
        func=aux.metadata["func"], 
        specs=aux.metadata["specs"], 
        kwargs=kwargs
    )

def _unflatten_def_to_exec_wrapper(children: Iterable[Any], aux: PyTreeAux) -> NodeWrapperExec:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeWrapperExec(
        func=aux.metadata["func"], 
        specs=aux.metadata["specs"], 
        kwargs=kwargs
    )


# --- Flatten/Unflatten Logic for Exec types (Completeness) ---

def _flatten_node_exec(obj: NodeExec) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    rich_keys = [AttributeKey("wrappers")]
    for k, v in obj._kwargs.items():
        children.append(v)
        rich_keys.append(MappingKey(k))
    metadata = {"func": obj._func, "specs": obj._specs}
    return tuple(children), PyTreeAux(keys=tuple(rich_keys), metadata=metadata, cls=NodeExec)

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
    return NodeExec(func=aux.metadata["func"], specs=aux.metadata["specs"], wrappers=wrappers, kwargs=kwargs)


# --- Standard Container Freezing Logic ---

def _flatten_list(l: list) -> tuple[Iterable[Any], PyTreeAux]:
    return iter(l), PyTreeAux()

def _unflatten_to_tuple(children: Iterable[Any], _: PyTreeAux) -> tuple:
    return tuple(children)

def _flatten_dict(d: dict) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(d.keys())
    rich_keys = tuple(MappingKey(k) for k in keys)
    children = (d[k] for k in keys)
    return children, PyTreeAux(keys=rich_keys)

def _unflatten_to_mapping_proxy(children: Iterable[Any], aux: PyTreeAux) -> types.MappingProxyType:
    raw_keys = [k.key for k in cast("Iterable[MappingKey]", aux.keys)]
    return types.MappingProxyType(dict(zip(raw_keys, children)))


# --- Registration (Executed at Module Level) ---
# 1. NODE_PYTREE_ENGINE (Standard: Def -> Def)
NODE_PYTREE_ENGINE.register(NodeDef, _flatten_node_def, _unflatten_node_def, strict=True)
NODE_PYTREE_ENGINE.register(NodeExpressionDef, _flatten_element_def, _unflatten_element_def, strict=True)
NODE_PYTREE_ENGINE.register(NodeWrapperDef, _flatten_element_def, _unflatten_element_def, strict=True)
# Register Exec types for completeness (Exec -> Exec)
NODE_PYTREE_ENGINE.register(NodeExec, _flatten_node_exec, _unflatten_node_exec, strict=True)

# 2. FREEZE_PYTREE_ENGINE (Transformer: Def -> Exec, List -> Tuple, Dict -> Proxy)
# Custom Containers
FREEZE_PYTREE_ENGINE.register(list, _flatten_list, _unflatten_to_tuple)
FREEZE_PYTREE_ENGINE.register(dict, _flatten_dict, _unflatten_to_mapping_proxy)
# Def -> Exec Transformations
FREEZE_PYTREE_ENGINE.register(NodeDef, _flatten_node_def, _unflatten_def_to_exec_node, strict=True)
FREEZE_PYTREE_ENGINE.register(NodeExpressionDef, _flatten_element_def, _unflatten_def_to_exec_expression, strict=True)
FREEZE_PYTREE_ENGINE.register(NodeWrapperDef, _flatten_element_def, _unflatten_def_to_exec_wrapper, strict=True)
