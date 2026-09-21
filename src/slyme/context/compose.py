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

"""Scope-aware reversible registration in application-defined layers."""

from __future__ import annotations

import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, ParamSpec, Protocol, TypeVar, overload

from slyme.utils.execution import once

from .scope import Identity, Scope, ScopeBinding

__all__ = ["Compose", "ComposeLayer"]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_S = TypeVar("_S")


class ComposeLayer(Protocol[_P]):
    """Synchronous, token-addressed storage for one Identity.

    register returns a synchronous disposer for exactly that registration and
    must leave data unchanged on failure. Neither registration nor disposal
    explicitly reenters its owning Compose. Compose alone registers on its layers
    and invokes each returned disposer at most once.
    Registration arguments after token belong entirely to the application.
    """

    def register(
        self, token: object, /, *args: _P.args, **kwargs: _P.kwargs
    ) -> Callable[[], None]: ...


_L = TypeVar("_L", bound=ComposeLayer[...], covariant=True)


@dataclass
class _Bucket(Generic[_L]):
    data: _L
    registrations: dict[object, Scope] = field(default_factory=dict)


class Compose(Generic[_L, _R]):
    """Own registration tokens and expose visible layers in C3 order.

    Each Identity receives one factory-created ComposeLayer. A layer receives
    only its registration token and caller-supplied business arguments, never an
    implicitly bound Compose, Scope, or Identity.

    Queries receive live layers, not copies. Do not mutate the Compose
    while iterating them; snapshot selected values before invoking user code.
    Contributions retain their Scopes until their exact disposers run.
    """

    __slots__ = (
        "_factory",
        "_query",
        "_buckets",
        "_scope_bindings",
        "__weakref__",
    )

    def __init__(
        self,
        *,
        factory: Callable[[], _L],
        query: Callable[[Iterable[_L]], _R] | None = None,
    ) -> None:
        self._factory = factory
        self._query = query
        self._scope_bindings: weakref.WeakKeyDictionary[Scope, ScopeBinding] = (
            weakref.WeakKeyDictionary()
        )
        self._buckets: dict[Identity, _Bucket[_L]] = {}

    def derive(
        self,
        *,
        label: Any | None = None,
        parents: Scope | tuple[Scope, ...],
        binding: ScopeBinding | Identity,
    ) -> Scope:
        """Create a configured Scope without changing parents or owning cleanup."""
        return self.derive_many(label=label, parents=parents, bindings={self: binding})

    @staticmethod
    def derive_many(
        *,
        label: Any | None = None,
        parents: Scope | tuple[Scope, ...],
        bindings: Mapping[Compose[Any, Any], ScopeBinding | Identity],
    ) -> Scope:
        """Configure several Composes on one new Scope without lifecycle ownership."""
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

    def register(
        self: Compose[ComposeLayer[_P], _R],
        scope: Scope,
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> Callable[[], None]:
        """Forward business arguments with a unique token; return an exact disposer.

        Disposers retain this Compose until called and replay a cleanup failure
        without retrying. Removing the last registration drops its layer;
        immutable Scope bindings survive independently of layer contents.
        """
        binding = self._scope_bindings.get(scope)
        if binding is None:
            binding = self._scope_bindings[scope] = ScopeBinding()
        identity = binding.identity
        bucket = self._buckets.get(identity)
        if bucket is None:
            bucket = _Bucket(self._factory())
            self._buckets[identity] = bucket
        token = object()
        try:
            cleanup = bucket.data.register(token, *args, **kwargs)
        except BaseException:
            if not bucket.registrations:
                del self._buckets[identity]
            raise
        bucket.registrations[token] = scope

        @once
        def dispose() -> None:
            self._remove(identity, token, cleanup)

        return dispose

    def _remove(
        self, identity: Identity, token: object, cleanup: Callable[[], None]
    ) -> None:
        bucket = self._buckets[identity]
        scope = bucket.registrations.pop(token)
        if not bucket.registrations:
            # Detach before releasing payloads: finalizers may register a new layer.
            del self._buckets[identity]
        cleanup()
        del scope

    def layers(
        self,
        scope: Scope | None = None,
        *,
        local: bool = False,
    ) -> Iterable[_L]:
        """Yield layers once per visible Identity, without creating data.

        Omit scope to inspect all live layers. local=True selects only the
        supplied Scope's Identity, including registrations through other Scopes
        sharing it. Empty and duplicate-Identity bindings still apply barriers.
        """
        if scope is None:
            if local:
                raise ValueError("local=True requires a Scope.")
            yield from (bucket.data for bucket in self._buckets.values())
            return
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
                    yield bucket.data
            if binding.blocked or identity.blocked:
                break

    @overload
    def resolve(
        self,
        scope: Scope,
        query: None = None,
        *,
        local: bool = False,
    ) -> _R: ...
    @overload
    def resolve(
        self,
        scope: Scope,
        query: Callable[[Iterable[_L]], _S],
        *,
        local: bool = False,
    ) -> _S: ...
    def resolve(
        self,
        scope: Scope,
        query: Callable[[Iterable[_L]], _S] | None = None,
        *,
        local: bool = False,
    ) -> _R | _S:
        """Apply the supplied query or the constructor's default to visible data."""
        if query is None:
            if self._query is None:
                raise ValueError("Compose.resolve() requires a query.")
            return self._query(self.layers(scope, local=local))
        return query(self.layers(scope, local=local))

    def __len__(self) -> int:
        """Return the number of live registrations, independent of layer contents."""
        return sum(len(bucket.registrations) for bucket in self._buckets.values())
