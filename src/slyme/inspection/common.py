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
