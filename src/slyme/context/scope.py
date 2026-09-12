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

from collections.abc import Hashable, Iterable
from dataclasses import dataclass, field

__all__ = ["Scope"]


@dataclass(frozen=True, eq=False, repr=False)
class Scope:
    """One immutable position in a C3-linearized visibility graph."""

    name: Hashable | None = None
    parents: tuple[Scope, ...] = ()
    _mro: tuple[Scope, ...] = field(init=False)

    @staticmethod
    def _contains_identity(values: Iterable[Scope], target: Scope) -> bool:
        return any(value is target for value in values)

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
                        Scope._contains_identity(other[1:], sequence[0])
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

    def __post_init__(self) -> None:
        direct_parents = tuple(self.parents)
        if any(
            left is right
            for index, left in enumerate(direct_parents)
            for right in direct_parents[index + 1 :]
        ):
            raise TypeError("A Scope cannot contain duplicate direct parents.")
        if self.name is not None:
            try:
                hash(self.name)
            except TypeError as error:
                raise TypeError("Scope names must be hashable.") from error

        object.__setattr__(self, "parents", direct_parents)
        object.__setattr__(
            self,
            "_mro",
            (self, *self._merge_mro(direct_parents)),
        )

    @property
    def mro(self) -> tuple[Scope, ...]:
        """Return this Scope followed by its C3-linearized ancestors."""
        return self._mro

    def fork(
        self,
        *,
        name: Hashable | None = None,
    ) -> Scope:
        """Create a child Scope with this Scope as its only direct parent."""
        return type(self)(name=name, parents=(self,))

    def find(self, name: Hashable) -> Scope:
        """Find the unique visible Scope carrying *name*."""
        try:
            hash(name)
        except TypeError as error:
            raise TypeError("Scope names must be hashable.") from error
        matches = tuple(scope for scope in self.mro if scope.name == name)
        if not matches:
            raise LookupError(f"No visible Scope is named {name!r}.")
        if len(matches) > 1:
            raise LookupError(f"Multiple visible Scopes are named {name!r}.")
        return matches[0]
