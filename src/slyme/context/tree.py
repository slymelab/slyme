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

from typing import Any, cast
from collections.abc import Iterable
from types import MappingProxyType
from slyme.utils.pytree import (
    AttributeKey,
    PyTreeEngine,
    PyTreeAux,
    MappingKey,
    PYTREE_ENGINE_REGISTRY,
)
from slyme.utils.pytree.common import flatten_mapping_proxy, unflatten_mapping_proxy
from .core import ContextData, Context

# Context engine
CONTEXT_ENGINE = PyTreeEngine("context_engine", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(CONTEXT_ENGINE, key="context_engine")


def _flatten_context_data(data: ContextData) -> tuple[Iterable[Any], PyTreeAux]:
    """Flatten ContextData."""
    keys = tuple(data.keys())
    rich_keys = tuple(MappingKey(k) for k in keys)
    children = (data[k] for k in keys)
    return children, PyTreeAux(children_keys=rich_keys)


def _unflatten_context_data(children: Iterable[Any], aux: PyTreeAux) -> ContextData:
    """Unflatten to ContextData."""
    if aux.children_keys is None:
        raise ValueError("Missing keys for ContextData unflattening.")
    raw_keys = [k.key for k in cast("Iterable[MappingKey]", aux.children_keys)]
    return ContextData(zip(raw_keys, children))


def _flatten_context(context: Context) -> tuple[Iterable[Any], PyTreeAux]:
    """Flatten Context -> (root_context_data, )."""
    return (context._root,), PyTreeAux(
        metadata={"hook": context._hook}, children_keys=(AttributeKey("_root"),)
    )


def _unflatten_context(children: Iterable[Any], aux: PyTreeAux) -> Context:
    """Unflatten Context."""
    (root,) = children
    return Context._from_context_data(root, hook=aux.metadata["hook"])


CONTEXT_ENGINE.register(ContextData, _flatten_context_data, _unflatten_context_data)
CONTEXT_ENGINE.register(Context, _flatten_context, _unflatten_context)
# Context eval
CTX_EVAL_ENGINE = PyTreeEngine("ctx_eval_engine", register_defaults=True)
PYTREE_ENGINE_REGISTRY.register(CTX_EVAL_ENGINE, key="ctx_eval_engine")
CTX_EVAL_ENGINE.register(
    MappingProxyType, flatten_mapping_proxy, unflatten_mapping_proxy
)
