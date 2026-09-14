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

"""Tree engine for inspection and Ref discovery, not Node/Wrapper reconstruction."""

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from slyme.utils.tree import (
    TREE_ENGINE_REGISTRY,
    AttributeKey,
    TreeAux,
    TreeEngine,
    TreeKey,
)
from slyme.utils.tree.common import flatten_mapping_proxy, unflatten_mapping_proxy

from .core import Node, Wrapper

NODE_ENGINE = TreeEngine("node_engine")
TREE_ENGINE_REGISTRY.register(NODE_ENGINE, key="node_engine")


@dataclass(frozen=True)
class _NodeParameterKey(TreeKey):
    """Address one NodeElement build parameter without attribute projection."""

    name: str

    def resolve(self, element: Any) -> Any:
        return element.get(self.name)

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.get({self.name!r})"


def _flatten_node(obj: Node) -> tuple[Iterable[Any], TreeAux]:
    children = [obj.wrappers]
    keys: list[TreeKey] = [AttributeKey("wrappers")]
    for name in obj._specs:
        children.append(obj.get(name))
        keys.append(_NodeParameterKey(name))
    return tuple(children), TreeAux(
        children_keys=tuple(keys),
        cls=Node,
    )


def _flatten_wrapper(obj: Wrapper[Any]) -> tuple[Iterable[Any], TreeAux]:
    keys = tuple(_NodeParameterKey(name) for name in obj._specs)
    return tuple(obj.get(name) for name in obj._specs), TreeAux(
        children_keys=keys,
        cls=Wrapper,
    )


NODE_ENGINE.register(Node, _flatten_node, None, strict=True)
NODE_ENGINE.register(Wrapper, _flatten_wrapper, None, strict=True)
NODE_ENGINE.register(MappingProxyType, flatten_mapping_proxy, unflatten_mapping_proxy)
