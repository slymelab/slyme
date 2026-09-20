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
from dataclasses import dataclass
from typing import Any, Generic, Literal, TypeVar

from .scope import Identity, Scope, ScopeBinding

__all__ = ["Compose"]

_T = TypeVar("_T")
_R = TypeVar("_R")
_K = TypeVar("_K", bound=Hashable)
_V = TypeVar("_V")


@dataclass(frozen=True)
class _ComposeEntry(Generic[_T]):
    token: object
    scope: Scope
    identity: Identity
    value: _T
    metadata: Mapping[str, Any]


class Compose(Generic[_T, _R]):
    """Store reversible values and combine those visible through Scope C3 order."""

    __slots__ = ("_buckets", "_resolver", "_scope_bindings", "__weakref__")

    def __init__(self, resolver: Callable[[tuple[_T, ...]], _R]) -> None:
        self._resolver = resolver
        self._scope_bindings: weakref.WeakKeyDictionary[Scope, ScopeBinding] = (
            weakref.WeakKeyDictionary()
        )
        self._buckets: dict[Identity, OrderedDict[object, _ComposeEntry[_T]]] = {}

    def derive(
        self,
        *,
        label: Any | None = None,
        parents: Scope | tuple[Scope, ...],
        binding: ScopeBinding | Identity,
    ) -> Scope:
        """Create a Scope configured for this Compose, leaving its parents unchanged.

        ScopeBinding() selects private storage with ancestor fallback. Share
        storage by supplying the same Identity directly or inside ScopeBinding.
        A direct Identity uses the default unblocked Scope-local policy.
        No Context or lifecycle is created; contributions require disposer ownership.
        """
        return self.derive_many(label=label, parents=parents, bindings={self: binding})

    @staticmethod
    def derive_many(
        *,
        label: Any | None = None,
        parents: Scope | tuple[Scope, ...],
        bindings: Mapping[Compose[Any, Any], ScopeBinding | Identity],
    ) -> Scope:
        """Configure all Composes on one new Scope, without lifecycle ownership.

        An empty parent tuple creates an independent Scope. Binding values follow
        derive() semantics: an Identity or an explicit ScopeBinding.
        """
        scope = Scope(label=label, parents=parents)
        for compose, binding in bindings.items():
            compose._bind(
                scope,
                ScopeBinding(binding) if isinstance(binding, Identity) else binding,
            )
        return scope

    def _bind(self, scope: Scope, binding: ScopeBinding) -> None:
        previous = self._scope_bindings.get(scope)
        if previous is None:
            self._scope_bindings[scope] = binding
        elif previous != binding:
            raise ValueError(
                "A Scope binding's identity and blocked policy are immutable."
            )

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
        entries: list[_ComposeEntry[_T]] = []
        seen: set[Identity] = set()
        for current in (scope,) if local else scope.mro:
            binding = self._scope_bindings.get(current)
            if binding is None:
                continue
            identity = binding.identity
            if identity not in seen:
                seen.add(identity)
                bucket = self._buckets.get(identity)
                if bucket is not None:
                    entries.extend(bucket.values())
            if binding.blocked or identity.blocked:
                break
        return tuple(entries)

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
        binding = self._scope_bindings.get(scope)
        if binding is None:
            binding = self._scope_bindings[scope] = ScopeBinding()
        identity = binding.identity
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
            bucket = OrderedDict()
            self._buckets[identity] = bucket
        bucket[token] = entry
        if position == "prepend":
            bucket.move_to_end(token, last=False)
        return entry

    def _remove(self, identity: Identity, token: object) -> None:
        current = self._buckets.get(identity)
        if current is None:
            return
        # Detach an empty bucket before dropping the value: its finalizer may add.
        entry = current.pop(token, None)
        if not current:
            del self._buckets[identity]
        del entry

    def _clear_bucket(self, identity: Identity) -> None:
        bucket = self._buckets.pop(identity, None)
        if bucket is not None:
            bucket.clear()

    def _disposer(
        self,
        entry: _ComposeEntry[_T],
    ) -> Callable[[], None]:
        compose: Compose[_T, _R] | None = self
        identity = entry.identity
        token = entry.token
        error: BaseException | None = None

        def dispose() -> None:
            nonlocal compose, error
            if compose is None:
                if error is not None:
                    raise error
                return
            current = compose
            compose = None
            try:
                current._remove(identity, token)
            except BaseException as failure:
                error = failure
                raise

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
                for entry in bucket.values()
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
        return sum(len(bucket) for bucket in self._buckets.values())
