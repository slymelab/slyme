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

"""PyTree engine used for Node inspection and Ref discovery."""

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, cast

from slyme.utils.pytree import (
    PYTREE_ENGINE_REGISTRY,
    AttributeKey,
    PyTreeAux,
    PyTreeEngine,
    PyTreeKey,
)
from slyme.utils.pytree.common import flatten_mapping_proxy, unflatten_mapping_proxy

from .core import Node, Wrapper

NODE_ENGINE = PyTreeEngine("node_engine")
PYTREE_ENGINE_REGISTRY.register(NODE_ENGINE, key="node_engine")


@dataclass(frozen=True)
class _NodeParameterKey(PyTreeKey):
    """Address one NodeElement build parameter without attribute projection."""

    name: str

    def resolve(self, element: Any) -> Any:
        return element.get(self.name)

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.get({self.name!r})"


def _flatten_node(obj: Node) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    keys: list[PyTreeKey] = [AttributeKey("wrappers")]
    for name in obj._specs:
        children.append(obj.get(name))
        keys.append(_NodeParameterKey(name))
    return tuple(children), PyTreeAux(
        children_keys=tuple(keys),
        metadata={"func": obj._func, "specs": obj._specs},
        cls=Node,
    )


def _unflatten_node(children: Iterable[Any], aux: PyTreeAux) -> Node:
    if aux.children_keys is None:
        raise ValueError("Missing keys for Node unflattening.")
    iterator = zip(aux.children_keys, children, strict=True)
    _, wrappers = next(iterator)
    params = {cast("_NodeParameterKey", key).name: value for key, value in iterator}
    return Node(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        params=params,
    )


def _flatten_wrapper(obj: Wrapper[Any]) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(_NodeParameterKey(name) for name in obj._specs)
    return tuple(obj.get(name) for name in obj._specs), PyTreeAux(
        children_keys=keys,
        metadata={"func": obj._func, "specs": obj._specs},
        cls=Wrapper,
    )


def _unflatten_wrapper(children: Iterable[Any], aux: PyTreeAux) -> Wrapper[Any]:
    if aux.children_keys is None:
        raise ValueError("Missing keys for Wrapper unflattening.")
    params = {
        cast("_NodeParameterKey", key).name: value
        for key, value in zip(aux.children_keys, children, strict=True)
    }
    return Wrapper(
        func=aux.metadata["func"], specs=aux.metadata["specs"], params=params
    )


NODE_ENGINE.register(Node, _flatten_node, _unflatten_node, strict=True)
NODE_ENGINE.register(Wrapper, _flatten_wrapper, _unflatten_wrapper, strict=True)
NODE_ENGINE.register(MappingProxyType, flatten_mapping_proxy, unflatten_mapping_proxy)
