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
    """.. deprecated:: 0.1.1
        Will be removed in 0.2.0.  Context no longer supports hooks.
    """

    def __post_init__(self):
        from slyme.utils.warning import warning_once

        warning_once(
            "Hook is deprecated and will be removed in slyme 0.2.0. "
            "Context no longer supports hooks.",
            FutureWarning,
            2,
        )

    def on_extract(
        self,
        *,
        ctx: "Context",
        refs: tuple["Ref", ...],
        values: tuple[Any, ...],
        **kwargs,
    ) -> ExtractResult:
        return ExtractResult(values=values)

    async def on_async_extract(
        self,
        *,
        ctx: "Context",
        refs: tuple["Ref", ...],
        values: tuple[Any, ...],
        **kwargs,
    ) -> ExtractResult:
        return self.on_extract(ctx=ctx, refs=refs, values=values, **kwargs)

    def on_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        return MutateResult(updates=updates, drops=drops)

    async def on_async_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        return self.on_mutate(ctx=ctx, updates=updates, drops=drops, **kwargs)


@dataclass(frozen=True)
class HookChain(Hook):
    """.. deprecated:: 0.1.1
        Will be removed in 0.2.0.  Context no longer supports hooks.
    """

    hooks: tuple[Hook, ...]

    def __post_init__(self):
        from slyme.utils.warning import warning_once

        warning_once(
            "HookChain is deprecated and will be removed in slyme 0.2.0. "
            "Context no longer supports hooks.",
            FutureWarning,
            2,
        )

    def on_extract(
        self,
        *,
        ctx: "Context",
        refs: tuple["Ref", ...],
        values: tuple[Any, ...],
        **kwargs,
    ) -> ExtractResult:
        for hook in self.hooks:
            result = hook.on_extract(ctx=ctx, refs=refs, values=values, **kwargs)
            values = result.values
        return ExtractResult(values=values)

    async def on_async_extract(
        self,
        *,
        ctx: "Context",
        refs: tuple["Ref", ...],
        values: tuple[Any, ...],
        **kwargs,
    ) -> ExtractResult:
        for hook in self.hooks:
            result = await hook.on_async_extract(
                ctx=ctx, refs=refs, values=values, **kwargs
            )
            values = result.values
        return ExtractResult(values=values)

    def on_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        for hook in self.hooks:
            result = hook.on_mutate(ctx=ctx, updates=updates, drops=drops, **kwargs)
            updates, drops = result.updates, result.drops
        return MutateResult(updates=updates, drops=drops)

    async def on_async_mutate(
        self, *, ctx: "Context", updates: dict["Ref", Any], drops: set["Ref"], **kwargs
    ) -> MutateResult:
        for hook in self.hooks:
            result = await hook.on_async_mutate(
                ctx=ctx, updates=updates, drops=drops, **kwargs
            )
            updates, drops = result.updates, result.drops
        return MutateResult(updates=updates, drops=drops)
