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
import weakref
from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, Literal, TypeVar

from ..utils.retainer import Retainer
from .scope import Scope

__all__ = ["Compose"]

_T = TypeVar("_T")
_R = TypeVar("_R")
_K = TypeVar("_K", bound=Hashable)
_V = TypeVar("_V")


@dataclass(frozen=True)
class _ComposeEntry(Generic[_T]):
    token: object
    scope: Scope
    identity: Hashable
    value: _T
    metadata: Mapping[str, Any]
    release: Callable[[], None] = field(init=False, repr=False, compare=False)


@dataclass(frozen=True)
class _ComposeBucket(Generic[_T]):
    entries: OrderedDict[object, _ComposeEntry[_T]]
    retainer: Retainer[[_ComposeEntry[_T], Literal["prepend", "append"]]]


class Compose(Generic[_T, _R]):
    """Store reversible values and combine those visible through Scope C3 order."""

    __slots__ = ("_buckets", "_resolver", "_scope_identities", "__weakref__")

    def __init__(self, resolver: Callable[[tuple[_T, ...]], _R]) -> None:
        self._resolver = resolver
        self._scope_identities: weakref.WeakKeyDictionary[Scope, Hashable] = (
            weakref.WeakKeyDictionary()
        )
        self._buckets: dict[Hashable, _ComposeBucket[_T]] = {}

    def _create_bucket(self, identity: Hashable) -> _ComposeBucket[_T]:
        entries: OrderedDict[object, _ComposeEntry[_T]] = OrderedDict()
        compose_ref = weakref.ref(self)

        def acquire(
            entry: _ComposeEntry[_T], position: Literal["prepend", "append"]
        ) -> Callable[[], None]:
            entries[entry.token] = entry
            if position == "prepend":
                entries.move_to_end(entry.token, last=False)

            def undo() -> None:
                # Keep the value alive until Retainer leaves its cleanup callback.
                entries.pop(entry.token)

            return undo

        def cleanup() -> None:
            compose = compose_ref()
            if compose is not None:
                del compose._buckets[identity]

        return _ComposeBucket(
            entries,
            Retainer(acquire, should_cleanup=lambda: not entries, cleanup=cleanup),
        )

    @staticmethod
    def _validate_identity(identity: Hashable) -> Hashable:
        try:
            hash(identity)
        except TypeError as error:
            raise TypeError("Compose identities must be hashable.") from error
        return identity

    def bind(self, *scopes: Scope, identity: Hashable) -> None:
        """Bind Scopes once to one Compose-local storage identity."""
        if not scopes:
            raise ValueError("Compose.bind() requires at least one Scope.")
        checked_identity = self._validate_identity(identity)

        conflicts = tuple(
            scope
            for scope in scopes
            if scope in self._scope_identities
            and self._scope_identities[scope] != checked_identity
        )
        if conflicts:
            raise ValueError("A Scope cannot be rebound to another Compose identity.")

        for scope in scopes:
            if scope not in self._scope_identities:
                self._scope_identities[scope] = checked_identity

    def _identity_for(self, scope: Scope, *, create: bool) -> Hashable:
        try:
            return self._scope_identities[scope]
        except KeyError:
            if not create:
                raise LookupError("Scope is not bound to this Compose.") from None
        identity = object()
        self._scope_identities[scope] = identity
        return identity

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
    ) -> tuple[_ComposeEntry[_T], ...]:
        scopes = (scope,) if local else scope.mro
        identities: list[Hashable] = []
        seen: set[Hashable] = set()
        for current in scopes:
            try:
                identity = self._identity_for(current, create=False)
            except LookupError:
                continue
            if identity not in seen:
                seen.add(identity)
                identities.append(identity)
        return tuple(
            entry
            for identity in identities
            if (bucket := self._buckets.get(identity)) is not None
            for entry in bucket.entries.values()
        )

    def add(
        self,
        scope: Scope,
        value: _T,
        *,
        metadata: Mapping[str, Any] | None = None,
        position: Literal["prepend", "append"] = "append",
    ) -> Callable[[], None]:
        """Add a value with exact disposal; the final release removes its bucket.

        The disposer retains this Compose until called and reproduces a failed
        release on subsequent calls without repeating its cleanup.
        """
        entry = self._insert(
            scope,
            value,
            metadata=metadata,
            position=position,
        )
        return self._disposer(entry)

    def _insert(
        self,
        scope: Scope,
        value: _T,
        *,
        metadata: Mapping[str, Any] | None = None,
        position: Literal["prepend", "append"] = "append",
    ) -> _ComposeEntry[_T]:
        identity = self._identity_for(scope, create=True)
        token = object()
        entry = _ComposeEntry(
            token,
            scope,
            identity,
            value,
            types.MappingProxyType(dict(metadata or {})),
        )
        bucket = self._buckets.get(identity)
        if bucket is None:
            bucket = self._create_bucket(identity)
            self._buckets[identity] = bucket
        object.__setattr__(entry, "release", bucket.retainer.acquire(entry, position))
        return entry

    def _remove(self, identity: Hashable, token: object) -> None:
        current = self._buckets.get(identity)
        if current is None:
            return
        entry = current.entries.get(token)
        if entry is not None:
            entry.release()

    def _clear_bucket(self, identity: Hashable) -> None:
        bucket = self._buckets.get(identity)
        if bucket is None:
            return
        first_error: BaseException | None = None
        for entry in tuple(bucket.entries.values()):
            try:
                entry.release()
            except BaseException as error:
                if first_error is None:
                    first_error = error
        if first_error is not None:
            raise first_error

    def _disposer(
        self,
        entry: _ComposeEntry[_T],
    ) -> Callable[[], None]:
        compose: Compose[_T, _R] | None = self
        release = entry.release

        def dispose() -> None:
            nonlocal compose
            try:
                release()
            finally:
                compose = None

        return dispose

    def values(self, scope: Scope, *, local: bool = False) -> tuple[_T, ...]:
        """Return values from most-specific to least-specific Scope."""
        return tuple(entry.value for entry in self._scoped_entries(scope, local=local))

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
                entry
                for bucket in tuple(self._buckets.values())
                for entry in bucket.entries.values()
            )
        else:
            scoped = self._scoped_entries(scope, local=local)

        return tuple(
            types.MappingProxyType(
                {
                    "id": entry.token,
                    "scope": entry.scope,
                    "identity": entry.identity,
                    "value": entry.value,
                    "metadata": entry.metadata,
                }
            )
            for entry in scoped
        )

    def __len__(self) -> int:
        return sum(len(bucket.entries) for bucket in self._buckets.values())
