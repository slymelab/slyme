from dataclasses import dataclass
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Context, Ref

__all__ = [
    "ExtractResult",
    "MutateResult",
    "Hook",
    "HookChain",
]


@dataclass(frozen=True)
class ExtractResult:
    values: tuple[Any, ...]


@dataclass(frozen=True)
class MutateResult:
    updates: dict["Ref", Any]
    drops: set["Ref"]


@dataclass(frozen=True)
class Hook:
    def on_extract(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> ExtractResult:
        return ExtractResult(values=values)

    async def on_extract_async(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> ExtractResult:
        return self.on_extract(ctx=ctx, refs=refs, values=values, **kwargs)

    def on_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        return MutateResult(updates=updates, drops=drops)

    async def on_mutate_async(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        return self.on_mutate(ctx=ctx, updates=updates, drops=drops, **kwargs)


@dataclass(frozen=True)
class HookChain(Hook):
    hooks: tuple[Hook, ...]

    def on_extract(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> ExtractResult:
        for hook in self.hooks:
            result = hook.on_extract(ctx=ctx, refs=refs, values=values, **kwargs)
            values = result.values
        return ExtractResult(values=values)

    async def on_extract_async(
        self, *, ctx: "Context", refs: tuple["Ref", ...], values: tuple[Any, ...], **kwargs
    ) -> ExtractResult:
        for hook in self.hooks:
            result = await hook.on_extract_async(
                ctx=ctx, refs=refs, values=values, **kwargs
            )
            values = result.values
        return ExtractResult(values=values)

    def on_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        for hook in self.hooks:
            result = hook.on_mutate(
                ctx=ctx, updates=updates, drops=drops, **kwargs
            )
            updates, drops = result.updates, result.drops
        return MutateResult(updates=updates, drops=drops)

    async def on_mutate_async(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        for hook in self.hooks:
            result = await hook.on_mutate_async(
                ctx=ctx, updates=updates, drops=drops, **kwargs
            )
            updates, drops = result.updates, result.drops
        return MutateResult(updates=updates, drops=drops)
