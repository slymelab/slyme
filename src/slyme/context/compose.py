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
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import Any, Generic, ParamSpec, Protocol, TypeVar, overload

from slyme.execution import once

from .scope import Identity, Scope, ScopeBinding, identities

__all__ = [
    "Compose",
    "ComposeLayer",
    "ComposeLayerInit",
    "ComposeRegistration",
    "ComposeView",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_S = TypeVar("_S")


@dataclass(frozen=True, slots=True, kw_only=True)
class ComposeLayerInit:
    """Inputs supplied to the factory when creating a layer."""

    identity: Identity


@dataclass(frozen=True, eq=False, slots=True)
class ComposeRegistration:
    """The unique token and write origin of one registration."""

    scope: Scope
    binding: ScopeBinding


class ComposeLayer(Protocol[_P]):
    """Synchronous, token-addressed storage for one Identity.

    register returns a synchronous disposer for exactly that registration and
    must leave data unchanged on failure. Neither registration nor disposal
    explicitly reenters its owning Compose. Compose alone registers on its layers
    and invokes each returned disposer at most once.
    The remaining arguments belong to the application.
    """

    def register(
        self, registration: ComposeRegistration, /, *args: _P.args, **kwargs: _P.kwargs
    ) -> Callable[[], None]: ...


_L = TypeVar("_L", bound=ComposeLayer[...], covariant=True)


@dataclass
class _Bucket(Generic[_L]):
    data: _L
    registrations: set[ComposeRegistration] = field(default_factory=set)


@dataclass(frozen=True, eq=False, slots=True)
class ComposeView(Iterable[_L]):
    """A live, reiterable selection of layers in lookup order."""

    _compose: Compose[_L, Any] = field(repr=False)
    scope: Scope | None
    local: bool

    def identities(self) -> Iterator[Identity]:
        """Include visible empty bindings; an unscoped view enumerates live buckets."""
        if self.scope is None:
            return iter(self._compose._buckets)
        return identities(self.scope, self._compose._scope_bindings, local=self.local)

    def items(self) -> Iterator[tuple[Identity, _L]]:
        """Yield the identity and data of each visible live layer."""
        buckets = self._compose._buckets
        for identity in self.identities():
            bucket = buckets.get(identity)
            if bucket is not None:
                yield identity, bucket.data

    def __iter__(self) -> Iterator[_L]:
        return (layer for _, layer in self.items())


class Compose(Generic[_L, _R]):
    """Own registration tokens and expose visible layers in explicit lookup order.

    The factory receives ComposeLayerInit with the writable Identity.
    A layer receives a unique ComposeRegistration and the business arguments.

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
        factory: Callable[[ComposeLayerInit], _L],
        query: Callable[[ComposeView[_L]], _R] | None = None,
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
        scope_label: object = None,
        scope_parent: Scope | None = None,
        binding: ScopeBinding | Identity | Iterable[Identity],
    ) -> Scope:
        """Create a configured Scope without changing the parent or owning cleanup."""
        return self.derive_many(
            scope_label=scope_label, scope_parent=scope_parent, bindings={self: binding}
        )

    @staticmethod
    def derive_many(
        *,
        scope_label: object = None,
        scope_parent: Scope | None = None,
        bindings: Mapping[
            Compose[Any, Any], ScopeBinding | Identity | Iterable[Identity]
        ],
    ) -> Scope:
        """Configure several Composes on one new Scope without lifecycle ownership."""
        scope = Scope(label=scope_label, parent=scope_parent)
        for compose, binding in bindings.items():
            compose._bind(
                scope,
                binding if isinstance(binding, ScopeBinding) else ScopeBinding(binding),
            )
        return scope

    def _bind(self, scope: Scope, binding: ScopeBinding) -> None:
        previous = self._scope_bindings.get(scope)
        if previous is None:
            self._scope_bindings[scope] = binding
        elif previous != binding:
            raise ValueError(
                "A Scope binding's identities and blocked policy are immutable."
            )

    def register(
        self: Compose[ComposeLayer[_P], _R],
        scope: Scope,
        /,
        *args: _P.args,
        **kwargs: _P.kwargs,
    ) -> Callable[[], None]:
        """Register the business arguments and return their exact disposer.

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
            bucket = _Bucket(self._factory(ComposeLayerInit(identity=identity)))
            self._buckets[identity] = bucket
        registration = ComposeRegistration(scope, binding)
        try:
            cleanup = bucket.data.register(registration, *args, **kwargs)
        except BaseException:
            if not bucket.registrations:
                del self._buckets[identity]
            raise
        bucket.registrations.add(registration)

        @once
        def dispose() -> None:
            self._remove(registration, cleanup)

        return dispose

    def _remove(
        self, registration: ComposeRegistration, cleanup: Callable[[], None]
    ) -> None:
        identity = registration.binding.identity
        bucket = self._buckets[identity]
        bucket.registrations.remove(registration)
        if not bucket.registrations:
            # Detach before releasing payloads: finalizers may register a new layer.
            del self._buckets[identity]
        cleanup()

    def layers(
        self,
        scope: Scope | None = None,
        *,
        local: bool = False,
    ) -> ComposeView[_L]:
        """Select layers lazily; local reads use only the Scope's writable Identity."""
        if scope is None and local:
            raise ValueError("local=True requires a Scope.")
        return ComposeView(self, scope, local)

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
        query: Callable[[ComposeView[_L]], _S],
        *,
        local: bool = False,
    ) -> _S: ...
    def resolve(
        self,
        scope: Scope,
        query: Callable[[ComposeView[_L]], _S] | None = None,
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
