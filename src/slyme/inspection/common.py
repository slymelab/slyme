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

"""Optional value retention and Tree key descriptions for inspection."""

from __future__ import annotations

from slyme.tree import (
    AttributeKey,
    MappingKey,
    SequenceKey,
    TreeAux,
    TreeHandler,
    TreeRules,
)

SCALAR_TYPES: frozenset[type] = frozenset((type(None), bool, int, float, str))

KEY_INSPECTION_TREE_RULES = TreeRules(
    {
        AttributeKey: TreeHandler(lambda key: ((key.name,), TreeAux()), None),
        MappingKey: TreeHandler(lambda key: ((key.key,), TreeAux()), None),
        SequenceKey: TreeHandler(lambda key: ((key.index,), TreeAux()), None),
    }
)

KEY_INSPECTION_TYPE_NAMES: dict[type, str] = {
    AttributeKey: "slyme.tree.attribute_key",
    MappingKey: "slyme.tree.mapping_key",
    SequenceKey: "slyme.tree.sequence_key",
}
