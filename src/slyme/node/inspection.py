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

"""Inspect explicit nested composition through normal Node parameters."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from slyme.tree import AttributeKey, TreeAux, TreeHandler, TreeRules

from .core import Auto, Node

NODE_INSPECTION_TYPE_NAMES: Mapping[type, str] = {
    Node: "slyme.node",
    Auto: "slyme.auto",
}


def _flatten(value: Node[..., Any]) -> tuple[Iterable[Any], TreeAux]:
    names = ("func", "params", "metadata", "inject_ctx", "result_to")
    return (
        (getattr(value, name) for name in names),
        TreeAux(children_keys=tuple(AttributeKey(name) for name in names)),
    )


NODE_INSPECTION_TREE_RULES = TreeRules(
    {
        Node: TreeHandler(_flatten, None),
        Auto: TreeHandler(
            lambda obj: ((obj.value,), TreeAux(children_keys=(AttributeKey("value"),))),
            None,
        ),
    }
)
