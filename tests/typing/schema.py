"""Schema declarations use dict trees or another Schema, not arbitrary mappings."""

from collections.abc import Hashable, Mapping
from typing import Any

from typing_extensions import assert_type

from slyme.context import (
    Context,
    Metadata,
    Ref,
    RefConfig,
    RefContainerConfig,
    RefEntry,
    RefLeafConfig,
    Schema,
)
from slyme.context.schema import _Declaration


def check_types(ctx: Context, schema: Schema, mapping: Mapping[str, Any]) -> None:
    assert_type(schema.entries, tuple[RefEntry[Any], ...])
    assert_type(schema.resolve_entry("value"), RefEntry[Any])
    assert_type(schema.resolve_entry("value").config.metadata, Mapping[str, Metadata])
    assert_type(Schema.leaf(int), RefLeafConfig[int])
    assert_type(Schema.container(), RefContainerConfig)
    marker = Metadata()
    annotated = Schema.leaf(int, metadata={"app.marker": marker})
    assert_type(annotated, RefLeafConfig[int])
    assert_type(annotated.metadata, Mapping[str, Metadata])
    assert_type(marker.merge(marker), Metadata)
    Schema.container(metadata={"app.marker": marker})
    Schema.leaf(metadata={"invalid": "text"})  # type: ignore[dict-item]
    config = Schema.leaf(int)
    assert_type(config.merge(config), RefLeafConfig[int])
    entry = RefEntry(Ref[int]("value"))
    entry._declare(("plugin", 1), config)
    assert_type(entry.config, RefConfig[int])
    assert_type(entry._config, RefConfig[int] | None)
    assert_type(entry._declarations, dict[Hashable, RefConfig[int]])
    assert_type(entry.alive, bool)
    entry._undeclare(("plugin", 1))
    declaration: _Declaration = {
        "group": {"": Schema.container(), "value": Schema.leaf(int)},
        "empty": {},
    }
    Schema(declaration=declaration)
    Schema(schema)
    schema.declare(declaration=declaration)
    ctx.declare(declaration=declaration)
    ctx.declare(schema)
    Schema(mapping)  # type: ignore[arg-type]
    schema.declare(mapping)  # type: ignore[arg-type]
    ctx.declare(mapping)  # type: ignore[arg-type]
