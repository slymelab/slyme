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

"""Immutable logical visibility identities."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import overload

__all__ = ["Identity", "Scope", "ScopeBinding", "identities"]

_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(frozen=True, eq=False)
class Identity:
    """Shared storage identity with immutable fallback policy.

    Sharing requires the same object, not an equal label. Storage is local to
    each Context field or Compose. A blocked identity stops lookup after its
    own values, including when its storage is empty.
    """

    label: str | None = None
    blocked: bool = False


@dataclass(frozen=True, slots=True, init=False)
class ScopeBinding:
    """Write to ``identity`` and read through the ordered ``identities`` list.

    Identities are deduplicated in order and truncated after the first blocked
    Identity. A blocked binding stops parent lookup after its own list.
    """

    identities: tuple[Identity, ...]
    blocked: bool
    identity: Identity = field(init=False, repr=False, compare=False)

    def __init__(
        self,
        identities: Identity | Iterable[Identity] | None = None,
        blocked: bool = False,
    ) -> None:
        if identities is None:
            identities = (Identity(),)
        elif isinstance(identities, Identity):
            identities = (identities,)
        visible: dict[Identity, None] = {}
        for identity in identities:
            visible[identity] = None
            if identity.blocked:
                break
        if not visible:
            raise ValueError("A Scope binding requires a writable Identity.")
        identities = tuple(visible)
        object.__setattr__(self, "identities", identities)
        object.__setattr__(self, "identity", identities[0])
        object.__setattr__(self, "blocked", blocked)


@dataclass(frozen=True, eq=False, repr=False, kw_only=True)
class Scope:
    """An immutable visibility scope with one optional parent."""

    label: object = None
    parent: Scope | None = None

    def lineage(self) -> Iterator[Scope]:
        """Yield this Scope followed by its ancestors, nearest first."""
        current: Scope | None = self
        while current is not None:
            yield current
            current = current.parent

    def fork(self, *, label: object = None) -> Scope:
        """Create a base Scope with this Scope as its parent."""
        return Scope(label=label, parent=self)

    @overload
    def find(self, label: object, default: Scope | _Missing = _MISSING) -> Scope: ...
    @overload
    def find(self, label: object, default: Scope | None) -> Scope | None: ...
    def find(
        self, label: object, default: Scope | None | _Missing = _MISSING
    ) -> Scope | None:
        """Return the nearest label match, or the explicit default.

        If no Scope matches and no default is supplied, raise LookupError.
        """
        for scope in self.lineage():
            if scope.label == label:
                return scope
        if default is _MISSING:
            raise LookupError(f"No visible Scope has label {label!r}.")
        return default

    def find_all(self, label: object) -> tuple[Scope, ...]:
        """Return all label matches from this Scope to its root, or an empty tuple."""
        return tuple(scope for scope in self.lineage() if scope.label == label)


def identities(
    scope: Scope,
    bindings: Mapping[Scope, ScopeBinding],
    *,
    local: bool = False,
) -> Iterator[Identity]:
    """Expand per-target bindings in lookup order, respecting every barrier."""
    if local:
        if (binding := bindings.get(scope)) is not None:
            yield binding.identity
        return
    seen: set[Identity] = set()
    for current in scope.lineage():
        if (binding := bindings.get(current)) is None:
            continue
        for identity in binding.identities:
            if identity not in seen:
                seen.add(identity)
                yield identity
        if binding.blocked or binding.identities[-1].blocked:
            return
