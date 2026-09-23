"""Context facets infer independently of parent types and default to Any."""

from __future__ import annotations

from typing import Any

from typing_extensions import assert_type

from slyme.context import Context


class Plugin:
    def __init__(self, ctx: Context[Plugin]) -> None:
        self.ctx = ctx


class Job:
    def __init__(self, ctx: Context[Job]) -> None:
        self.ctx = ctx


def check_types(ctx: Context) -> None:
    assert_type(ctx, Context[Any])
    assert_type(ctx.facet, Any)
    assert_type(Context(), Context[Any])
    assert_type(Context[None]().facet, None)
    assert_type(Context(facet_factory=None), Context[Any])
    assert_type(Context(facet_factory=Plugin), Context[Plugin])
    assert_type(Context[Plugin](facet_factory=Plugin).facet, Plugin)
    plugin_ctx = ctx.fork(facet_factory=Plugin)
    assert_type(plugin_ctx, Context[Plugin])
    assert_type(plugin_ctx.facet, Plugin)
    assert_type(plugin_ctx.facet.ctx, Context[Plugin])
    assert_type(plugin_ctx.parent, Context[Any] | None)
    assert_type(plugin_ctx.root, Context[Any])
    assert_type(plugin_ctx.children, tuple[Context[Any], ...])
    assert_type(plugin_ctx.fork(), Context[Any])
    assert_type(plugin_ctx.fork(facet_factory=None), Context[Any])
    assert_type(plugin_ctx.fork(facet_factory=Job), Context[Job])
    assert_type(plugin_ctx.derive(), Context[Any])
    assert_type(plugin_ctx.derive(facet_factory=None), Context[Any])
    assert_type(plugin_ctx.derive(facet_factory=Job), Context[Job])
    assert_type(ctx.derive(bindings={}, facet_factory=Plugin).facet, Plugin)
    assert_type(ctx.fork(facet_factory=lambda child: "value"), Context[str])
    assert_type(ctx.derive(facet_factory=lambda child: None), Context[None])
    generic: Context[object] = plugin_ctx
    assert_type(generic.facet, object)
    plugin_ctx.facet = Plugin(plugin_ctx)  # type: ignore[misc]
    Context[int](facet_factory=Plugin)  # type: ignore[arg-type]
