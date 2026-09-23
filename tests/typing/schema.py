"""Schema declarations use dict trees or another Schema, not arbitrary mappings."""

from collections.abc import Hashable, Iterable, Mapping
from typing import Any

from tests.compose_helpers import ValueLayer, collect_values
from typing_extensions import assert_type

from slyme.context import (
    Compose,
    Context,
    Identity,
    Metadata,
    Ref,
    RefConfig,
    RefContainerConfig,
    RefEntry,
    RefLeafConfig,
    Schema,
    Scope,
    ScopeBinding,
)
from slyme.context.core import ContextView
from slyme.context.schema import _Declaration


def check_types(ctx: Context, schema: Schema, mapping: Mapping[str, Any]) -> None:
    Context(parent=ctx, scope=ctx.scope)
    Context(data={})  # type: ignore[call-overload]
    Context(schema=schema)  # type: ignore[call-overload]
    assert_type(ctx.entries, tuple[RefEntry[Any], ...])
    assert_type(ctx.resolve("value"), Ref[Any])
    assert_type(ctx.resolve(Ref("value"), role="leaf"), Ref[Any])
    assert_type(ctx.resolve("", role="container"), Ref[Any])
    assert_type(ctx.flatten(), dict[Ref[Any], Any])
    assert_type(ctx.flatten("group"), dict[Ref[Any], Any])
    assert_type(ctx.flatten(Ref("group"), local=True), dict[Ref[Any], Any])
    identity = Identity("shared", blocked=True)
    values: Compose[ValueLayer[str], tuple[str, ...]] = Compose(
        factory=ValueLayer[str], query=collect_values
    )
    assert_type(ctx.fork(), Context)
    assert_type(ctx.fork(scope=ctx.scope), Context)
    assert_type(ctx.derive(), Context)
    assert_type(ctx.derive(bindings=None), Context)
    assert_type(ctx.derive(bindings={}), Context)
    assert_type(ctx.derive(parents=ctx.scope, label="child"), Context)
    assert_type(
        ctx.derive(
            bindings={"value": ScopeBinding(), Ref("service"): ScopeBinding(identity)}
        ),
        Context,
    )
    assert_type(ctx.derive(bindings={values: ScopeBinding(blocked=False)}), Context)
    assert_type(ctx.derive(label="child", parents=ctx.scope, bindings={}), Context)
    assert_type(ctx.derive(parents=(ctx.scope,), bindings={}), Context)
    assert_type(ctx.derive(parents=(), bindings={}), Context)
    assert_type(values.derive(parents=ctx.scope, binding=ScopeBinding()), Scope)
    assert_type(values.derive(parents=ctx.scope, binding=ScopeBinding(identity)), Scope)
    assert_type(
        values.derive(label="child", parents=(ctx.scope,), binding=ScopeBinding()),
        Scope,
    )
    assert_type(values.derive(parents=(), binding=ScopeBinding()), Scope)
    assert_type(
        Compose.derive_many(
            parents=ctx.scope, bindings={values: ScopeBinding(identity)}
        ),
        Scope,
    )
    assert_type(Compose.derive_many(label="root", parents=(), bindings={}), Scope)
    ctx.fork(bindings={})  # type: ignore[call-overload]
    ctx.derive(bindings={}, scope=ctx.scope)  # type: ignore[call-overload]
    ctx.derive({})  # type: ignore[call-overload]
    values.derive()  # type: ignore[call-arg]
    values.derive(ctx.scope)  # type: ignore[misc, call-arg]
    Compose.derive_many(bindings={})  # type: ignore[call-arg]
    values.derive(parents=ctx.scope)  # type: ignore[call-arg]
    assert_type(values.derive(parents=ctx.scope, binding=identity), Scope)
    values.derive(parents=ctx.scope, binding=None)  # type: ignore[arg-type]
    assert_type(ctx.derive(bindings={"value": identity, values: identity}), Context)
    ctx.derive(bindings={"value": None})  # type: ignore[dict-item]
    assert_type(
        Compose.derive_many(parents=ctx.scope, bindings={values: identity}), Scope
    )
    Compose.derive_many(parents=ctx.scope, bindings={values: None})  # type: ignore[dict-item]
    assert_type(ctx.resolve_entry("value"), RefEntry[Any])
    assert_type(ctx.resolve_entry(Ref("value"), role=None), RefEntry[Any])
    assert_type(schema.entries, tuple[RefEntry[Any], ...])
    assert_type(schema.resolve(Ref("value"), role="leaf"), Ref[Any])
    assert_type(schema.resolve_entry("value"), RefEntry[Any])
    assert_type(schema.resolve_entry(Ref("value"), role=None), RefEntry[Any])
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


def check_view_types(view: ContextView, ref: Ref[Any]) -> None:
    assert_type(view.get("value"), Any)
    assert_type(view.exists("value"), bool)
    assert_type(view.keys(""), Iterable[str])
    assert_type(view.to_dict(""), dict[str, Any])
    assert_type(view.flatten(), dict[Ref[Any], Any])
    view.get(ref)  # type: ignore[arg-type]
    view.exists(ref)  # type: ignore[arg-type]
    view.keys(ref)  # type: ignore[arg-type]
    view.to_dict(ref)  # type: ignore[arg-type]
