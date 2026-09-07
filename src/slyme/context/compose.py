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

"""Context-aware reversible composition."""

from __future__ import annotations

import types
import weakref
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar

if TYPE_CHECKING:
    from .core import Context

__all__ = ["Compose"]

_T = TypeVar("_T")
_R = TypeVar("_R")
_K = TypeVar("_K", bound=Hashable)
_V = TypeVar("_V")


@dataclass(frozen=True)
class _ComposeEntry(Generic[_T]):
    identity: object
    value: _T
    metadata: Mapping[str, Any]


class Compose(Generic[_T, _R]):
    """Store reversible values and combine those visible through Context C3 order."""

    __slots__ = ("_buckets", "_resolver", "__weakref__")

    def __init__(self, resolver: Callable[[tuple[_T, ...]], _R]) -> None:
        self._resolver = resolver
        self._buckets: weakref.WeakKeyDictionary[
            Context, dict[object, _ComposeEntry[_T]]
        ] = weakref.WeakKeyDictionary()

    @classmethod
    def one(cls) -> Compose[_T, _T]:
        """Create a composition that selects the first visible value."""

        def resolve(values: tuple[_T, ...]) -> _T:
            if not values:
                raise LookupError("Compose has no value visible from this Context.")
            return values[0]

        return Compose(resolve)

    @classmethod
    def collect(cls) -> Compose[_T, tuple[_T, ...]]:
        """Create a composition that returns every visible value in order."""
        return Compose(lambda values: values)

    @classmethod
    def merge(cls) -> Compose[Mapping[_K, _V], dict[_K, _V]]:
        """Create a composition that merges mappings with first value precedence."""

        def resolve(values: tuple[Mapping[_K, _V], ...]) -> dict[_K, _V]:
            result: dict[_K, _V] = {}
            for value in values:
                for key, item in value.items():
                    if key not in result:
                        result[key] = item
            return result

        return Compose(resolve)

    def _scoped_entries(
        self,
        ctx: Context,
        *,
        local: bool,
    ) -> tuple[tuple[Context, _ComposeEntry[_T]], ...]:
        contexts = (ctx,) if local else ctx.mro
        return tuple(
            (context, entry)
            for context in contexts
            for entry in self._buckets.get(context, {}).values()
        )

    def add(
        self,
        ctx: Context,
        value: _T,
        *,
        metadata: Mapping[str, Any] | None = None,
        position: Literal["prepend", "append"] = "append",
    ) -> Callable[[], None]:
        """Add one Context-local value and return an idempotent exact disposer."""
        entry = self._insert(
            ctx,
            value,
            metadata=metadata,
            position=position,
        )
        return self._disposer(ctx, entry)

    def _insert(
        self,
        ctx: Context,
        value: _T,
        *,
        metadata: Mapping[str, Any] | None = None,
        position: Literal["prepend", "append"] = "append",
    ) -> _ComposeEntry[_T]:
        if position not in ("prepend", "append"):
            raise ValueError(f"Unknown Compose position: {position!r}.")

        identity = object()
        entry = _ComposeEntry(
            identity,
            value,
            types.MappingProxyType(dict(metadata or {})),
        )
        bucket = self._buckets.setdefault(ctx, {})
        if position == "append":
            bucket[identity] = entry
        else:
            self._buckets[ctx] = {identity: entry, **bucket}
        return entry

    def _remove(self, ctx: Context, expected: _ComposeEntry[_T]) -> None:
        current = self._buckets.get(ctx)
        if current is None or current.get(expected.identity) is not expected:
            return
        current.pop(expected.identity)
        if not current:
            self._buckets.pop(ctx, None)

    def _disposer(
        self,
        ctx: Context,
        entry: _ComposeEntry[_T],
    ) -> Callable[[], None]:

        compose_ref = weakref.ref(self)
        context_ref = weakref.ref(ctx)
        entry_ref = weakref.ref(entry)

        def dispose() -> None:
            compose = compose_ref()
            context = context_ref()
            expected = entry_ref()
            if compose is None or context is None or expected is None:
                return
            compose._remove(context, expected)

        return dispose

    def values(self, ctx: Context, *, local: bool = False) -> tuple[_T, ...]:
        """Return values from most-specific to least-specific Context."""
        return tuple(entry.value for _, entry in self._scoped_entries(ctx, local=local))

    def resolve(self, ctx: Context, *, local: bool = False) -> _R:
        """Resolve values visible from a context."""
        return self._resolver(self.values(ctx, local=local))

    def entries(
        self,
        ctx: Context | None = None,
        *,
        local: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return immutable entry snapshots for introspection."""
        if ctx is None:
            if local:
                raise ValueError("local=True requires a Context.")
            scoped = tuple(
                (context, entry)
                for context, bucket in tuple(self._buckets.items())
                for entry in bucket.values()
            )
        else:
            scoped = self._scoped_entries(ctx, local=local)

        return tuple(
            types.MappingProxyType(
                {
                    "id": entry.identity,
                    "context": context,
                    "value": entry.value,
                    "metadata": entry.metadata,
                }
            )
            for context, entry in scoped
        )

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._buckets.values())
