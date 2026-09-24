"""Context facet inference and extension callback signatures."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from typing_extensions import assert_type

from slyme.context import Context, Scope


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
    assert_type(Context(dispose_mode="sequential"), Context[Any])
    assert_type(
        Context(parent=None, scope=Scope(), facet_factory=Plugin), Context[Plugin]
    )
    assert_type(Context(parent=ctx, dispose_mode="batch"), Context[Any])
    assert_type(
        Context(parent=ctx, dispose_mode="batch", facet_factory=Plugin), Context[Plugin]
    )
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
    Context(dispose_mode="batch")  # type: ignore[call-overload]
    Context(parent=None, dispose_mode="batch")  # type: ignore[call-overload]
    Context(scope=Scope(), dispose_mode="batch")  # type: ignore[call-overload]


def check_installed_methods(ctx: Context) -> None:
    def describe(current: Context, /, value: int, *, suffix: str = "") -> str:
        return str(value) + suffix

    async def identify(current: Context, /) -> Context:
        return current

    def wrong_receiver(current: str, /) -> str:
        return current

    assert_type(ctx.install("describe", describe), Callable[[], None | Awaitable[None]])
    assert_type(ctx.install("identify", identify), Callable[[], None | Awaitable[None]])
    assert_type(ctx.describe(1, suffix="!"), Any)
    ctx.install("wrong", wrong_receiver)  # type: ignore[arg-type]
