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

"""Scope-aware reversible composition."""

from __future__ import annotations

import types
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from .scope import Scope

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
    """Store reversible values and combine those visible through Scope C3 order."""

    __slots__ = ("_buckets", "_resolver", "__weakref__")

    def __init__(self, resolver: Callable[[tuple[_T, ...]], _R]) -> None:
        if not callable(resolver):
            raise TypeError("Compose resolver must be callable.")
        self._resolver = resolver
        self._buckets: dict[Scope, dict[object, _ComposeEntry[_T]]] = {}

    @staticmethod
    def _validate_scope(scope: Scope) -> Scope:
        if not isinstance(scope, Scope):
            raise TypeError(f"Compose scope must be Scope, got {type(scope).__name__}.")
        return scope

    @classmethod
    def one(cls) -> Compose[_T, _T]:
        """Create a composition that selects the first visible value."""

        def resolve(values: tuple[_T, ...]) -> _T:
            if not values:
                raise LookupError("Compose has no value visible from this Scope.")
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
        scope: Scope,
        *,
        local: bool,
    ) -> tuple[tuple[Scope, _ComposeEntry[_T]], ...]:
        scope = self._validate_scope(scope)
        scopes = (scope,) if local else scope.mro
        return tuple(
            (current, entry)
            for current in scopes
            for entry in self._buckets.get(current, {}).values()
        )

    def add(
        self,
        scope: Scope,
        value: _T,
        *,
        metadata: Mapping[str, Any] | None = None,
        position: Literal["prepend", "append"] = "append",
    ) -> Callable[[], None]:
        """Add one Scope-local value and return an idempotent exact disposer."""
        entry = self._insert(
            scope,
            value,
            metadata=metadata,
            position=position,
        )
        return self._disposer(scope, entry)

    def _insert(
        self,
        scope: Scope,
        value: _T,
        *,
        metadata: Mapping[str, Any] | None = None,
        position: Literal["prepend", "append"] = "append",
    ) -> _ComposeEntry[_T]:
        scope = self._validate_scope(scope)
        if position not in ("prepend", "append"):
            raise ValueError(f"Unknown Compose position: {position!r}.")

        identity = object()
        entry = _ComposeEntry(
            identity,
            value,
            types.MappingProxyType(dict(metadata or {})),
        )
        bucket = self._buckets.setdefault(scope, {})
        if position == "append":
            bucket[identity] = entry
        else:
            self._buckets[scope] = {identity: entry, **bucket}
        return entry

    def _remove(self, scope: Scope, identity: object) -> None:
        current = self._buckets.get(scope)
        if current is None or identity not in current:
            return
        current.pop(identity)
        if not current:
            self._buckets.pop(scope, None)

    def _disposer(
        self,
        scope: Scope,
        entry: _ComposeEntry[_T],
    ) -> Callable[[], None]:
        compose: Compose[_T, _R] | None = self
        target: Scope | None = scope
        identity = entry.identity

        def dispose() -> None:
            nonlocal compose, target
            if compose is None or target is None:
                return
            compose._remove(target, identity)
            compose = None
            target = None

        return dispose

    def values(self, scope: Scope, *, local: bool = False) -> tuple[_T, ...]:
        """Return values from most-specific to least-specific Scope."""
        return tuple(
            entry.value for _, entry in self._scoped_entries(scope, local=local)
        )

    def resolve(self, scope: Scope, *, local: bool = False) -> _R:
        """Resolve values visible from a Scope."""
        return self._resolver(self.values(scope, local=local))

    def entries(
        self,
        scope: Scope | None = None,
        *,
        local: bool = False,
    ) -> tuple[Mapping[str, Any], ...]:
        """Return immutable entry snapshots for introspection."""
        if scope is None:
            if local:
                raise ValueError("local=True requires a Scope.")
            scoped = tuple(
                (current, entry)
                for current, bucket in tuple(self._buckets.items())
                for entry in bucket.values()
            )
        else:
            scope = self._validate_scope(scope)
            scoped = self._scoped_entries(scope, local=local)

        return tuple(
            types.MappingProxyType(
                {
                    "id": entry.identity,
                    "scope": current,
                    "value": entry.value,
                    "metadata": entry.metadata,
                }
            )
            for current, entry in scoped
        )

    def __len__(self) -> int:
        return sum(len(bucket) for bucket in self._buckets.values())
