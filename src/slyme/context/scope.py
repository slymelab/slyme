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

from dataclasses import dataclass, field
from enum import Enum
from typing import overload

__all__ = ["Identity", "Scope", "ScopeBinding"]

_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(frozen=True, eq=False)
class Identity:
    """Shared storage identity with immutable fallback policy.

    Sharing requires the same object, not an equal label. Storage is local to
    each Context field or Compose. A blocked identity stops ancestor lookup
    after its own values, including when its storage is empty.
    """

    label: str | None = None
    blocked: bool = False


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    """Immutable storage selection and local fallback policy for a new Scope.

    Each default binding has a private Identity and allows ancestor lookup.
    Either this binding's blocked flag or its Identity's flag stops fallback.
    """

    identity: Identity = field(default_factory=Identity)
    blocked: bool = False


@dataclass(frozen=True, eq=False, repr=False, init=False)
class Scope:
    """One immutable C3 position with direct parents normalized to a tuple."""

    label: object = None
    parents: tuple[Scope, ...] = ()
    _mro: tuple[Scope, ...] = field(init=False)

    def __init__(
        self,
        *,
        label: object = None,
        parents: Scope | tuple[Scope, ...] = (),
    ) -> None:
        direct_parents = (parents,) if isinstance(parents, Scope) else parents
        if len(direct_parents) != len(set(direct_parents)):
            raise TypeError("A Scope cannot contain duplicate direct parents.")

        object.__setattr__(self, "label", label)
        object.__setattr__(self, "parents", direct_parents)
        object.__setattr__(self, "_mro", (self, *self._merge_mro(direct_parents)))

    @staticmethod
    def _merge_mro(parents: tuple[Scope, ...]) -> tuple[Scope, ...]:
        if len(parents) == 1:
            return parents[0].mro
        pending = [list(parent.mro) for parent in parents]
        pending.append(list(parents))
        result: list[Scope] = []

        while True:
            pending = [sequence for sequence in pending if sequence]
            if not pending:
                return tuple(result)

            candidate = next(
                (
                    sequence[0]
                    for sequence in pending
                    if not any(
                        any(value is sequence[0] for value in other[1:])
                        for other in pending
                    )
                ),
                None,
            )
            if candidate is None:
                raise TypeError("Cannot create a consistent Scope C3 linearization.")

            result.append(candidate)
            for sequence in pending:
                if sequence and sequence[0] is candidate:
                    sequence.pop(0)

    @property
    def mro(self) -> tuple[Scope, ...]:
        """Return this Scope followed by its C3-linearized ancestors."""
        return self._mro

    def fork(
        self,
        *,
        label: object = None,
    ) -> Scope:
        """Create a base Scope with this Scope as its only direct parent."""
        return Scope(label=label, parents=self)

    @overload
    def find(self, label: object, default: Scope | _Missing = _MISSING) -> Scope: ...
    @overload
    def find(self, label: object, default: Scope | None) -> Scope | None: ...
    def find(
        self, label: object, default: Scope | None | _Missing = _MISSING
    ) -> Scope | None:
        """Return the first label match in C3 order, or the explicit default.

        If no Scope matches and no default is supplied, raise LookupError.
        """
        for scope in self.mro:
            if scope.label == label:
                return scope
        if default is _MISSING:
            raise LookupError(f"No visible Scope has label {label!r}.")
        return default

    def find_all(self, label: object) -> tuple[Scope, ...]:
        """Return all label matches in C3 order, or an empty tuple."""
        return tuple(scope for scope in self.mro if scope.label == label)
