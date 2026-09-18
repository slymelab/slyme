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
from collections.abc import Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypeVar, cast

from slyme.utils.execution import once

from .schema import Ref, RefEntry, RefLeafConfig, Schema
from .scope import Scope

__all__ = ["ContextStore", "ContextKey", "ContextPathError"]

_T = TypeVar("_T")
ContextKey = str | Ref[Any]
_RefRole = Literal["any", "leaf", "container"]
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


class ContextPathError(KeyError):
    """A Context path cannot be resolved or changed as requested."""


@dataclass(eq=False, slots=True)
class _IdentityData:
    value: Any = _MISSING
    token: object | None = None
    blocked: bool = False
    scopes: set[Scope] = field(default_factory=set)

    def clear(self) -> None:
        self.scopes.clear()
        self.token = None
        self.blocked = False
        self.value = _MISSING


@dataclass(slots=True)
class _ScopeUsage:
    viewers: set[object] = field(default_factory=set)
    entries: set[RefEntry[Any]] = field(default_factory=set)


class _ContextBinding:
    """Own one field's identities and values, independently of Store indexes.

    Saved identities outlive active data while their Scopes remain reachable.
    Reads never acquire ownership; the last retaining Scope releases the data.
    """

    __slots__ = (
        "_data",
        "_scope_identities",
        "__weakref__",
    )

    def __init__(self) -> None:
        self._data: dict[Hashable, _IdentityData] = {}
        self._scope_identities: weakref.WeakKeyDictionary[Scope, Hashable] = (
            weakref.WeakKeyDictionary()
        )

    @property
    def scopes(self) -> tuple[Scope, ...]:
        """Snapshot Scopes retaining identity data, excluding inactive identities."""
        return tuple(scope for data in self._data.values() for scope in data.scopes)

    def _lookup_data(self, scope: Scope) -> _IdentityData | None:
        identity = self._scope_identities.get(scope, _MISSING)
        return self._data.get(identity)

    def _ensure_data(
        self, scope: Scope, *, identity: Hashable = _MISSING
    ) -> _IdentityData:
        """Retain local data under the saved identity or a new immutable identity."""
        current = self._scope_identities.get(scope, _MISSING)
        if current is _MISSING:
            current = object() if identity is _MISSING else identity
            self._scope_identities[scope] = current
        elif identity is not _MISSING and current != identity:
            raise ValueError("A Scope cannot be rebound to another Context identity.")
        data = self._data.get(current)
        if data is None:
            data = self._data[current] = _IdentityData()
        data.scopes.add(scope)
        return data

    def restore_scope(self, scope: Scope) -> bool:
        """Restore saved identity ownership; return False for an unbound Scope."""
        if scope not in self._scope_identities:
            return False
        self._ensure_data(scope)
        return True

    def resolve(self, scope: Scope, *, local: bool = False) -> Any:
        for current in (scope,) if local else scope.mro:
            data = self._data.get(self._scope_identities.get(current, _MISSING))
            if data is None:
                continue
            if data.value is not _MISSING:
                return data.value
            if data.blocked:
                break
        raise LookupError("Context value is not visible.")

    def has_value(self, scope: Scope, *, token: object = _MISSING) -> bool:
        """Check locally without creating data; a supplied token must match by identity.

        Omitting token accepts any owner; explicit None matches only None.
        """
        data = self._lookup_data(scope)
        return (
            data is not None
            and data.value is not _MISSING
            and (token is _MISSING or data.token is token)
        )

    def set_value(
        self, scope: Scope, value: Any, *, token: object | None = None
    ) -> None:
        """Write freely when unprotected; otherwise require the stored token."""
        data = self._ensure_data(scope)
        if data.token is not None and data.token is not token:
            raise ValueError("Context value token does not match.")
        data.token = token
        data.value = value

    def delete_value(self, scope: Scope, *, token: object | None = None) -> None:
        """Delete locally, enforcing any stored token; an absent value is a no-op."""
        data = self._lookup_data(scope)
        if data is not None:
            if data.token is not None and data.token is not token:
                raise ValueError("Context value token does not match.")
            # Release ownership before dropping a value that may reenter in __del__.
            data.token = None
            data.value = _MISSING

    def block(self, scope: Scope, *, identity: Hashable | None = None) -> None:
        """Block inheritance at a saved, new private, or explicitly shared identity."""
        self._ensure_data(
            scope, identity=_MISSING if identity is None else identity
        ).blocked = True

    def release_scope(self, scope: Scope) -> None:
        """Release one Scope and remove its identity if no bound Scope remains."""
        identity = self._scope_identities.get(scope, _MISSING)
        data = self._data.get(identity)
        if data is None:
            return
        data.scopes.discard(scope)
        if not data.scopes:
            del self._data[identity]
            data.clear()

    def clear(self) -> None:
        """Detach all identities before releasing values and registration handles."""
        data = tuple(self._data.values())
        self._data.clear()
        self._scope_identities.clear()
        for identity_data in data:
            identity_data.clear()


_ContextData = dict[RefEntry[Any], _ContextBinding]
_Tree = dict[str, Any]


class ContextStore:
    """Scoped data shared by an application, without a caller's lifecycle state.

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
        self._scope_usages.clear()

    def acquire_scope(self, viewer: object, requested_scope: Scope) -> None:
        usages = self._scope_usages
        if any(
            scope in usages and viewer in usages[scope].viewers
            for scope in requested_scope.mro
        ):
            raise RuntimeError("Context is already registered as a Scope viewer.")
        restored_scopes: list[Scope] = []
        for scope in requested_scope.mro:
            usage = usages.get(scope)
            if usage is None:
                usage = usages[scope] = _ScopeUsage()
            elif not usage.viewers:
                restored_scopes.append(scope)
            usage.viewers.add(viewer)
        try:
            for scope in restored_scopes:
                # Empty usages retain history without keeping their Scope alive.
                # Saved Scopes restore immutable identity ownership on reuse.
                for entry in self._schema.entries:
                    binding = self._data.get(entry)
                    if binding is None:
                        continue
                    try:
                        restored = binding.restore_scope(scope)
                    except BaseException:
                        # A partial restore must remain reachable by rollback.
                        usages[scope].entries.add(entry)
                        raise
                    if restored:
                        usages[scope].entries.add(entry)
        except BaseException as error:
            try:
                self.release_scope(viewer, requested_scope)
            except BaseException as rollback_error:
                raise error from rollback_error
            raise

    def release_scope(self, viewer: object, requested_scope: Scope) -> None:
        usages = self._scope_usages
        if any(
            scope not in usages or viewer not in usages[scope].viewers
            for scope in requested_scope.mro
        ):
            raise RuntimeError("Context is not registered as a Scope viewer.")
        expired: list[tuple[Scope, _ScopeUsage, tuple[RefEntry[Any], ...]]] = []
        for scope in requested_scope.mro:
            usage = usages[scope]
            usage.viewers.remove(viewer)
            if not usage.viewers:
                entries = tuple(usage.entries)
                usage.entries.clear()
                expired.append((scope, usage, entries))

        first_error: BaseException | None = None
        for scope, usage, entries in expired:
            for entry in entries:
                # Value finalizers may register new viewers for this Scope.
                if usage.viewers:
                    break
                binding = self._data.get(entry)
                if binding is None:
                    continue
                try:
                    binding.release_scope(scope)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        if first_error is not None:
            raise first_error

    def validate_entry(
        self,
        key: ContextKey,
        *,
        role: _RefRole = "any",
    ) -> RefEntry[Any]:
        path = key if isinstance(key, str) else key.path

        try:
            entry = self._schema.resolve_entry(path)
        except (KeyError, ValueError) as error:
            if isinstance(key, Ref):
                raise ContextPathError(
                    f"Ref path {path!r} is not declared by this Context."
                ) from error
            raise ContextPathError(str(error)) from error

        kind: Literal["leaf", "container"]
        if isinstance(entry.config, RefLeafConfig):
            kind = "leaf"
        else:
            kind = "container"
        if role != "any" and role != kind:
            raise ContextPathError(
                f"Ref path {path!r} is declared as a {kind}, not a {role}."
            )
        return entry

    def _validate_ref(
        self,
        key: ContextKey,
        *,
        role: _RefRole = "any",
    ) -> Ref[Any]:
        return self.validate_entry(key, role=role).ref

    def _writable_binding(self, scope: Scope, entry: RefEntry[Any]) -> _ContextBinding:
        """Index usage before binding changes can release values or raise."""
        binding = self._data.get(entry)
        if binding is None:
            binding = _ContextBinding()
            self._data[entry] = binding
        self._scope_usages[scope].entries.add(entry)
        return binding

    def remove_entry(self, entry: RefEntry[Any]) -> None:
        """Withdraw one Schema definition from this application's active index."""
        binding = self._data.pop(entry, None)
        if binding is None:
            return
        for scope in binding.scopes:
            usage = self._scope_usages.get(scope)
            if usage is not None:
                usage.entries.discard(entry)
        binding.clear()

    def leaf_value(self, scope: Scope, entry: RefEntry[Any], *, local: bool) -> Any:
        binding = self._data.get(entry)
        if binding is None:
            raise ContextPathError(entry.ref.path)
        try:
            return binding.resolve(scope, local=local)
        except LookupError as error:
            raise ContextPathError(entry.ref.path) from error

    def leaf_items(
        self,
        scope: Scope,
        parts: tuple[str, ...],
        *,
        local: bool,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        for entry in self._schema._leaf_entries(parts):
            try:
                value = self.leaf_value(scope, entry, local=local)
            except ContextPathError:
                continue
            yield entry.ref, value

    def exists(self, scope: Scope, ref: ContextKey, *, local: bool = False) -> bool:
        entry = self.validate_entry(ref)
        if isinstance(entry.config, RefLeafConfig):
            try:
                self.leaf_value(scope, entry, local=local)
                return True
            except ContextPathError:
                return False
        return not entry.ref.parts or any(
            self.leaf_items(scope, entry.ref.parts, local=local)
        )

    def keys(
        self,
        scope: Scope,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        visible = tuple(self.leaf_items(scope, parts, local=local))
        if parts and not visible:
            raise ContextPathError(".".join(parts))

        result: list[str] = []
        for name in self._schema._child_names(parts):
            child_parts = (*parts, name)
            if any(
                leaf.parts[: len(child_parts)] == child_parts for leaf, _ in visible
            ):
                result.append(name)
        return tuple(result)

    def _snapshot_tree(
        self,
        scope: Scope,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        visible = tuple(self.leaf_items(scope, parts, local=local))
        if parts and not visible:
            raise ContextPathError(".".join(parts))

        result: _Tree = {}
        for leaf, value in visible:
            relative_parts = leaf.parts[len(parts) :]
            current = result
            for part in relative_parts[:-1]:
                current = current.setdefault(part, {})
            current[relative_parts[-1]] = value
        return result

    def to_dict(
        self,
        scope: Scope,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._snapshot_tree(scope, ref, local=local)

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

    def _set_entry(self, scope: Scope, entry: RefEntry[Any], value: Any) -> None:
        self._validate_mode(entry, "assign")
        self._writable_binding(scope, entry).set_value(scope, value)

    def _delete_leaf(self, scope: Scope, entry: RefEntry[Any]) -> None:
        binding = self._data.get(entry)
        if binding is not None:
            binding.delete_value(scope)

    def set(self, scope: Scope, ref: ContextKey, value: _T) -> None:
        """Set one local assignment value; registration paths reject assignment."""
        entry = self.validate_entry(ref, role="leaf")
        self._set_entry(scope, entry, value)

    def delete(self, scope: Scope, ref: ContextKey) -> None:
        """Delete local values under a path; the empty path covers all leaves.

        Deletion changes only the identity bound to this Scope for each leaf.
        Schema declarations, inheritance barriers, and effects remain intact.
        Every selected leaf must use assign mode, including absent local values.
        """
        entry = self.validate_entry(ref)
        if isinstance(entry.config, RefLeafConfig):
            self._validate_mode(entry, "assign")
            self._delete_leaf(scope, entry)
        else:
            leaves = set(self._schema._leaf_entries(entry.ref.parts))
            for leaf in leaves:
                self._validate_mode(leaf, "assign")
            for leaf in leaves & self._scope_usages[scope].entries:
                self._delete_leaf(scope, leaf)

    def update(self, scope: Scope, updates: Mapping[ContextKey, Any]) -> None:
        """Set local assignment values after validating every path and write mode.

        Preflight failures leave bindings unchanged. Failures during application
        of the writes do not trigger rollback.
        """
        entries = {
            self.validate_entry(ref, role="leaf"): value
            for ref, value in updates.items()
        }
        for entry in entries:
            self._validate_mode(entry, "assign")
        for entry, value in entries.items():
            self._set_entry(scope, entry, value)

    def drop(self, scope: Scope, refs: Iterable[ContextKey]) -> None:
        """Delete local assignment after validating all inputs and descendant modes.

        Preflight failures leave bindings unchanged. Failures during application
        of the deletions do not trigger rollback.
        """
        entries = {self.validate_entry(ref) for ref in refs}
        leaves: set[RefEntry[Any]] = set()
        for entry in entries:
            if isinstance(entry.config, RefLeafConfig):
                leaves.add(entry)
            else:
                leaves.update(self._schema._leaf_entries(entry.ref.parts))
        for leaf in leaves:
            self._validate_mode(leaf, "assign")
        for leaf in leaves & self._scope_usages[scope].entries:
            self._delete_leaf(scope, leaf)

    def register(self, scope: Scope, ref: ContextKey, value: _T) -> Callable[[], None]:
        """Install a registration at this Scope with an empty local identity.

        The returned disposer removes only this installation. Assignment paths reject
        registration; inherited values can be shadowed in a child Scope.
        The disposer holds its binding until called, then drops that reference even
        if cleanup fails. It does not retain the Store.
        """
        entry = self.validate_entry(ref, role="leaf")
        self._validate_mode(entry, "register")
        binding: _ContextBinding | None
        binding = self._writable_binding(scope, entry)
        if binding.has_value(scope):
            raise ContextPathError(
                f"Cannot register existing local path {entry.ref.path!r}."
            )
        token = object()
        binding.set_value(scope, value, token=token)

        @once
        def dispose() -> None:
            nonlocal binding
            try:
                if cast(_ContextBinding, binding).has_value(scope, token=token):
                    cast(_ContextBinding, binding).delete_value(scope, token=token)
            finally:
                binding = None

        return dispose

    def isolate(
        self,
        scope: Scope,
        entries: tuple[RefEntry[Any], ...],
        *,
        identity: Hashable | None = None,
    ) -> None:
        """Block inherited values for prevalidated leaves at a newly owned Scope."""
        for entry in entries:
            self._writable_binding(scope, entry).block(scope, identity=identity)
