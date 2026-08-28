# Copyright 2026 The SlymeLab Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""PyTree engines used for Node inspection and call-local snapshots."""

import types
from types import MappingProxyType
from typing import Any, Iterable, cast

from slyme.utils.pytree import (
    AttributeKey,
    MappingKey,
    PYTREE_ENGINE_REGISTRY,
    PyTreeAux,
    PyTreeEngine,
)
from slyme.utils.pytree.common import flatten_mapping_proxy, unflatten_mapping_proxy

from .core import AsyncNode, AsyncWrapper, Node, Wrapper


NODE_ENGINE = PyTreeEngine("node_engine")
PYTREE_ENGINE_REGISTRY.register(NODE_ENGINE, key="node_engine")

# Node/Wrapper and future Slot objects are deliberately unregistered leaves.
NODE_SNAPSHOT_ENGINE = PyTreeEngine("node_snapshot", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(NODE_SNAPSHOT_ENGINE, key="node_snapshot")


def _flatten_node(obj: Node) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    keys = [AttributeKey("wrappers")]
    for name, value in obj._kwargs.items():
        children.append(value)
        keys.append(MappingKey(name))
    return tuple(children), PyTreeAux(
        children_keys=tuple(keys),
        metadata={"func": obj._func, "specs": obj._specs},
        cls=Node,
    )


def _unflatten_node(children: Iterable[Any], aux: PyTreeAux) -> Node:
    iterator = zip(aux.children_keys, children)
    _, wrappers = next(iterator)
    kwargs = {cast("MappingKey", key).key: value for key, value in iterator}
    return Node(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        kwargs=kwargs,
    )


def _flatten_wrapper(obj: Wrapper) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(MappingKey(name) for name in obj._kwargs)
    return tuple(obj._kwargs.values()), PyTreeAux(
        children_keys=keys,
        metadata={"func": obj._func, "specs": obj._specs},
        cls=Wrapper,
    )


def _unflatten_wrapper(children: Iterable[Any], aux: PyTreeAux) -> Wrapper:
    kwargs = {
        cast("MappingKey", key).key: value
        for key, value in zip(aux.children_keys, children)
    }
    return Wrapper(
        func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs
    )


def _flatten_async_node(obj: AsyncNode) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    keys = [AttributeKey("wrappers")]
    for name, value in obj._kwargs.items():
        children.append(value)
        keys.append(MappingKey(name))
    return tuple(children), PyTreeAux(
        children_keys=tuple(keys),
        metadata={"func": obj._func, "specs": obj._specs},
        cls=AsyncNode,
    )


def _unflatten_async_node(children: Iterable[Any], aux: PyTreeAux) -> AsyncNode:
    iterator = zip(aux.children_keys, children)
    _, wrappers = next(iterator)
    kwargs = {cast("MappingKey", key).key: value for key, value in iterator}
    return AsyncNode(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        kwargs=kwargs,
    )


def _flatten_async_wrapper(obj: AsyncWrapper) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(MappingKey(name) for name in obj._kwargs)
    return tuple(obj._kwargs.values()), PyTreeAux(
        children_keys=keys,
        metadata={"func": obj._func, "specs": obj._specs},
        cls=AsyncWrapper,
    )


def _unflatten_async_wrapper(
    children: Iterable[Any], aux: PyTreeAux
) -> AsyncWrapper:
    kwargs = {
        cast("MappingKey", key).key: value
        for key, value in zip(aux.children_keys, children)
    }
    return AsyncWrapper(
        func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs
    )


def _flatten_list(value: list[Any]) -> tuple[Iterable[Any], PyTreeAux]:
    return iter(value), PyTreeAux()


def _unflatten_tuple(children: Iterable[Any], _: PyTreeAux) -> tuple[Any, ...]:
    return tuple(children)


def _flatten_tuple(value: tuple[Any, ...]) -> tuple[Iterable[Any], PyTreeAux]:
    return iter(value), PyTreeAux()


def _flatten_dict(value: dict[Any, Any]) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(value)
    return (value[key] for key in keys), PyTreeAux(
        children_keys=tuple(MappingKey(key) for key in keys)
    )


def _unflatten_to_mapping_proxy(
    children: Iterable[Any], aux: PyTreeAux
) -> types.MappingProxyType:
    keys = [cast("MappingKey", key).key for key in aux.children_keys]
    return types.MappingProxyType(dict(zip(keys, children)))


NODE_ENGINE.register(Node, _flatten_node, _unflatten_node, strict=True)
NODE_ENGINE.register(Wrapper, _flatten_wrapper, _unflatten_wrapper, strict=True)
NODE_ENGINE.register(
    AsyncNode, _flatten_async_node, _unflatten_async_node, strict=True
)
NODE_ENGINE.register(
    AsyncWrapper, _flatten_async_wrapper, _unflatten_async_wrapper, strict=True
)
NODE_ENGINE.register(MappingProxyType, flatten_mapping_proxy, unflatten_mapping_proxy)

NODE_SNAPSHOT_ENGINE.register(list, _flatten_list, _unflatten_tuple)
NODE_SNAPSHOT_ENGINE.register(tuple, _flatten_tuple, _unflatten_tuple)
NODE_SNAPSHOT_ENGINE.register(dict, _flatten_dict, _unflatten_to_mapping_proxy)
NODE_SNAPSHOT_ENGINE.register(
    MappingProxyType, flatten_mapping_proxy, unflatten_mapping_proxy
)
