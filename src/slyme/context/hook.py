from dataclasses import dataclass
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Context, Ref


@dataclass(frozen=True)
class Hook:
    def on_get(self, *, ctx: "Context", ref: "Ref", value: Any, **kwargs) -> Any:
        return value

    async def on_get_async(
        self, *, ctx: "Context", ref: "Ref", value: Any, **kwargs
    ) -> Any:
        return self.on_get(ctx=ctx, ref=ref, value=value, **kwargs)

    def on_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> tuple[dict["Ref", Any], set["Ref"]]:
        return updates, drops

    async def on_mutate_async(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> tuple[dict["Ref", Any], set["Ref"]]:
        return self.on_mutate(ctx=ctx, updates=updates, drops=drops, **kwargs)

    def on_to_dict(
        self, *, ctx: "Context", ref: Optional["Ref"], value: dict, **kwargs
    ) -> dict:
        return value

    async def on_to_dict_async(
        self, *, ctx: "Context", ref: Optional["Ref"], value: dict, **kwargs
    ) -> dict:
        return self.on_to_dict(ctx=ctx, ref=ref, value=value, **kwargs)


@dataclass(frozen=True)
class HookChain(Hook):
    hooks: tuple[Hook, ...]

    def on_get(self, *, ctx: "Context", ref: "Ref", value: Any, **kwargs) -> Any:
        for hook in self.hooks:
            value = hook.on_get(ctx=ctx, ref=ref, value=value, **kwargs)
        return value

    async def on_get_async(
        self, *, ctx: "Context", ref: "Ref", value: Any, **kwargs
    ) -> Any:
        for hook in self.hooks:
            value = await hook.on_get_async(ctx=ctx, ref=ref, value=value, **kwargs)
        return value

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

    def on_to_dict(
        self, *, ctx: "Context", ref: Optional["Ref"], value: dict, **kwargs
    ) -> dict:
        for hook in self.hooks:
            value = hook.on_to_dict(ctx=ctx, ref=ref, value=value, **kwargs)
        return value

    async def on_to_dict_async(
        self, *, ctx: "Context", ref: Optional["Ref"], value: dict, **kwargs
    ) -> dict:
        for hook in self.hooks:
            value = await hook.on_to_dict_async(ctx=ctx, ref=ref, value=value, **kwargs)
        return value
