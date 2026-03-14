from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Context, Ref


@dataclass(frozen=True)
class Hook:
    def on_extract(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> tuple[Any, ...]:
        return values

    async def on_extract_async(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> tuple[Any, ...]:
        return self.on_extract(ctx=ctx, refs=refs, values=values, **kwargs)

    def on_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> tuple[dict["Ref", Any], set["Ref"]]:
        return updates, drops

    async def on_mutate_async(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> tuple[dict["Ref", Any], set["Ref"]]:
        return self.on_mutate(ctx=ctx, updates=updates, drops=drops, **kwargs)


@dataclass(frozen=True)
class HookChain(Hook):
    hooks: tuple[Hook, ...]

    def on_extract(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> tuple[Any, ...]:
        for hook in self.hooks:
            values = hook.on_extract(ctx=ctx, refs=refs, values=values, **kwargs)
        return values

    async def on_extract_async(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> tuple[Any, ...]:
        for hook in self.hooks:
            values = await hook.on_extract_async(
                ctx=ctx, refs=refs, values=values, **kwargs
            )
        return values

    def on_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> tuple[dict["Ref", Any], set["Ref"]]:
        for hook in self.hooks:
            updates, drops = hook.on_mutate(
                ctx=ctx, updates=updates, drops=drops, **kwargs
            )
        return updates, drops

    async def on_mutate_async(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> tuple[dict["Ref", Any], set["Ref"]]:
        for hook in self.hooks:
            updates, drops = await hook.on_mutate_async(
                ctx=ctx, updates=updates, drops=drops, **kwargs
            )
        return updates, drops
