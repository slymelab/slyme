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

"""
slyme tree utility module.
"""

from __future__ import annotations

from .core import (
    AttributeKey,
    FlattenFunc,
    KeyPath,
    MappingKey,
    SequenceKey,
    TraverseAux,
    TreeAux,
    TreeDef,
    TreeHandler,
    TreeKey,
    TreeResolver,
    TreeRules,
    UnflattenFunc,
    codify_key_path,
    flatten,
    flatten_with_key_path,
    get_element,
    iter,
    iter_with_key_path,
    map,
)

__all__ = [
    "KeyPath",
    "TreeKey",
    "SequenceKey",
    "MappingKey",
    "AttributeKey",
    "TreeAux",
    "TreeDef",
    "TraverseAux",
    "TreeHandler",
    "TreeRules",
    "TreeResolver",
    "FlattenFunc",
    "UnflattenFunc",
    "flatten",
    "flatten_with_key_path",
    "iter",
    "iter_with_key_path",
    "map",
    "get_element",
    "codify_key_path",
]
