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

from __future__ import annotations

import weakref
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from itertools import chain
from typing import Any, Literal, TypeVar, cast

from slyme.utils.exception import exception_group
from slyme.utils.execution import once

from .schema import ContextPathError, Ref, RefEntry, RefLeafConfig, Schema
from .scope import Identity, Scope, ScopeBinding

__all__ = ["ContextStore"]

_T = TypeVar("_T")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(eq=False, slots=True)
class _IdentityData:
    value: Any = _MISSING
    token: object | None = None
    scopes: set[Scope] = field(default_factory=set)


@dataclass(slots=True)
class _ScopeUsage:
    """Current viewers and binding history retained across viewer lifetimes."""

    viewers: set[object] = field(default_factory=set)
    entries: set[RefEntry[Any]] = field(default_factory=set)


class _ContextBinding:
    """Own one field's identities and values, independently of Store indexes.

    Saved bindings outlive active data while their Scopes remain reachable.
    Reads never acquire ownership; the last retaining Scope releases the data.
    """

    __slots__ = (
        "_data",
        "_scope_bindings",
        "__weakref__",
    )

    def __init__(self) -> None:
        self._data: dict[Identity, _IdentityData] = {}
        self._scope_bindings: weakref.WeakKeyDictionary[Scope, ScopeBinding] = (
            weakref.WeakKeyDictionary()
        )

    @property
    def scopes(self) -> tuple[Scope, ...]:
        """Snapshot Scopes retaining identity data, excluding inactive identities."""
        return tuple(scope for data in self._data.values() for scope in data.scopes)

    def _get_data(self, scope: Scope) -> _IdentityData | None:
        binding = self._scope_bindings.get(scope)
        return None if binding is None else self._data.get(binding.identity)

    def _ensure_data(self, scope: Scope) -> _IdentityData:
        """Retain local data under the saved identity or a new immutable identity."""
        binding = self._scope_bindings.get(scope)
        if binding is None:
            binding = self._scope_bindings[scope] = ScopeBinding()
        data = self._data.get(binding.identity)
        if data is None:
            data = self._data[binding.identity] = _IdentityData()
        data.scopes.add(scope)
        return data

    def restore_scope(self, scope: Scope) -> bool:
        """Restore saved identity ownership; return False for an unbound Scope."""
        if scope not in self._scope_bindings:
            return False
        self._ensure_data(scope)
        return True

    def get(self, scope: Scope, *, local: bool = False) -> Any:
        """Return the first visible value, or _MISSING without acquiring ownership."""
        for current in (scope,) if local else scope.mro:
            binding = self._scope_bindings.get(current)
            if binding is None:
                continue
            data = self._data.get(binding.identity)
            if data is not None and data.value is not _MISSING:
                return data.value
            if binding.blocked or binding.identity.blocked:
                break
        return _MISSING

    def matches(self, scope: Scope, *, token: object | None) -> bool:
        """Check that a local value exists and its token matches by identity."""
        data = self._get_data(scope)
        return data is not None and data.value is not _MISSING and data.token is token

    def set(self, scope: Scope, value: Any, *, token: object | None = None) -> None:
        """Write freely when unprotected; otherwise require the stored token."""
        data = self._ensure_data(scope)
        if data.token is not None and data.token is not token:
            raise ValueError("Context value token does not match.")
        data.token = token
        data.value = value

    def delete(self, scope: Scope, *, token: object | None = None) -> None:
        """Delete locally, enforcing any stored token; an absent value is a no-op."""
        data = self._get_data(scope)
        if data is not None:
            if data.token is not None and data.token is not token:
                raise ValueError("Context value token does not match.")
            # Release ownership before dropping a value that may reenter in __del__.
            data.token = None
            data.value = _MISSING

    def bind(self, scope: Scope, binding: ScopeBinding) -> None:
        """Fix local lookup policy and retain its identity's current data."""
        previous = self._scope_bindings.get(scope)
        if previous is None:
            self._scope_bindings[scope] = binding
        elif previous != binding:
            raise ValueError(
                "A Scope binding's identity and blocked policy are immutable."
            )
        self._ensure_data(scope)

    def release_scope(self, scope: Scope) -> None:
        """Release data after its last retaining Scope, keeping binding policy."""
        binding = self._scope_bindings.get(scope)
        if binding is None:
            return
        data = self._data.get(binding.identity)
        if data is None:
            return
        data.scopes.discard(scope)
        if not data.scopes:
            del self._data[binding.identity]

    def clear(self) -> None:
        """Clear values and saved identities."""
        self._data.clear()
        self._scope_bindings.clear()


_ContextData = dict[RefEntry[Any], _ContextBinding]


class ContextStore:
    """Scoped data shared by an application, without a caller's lifecycle state.

    Context resolves live entries and required leaf/container roles through Schema.
    Acquire each viewer before writing through its Scope, release it exactly once,
    and dispose the store when its application ends. Context coordinates these steps.
    """

    def __init__(self, schema: Schema) -> None:
        self._schema = schema
        self._data: _ContextData = {}
        self._scope_usages: weakref.WeakKeyDictionary[Scope, _ScopeUsage] = (
            weakref.WeakKeyDictionary()
        )
        schema._attach_store(self)

    def dispose(self) -> None:
        """Detach from Schema and clear storage after releasing all viewers."""
        self._schema._detach_store(self)
        self._data.clear()

    def acquire_scope(self, viewer: object, requested_scope: Scope) -> None:
        """Register all viewers before restoring bindings; Context owns rollback."""
        usages = self._scope_usages
        restored_scopes: list[Scope] = []
        for scope in requested_scope.mro:
            usage = usages.get(scope)
            if usage is None:
                usage = usages[scope] = _ScopeUsage()
            elif not usage.viewers:
                restored_scopes.append(scope)
            usage.viewers.add(viewer)
        # Restore identity data -> scopes binding
        for scope in restored_scopes:
            entries = usages[scope].entries
            for entry in tuple(entries):
                binding = self._data.get(entry)
                if binding is None or not binding.restore_scope(scope):
                    entries.discard(entry)

    def release_scope(self, viewer: object, requested_scope: Scope) -> None:
        """Release one registered viewer exactly once, even after restoration fails."""
        usages = self._scope_usages
        expired: list[tuple[Scope, _ScopeUsage]] = []
        for scope in requested_scope.mro:
            usage = usages[scope]
            usage.viewers.remove(viewer)
            if not usage.viewers:
                expired.append((scope, usage))

        errors = []
        for scope, usage in expired:
            for entry in tuple(usage.entries):
                binding = self._data.get(entry)
                if binding is None:
                    usage.entries.discard(entry)
                    continue
                try:
                    binding.release_scope(scope)
                except BaseException as e:
                    errors.append(e)
        if errors:
            raise exception_group("Failed to release expired Scopes", errors)

    def _writable_binding(self, scope: Scope, entry: RefEntry[Any]) -> _ContextBinding:
        """Index usage before binding changes can release values or raise."""
        binding = self._data.get(entry)
        if binding is None:
            binding = _ContextBinding()
            self._data[entry] = binding
        self._scope_usages[scope].entries.add(entry)
        return binding

    def delete_entry(self, entry: RefEntry[Any]) -> None:
        """Withdraw one Schema definition from this application's active index."""
        binding = self._data.pop(entry, None)
        if binding is None:
            return
        for scope in binding.scopes:
            usage = self._scope_usages.get(scope)
            if usage is not None:
                usage.entries.discard(entry)
        binding.clear()

    def get(
        self, scope: Scope, entry: RefEntry[_T], *, local: bool = False
    ) -> _T | _Missing:
        """Return a resolved leaf's visible value, or _MISSING if absent."""
        binding = self._data.get(entry)
        if binding is None:
            return _MISSING
        return binding.get(scope, local=local)

    def _iter_items(
        self,
        scope: Scope,
        entry: RefEntry[Any],
        *,
        local: bool,
    ) -> Iterator[tuple[Ref[Any], Any]]:
        for leaf in self._schema._leaf_entries(entry.ref.parts):
            value = self.get(scope, leaf, local=local)
            if value is not _MISSING:
                yield leaf.ref, value

    def exists(
        self, scope: Scope, entry: RefEntry[Any], *, local: bool = False
    ) -> bool:
        """Check a resolved leaf or container's visibility; the root always exists."""
        if isinstance(entry.config, RefLeafConfig):
            return self.get(scope, entry, local=local) is not _MISSING
        return not entry.ref.parts or any(self._iter_items(scope, entry, local=local))

    def items(
        self,
        scope: Scope,
        entry: RefEntry[Any],
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        """Iterate a resolved container's leaves; reject empty non-root containers immediately."""
        items = self._iter_items(scope, entry, local=local)
        if not entry.ref.parts:
            return items
        first = next(items, None)
        if first is None:
            raise ContextPathError(entry.ref.path)
        return chain((first,), items)

    @staticmethod
    def _validate_mode(
        entry: RefEntry[Any], mode: Literal["assign", "register"]
    ) -> None:
        config = cast(RefLeafConfig[Any], entry.config)
        if config.mode != mode:
            raise ContextPathError(
                f"Path {entry.ref.path!r} uses {config.mode!r} mode; "
                f"this operation requires {mode!r} mode."
            )

    def _delete_leaf(self, scope: Scope, entry: RefEntry[Any]) -> None:
        binding = self._data.get(entry)
        if binding is not None:
            binding.delete(scope)

    def set(self, scope: Scope, entry: RefEntry[_T], value: _T) -> None:
        """Set one local assignment value; registration paths reject assignment."""
        self._validate_mode(entry, "assign")
        self._writable_binding(scope, entry).set(scope, value)

    def delete(self, scope: Scope, entry: RefEntry[Any]) -> None:
        """Delete local values under a path; the empty path covers all leaves.

        Deletion changes only the identity bound to this Scope for each leaf.
        Schema declarations, inheritance barriers, and effects remain intact.
        Every selected leaf must use assign mode, including absent local values.
        """
        if isinstance(entry.config, RefLeafConfig):
            self._validate_mode(entry, "assign")
            self._delete_leaf(scope, entry)
        else:
            self.drop(scope, (entry,))

    def update(
        self, scope: Scope, updates: Iterable[tuple[RefEntry[Any], Any]]
    ) -> None:
        """Consume resolved leaf updates, keep each entry's last value, then validate modes.

        Preflight failures leave bindings unchanged. Failures during application
        of the writes do not trigger rollback.
        """
        entries = dict(updates)
        for entry in entries:
            self._validate_mode(entry, "assign")
        for entry, value in entries.items():
            self._writable_binding(scope, entry).set(scope, value)

    def drop(self, scope: Scope, entries: Iterable[RefEntry[Any]]) -> None:
        """Consume resolved entries, then validate all descendant modes before deletion.

        Preflight failures leave bindings unchanged. Failures during application
        of the deletions do not trigger rollback.
        """
        leaves: set[RefEntry[Any]] = set()
        for entry in set(entries):
            if isinstance(entry.config, RefLeafConfig):
                leaves.add(entry)
            else:
                leaves.update(self._schema._leaf_entries(entry.ref.parts))
        for leaf in leaves:
            self._validate_mode(leaf, "assign")
        for leaf in leaves & self._scope_usages[scope].entries:
            self._delete_leaf(scope, leaf)

    def register(
        self, scope: Scope, entry: RefEntry[_T], value: _T
    ) -> Callable[[], None]:
        """Install a registration at this Scope with an empty local identity.

        The returned disposer removes only this installation. Assignment paths reject
        registration; inherited values can be shadowed in a child Scope.
        The disposer holds its binding until called, then drops that reference even
        if cleanup fails. It does not retain the Store.
        """
        self._validate_mode(entry, "register")
        binding: _ContextBinding | None
        binding = self._writable_binding(scope, entry)
        if binding.get(scope, local=True) is not _MISSING:
            raise ContextPathError(
                f"Cannot register existing local path {entry.ref.path!r}."
            )
        token = object()
        binding.set(scope, value, token=token)

        @once
        def dispose() -> None:
            nonlocal binding
            try:
                if cast(_ContextBinding, binding).matches(scope, token=token):
                    cast(_ContextBinding, binding).delete(scope, token=token)
            finally:
                binding = None

        return dispose

    def bind(
        self,
        scope: Scope,
        entry: RefEntry[Any],
        binding: ScopeBinding,
    ) -> None:
        """Fix one leaf's identity and fallback policy at this Scope."""
        self._writable_binding(scope, entry).bind(scope, binding)
