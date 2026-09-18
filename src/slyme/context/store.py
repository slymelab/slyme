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
from types import MappingProxyType
from typing import Any, Literal, TypeVar, cast, overload

from slyme.utils.tree import TREE_ENGINE_REGISTRY, TreeEngine
from slyme.utils.tree.common import flatten_mapping_proxy, unflatten_mapping_proxy

from .schema import Ref, RefEntry, RefLeafConfig, Schema
from .scope import Scope

__all__ = ["ContextStore", "ContextKey", "ContextPathError"]

_T = TypeVar("_T")
ContextKey = str | Ref[Any]
_RefRole = Literal["any", "leaf", "container"]
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
_Blocked = Enum("_Blocked", ["MARK"])
_BLOCKED = _Blocked.MARK

CTX_EVAL_ENGINE = TreeEngine("ctx_eval_engine", register_defaults=True)
TREE_ENGINE_REGISTRY.register(CTX_EVAL_ENGINE, key="ctx_eval_engine")
CTX_EVAL_ENGINE.register(
    MappingProxyType, flatten_mapping_proxy, unflatten_mapping_proxy
)


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
    """One current value and an optional inheritance barrier per identity."""

    __slots__ = (
        "_entry",
        "_data",
        "_scope_identities",
        "_scope_usage",
        "__weakref__",
    )

    def __init__(
        self, entry: RefEntry[Any], scope_usage: dict[Scope, _ScopeUsage]
    ) -> None:
        self._entry = entry
        self._data: dict[Hashable, _IdentityData] = {}
        self._scope_identities: weakref.WeakKeyDictionary[Scope, Hashable] = (
            weakref.WeakKeyDictionary()
        )
        self._scope_usage = scope_usage

    def _retain_scope(self, scope: Scope, identity: Hashable) -> _IdentityData:
        data = self._data.get(identity)
        if data is None:
            data = self._data[identity] = _IdentityData()
        data.scopes.add(scope)
        self._scope_usage[scope].entries.add(self._entry)
        return data

    def bind(self, scope: Scope, *, identity: Hashable) -> None:
        current = self._scope_identities.get(scope, _MISSING)
        if current is not _MISSING and current != identity:
            raise ValueError("A Scope cannot be rebound to another Context identity.")
        self._scope_identities[scope] = identity
        self._retain_scope(scope, identity)

    def _identity_for(self, scope: Scope, *, create: bool) -> Hashable:
        try:
            identity = self._scope_identities[scope]
        except KeyError:
            if not create:
                raise LookupError("Scope has no bound storage identity.") from None
            identity = object()
            self._scope_identities[scope] = identity
        if create:
            self._retain_scope(scope, identity)
        return identity

    def acquire_scope(self, scope: Scope) -> None:
        """Restore an indexed binding when a Scope gains its first live viewer."""
        self._retain_scope(scope, self._identity_for(scope, create=False))

    def _local_data(self, scope: Scope) -> _IdentityData | None:
        try:
            identity = self._identity_for(scope, create=False)
        except LookupError:
            return None
        return self._data.get(identity)

    def resolve(self, scope: Scope, *, local: bool = False) -> Any:
        result: Any = _MISSING
        for current in (scope,) if local else scope.mro:
            identity = self._scope_identities.get(current, _MISSING)
            data = self._data.get(identity)
            if data is not None and result is _MISSING:
                if data.value is not _MISSING:
                    result = data.value
                elif data.blocked:
                    result = _BLOCKED
        if result is _MISSING or result is _BLOCKED:
            raise LookupError("Context value is not visible.")
        return result

    def set_value(self, scope: Scope, value: Any) -> None:
        identity = self._identity_for(scope, create=True)
        self._data[identity].value = value

    def register_value(self, scope: Scope, value: Any) -> Callable[[], None]:
        current = self._local_data(scope)
        if current is not None and current.value is not _MISSING:
            raise ValueError("Context already has a local value.")
        identity = self._identity_for(scope, create=True)
        token = object()
        current = self._data[identity]
        current.token = token
        current.value = value
        data: _IdentityData | None = current

        def dispose() -> None:
            nonlocal data
            current, data = data, None
            if current is not None and current.token is token:
                current.token = None
                current.value = _MISSING

        return dispose

    def delete_value(self, scope: Scope) -> None:
        data = self._local_data(scope)
        if data is not None:
            data.value = _MISSING

    def block(self, scope: Scope) -> None:
        identity = self._identity_for(scope, create=True)
        self._data[identity].blocked = True

    def release_scope(self, scope: Scope) -> None:
        """Release one Scope and remove its identity if no bound Scope remains."""
        try:
            identity = self._identity_for(scope, create=False)
        except LookupError:
            return
        data = self._data.get(identity)
        if data is None:
            return
        data.scopes.discard(scope)
        if not data.scopes:
            del self._data[identity]
            data.clear()


_ContextData = dict[RefEntry[Any], _ContextBinding]
_Tree = dict[str, Any]


class ContextStore:
    """Scoped data shared by an application, without a caller's lifecycle state.

    Acquire each viewer before writing through its Scope, release it exactly once,
    and close the store when its application ends. Context coordinates these steps.
    """

    def __init__(self, schema: Schema) -> None:
        self._schema = schema
        self._data: _ContextData = {}
        self._scope_usage: dict[Scope, _ScopeUsage] = {}
        self._seen_scopes: weakref.WeakSet[Scope] = weakref.WeakSet()
        schema._stores.add(self)

    def close(self) -> None:
        """Detach from Schema after releasing the application's viewers."""
        self._schema._stores.discard(self)

    def acquire_scope(self, viewer: object, requested_scope: Scope) -> None:
        usages = self._scope_usage
        if any(
            scope in usages and viewer in usages[scope].viewers
            for scope in requested_scope.mro
        ):
            raise RuntimeError("Context is already registered as a Scope viewer.")
        acquired: list[Scope] = []
        for scope in requested_scope.mro:
            usage = usages.get(scope)
            if usage is None:
                usage = usages[scope] = _ScopeUsage()
                acquired.append(scope)
            usage.viewers.add(viewer)
        try:
            for scope in acquired:
                if scope not in self._seen_scopes:
                    self._seen_scopes.add(scope)
                    continue
                # Active indexes end with their viewers; saved Scopes retain
                # their immutable identities and restore ownership on reuse.
                for entry in self._schema.entries:
                    binding = self._data.get(entry)
                    if binding is not None and scope in binding._scope_identities:
                        binding.acquire_scope(scope)
        except BaseException as error:
            try:
                self.release_scope(viewer, requested_scope)
            except BaseException as rollback_error:
                raise error from rollback_error
            raise

    def release_scope(self, viewer: object, requested_scope: Scope) -> None:
        usages = self._scope_usage
        if any(
            scope not in usages or viewer not in usages[scope].viewers
            for scope in requested_scope.mro
        ):
            raise RuntimeError("Context is not registered as a Scope viewer.")
        expired: list[tuple[Scope, _ScopeUsage]] = []
        for scope in requested_scope.mro:
            usage = usages[scope]
            usage.viewers.remove(viewer)
            if not usage.viewers:
                del usages[scope]
                expired.append((scope, usage))

        first_error: BaseException | None = None
        for scope, usage in expired:
            for entry in tuple(usage.entries):
                # Value finalizers may register new viewers for this Scope.
                if scope in usages:
                    break
                binding = self._data.get(entry)
                if binding is None:
                    continue
                try:
                    binding.release_scope(scope)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
            usage.entries.clear()
        if not usages:
            self._data.clear()
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

    @overload
    def _binding(
        self,
        entry: RefEntry[Any],
        *,
        create: Literal[True],
    ) -> _ContextBinding: ...

    @overload
    def _binding(
        self,
        entry: RefEntry[Any],
        *,
        create: Literal[False],
    ) -> _ContextBinding | None: ...

    def _binding(
        self,
        entry: RefEntry[Any],
        *,
        create: bool,
    ) -> _ContextBinding | None:
        binding = self._data.get(entry)
        if binding is None and create:
            binding = _ContextBinding(entry, self._scope_usage)
            self._data[entry] = binding
        return binding

    def remove_entry(self, entry: RefEntry[Any]) -> None:
        """Withdraw one Schema definition from this application's active index."""
        binding = self._data.pop(entry, None)
        if binding is None:
            return
        data = tuple(binding._data.values())
        binding._data.clear()
        for identity_data in data:
            for scope in identity_data.scopes:
                usage = self._scope_usage.get(scope)
                if usage is not None:
                    usage.entries.discard(entry)
        binding._scope_identities.clear()
        for identity_data in data:
            identity_data.clear()

    def leaf_value(self, scope: Scope, entry: RefEntry[Any], *, local: bool) -> Any:
        binding = self._binding(entry, create=False)
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
        self._binding(entry, create=True).set_value(scope, value)

    def _delete_leaf(self, scope: Scope, entry: RefEntry[Any]) -> None:
        binding = self._binding(entry, create=False)
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
            for leaf in leaves & self._scope_usage[scope].entries:
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
        for leaf in leaves & self._scope_usage[scope].entries:
            self._delete_leaf(scope, leaf)

    def register(self, scope: Scope, ref: ContextKey, value: _T) -> Callable[[], None]:
        """Install a registration at this Scope with an empty local identity.

        The returned disposer removes only this installation. Assignment paths reject
        registration; inherited values can be shadowed in a child Scope.
        """
        entry = self.validate_entry(ref, role="leaf")
        self._validate_mode(entry, "register")
        binding = self._binding(entry, create=True)
        try:
            return binding.register_value(scope, value)
        except ValueError as error:
            raise ContextPathError(
                f"Cannot register existing local path {entry.ref.path!r}."
            ) from error

    def update_tree(self, scope: Scope, ref_tree: Any, value_tree: Any) -> None:
        """
        Recursively update the context using a structure of references (ref_tree)
        and a matching structure of values (value_tree).

        Args:
            ref_tree: A nested structure (list, tuple, dict, MappingProxyType) where
                      leaves are references (Ref objects) that point to locations in the context.
            value_tree: A nested structure matching the shape of ref_tree, containing
                        the values to be updated at the corresponding references.

        The context is updated in place and the method returns ``None``.
        """
        updates: dict[ContextKey, Any] = {
            self._validate_ref(ref): CTX_EVAL_ENGINE.get_element(value_tree, path)
            for path, ref in CTX_EVAL_ENGINE.iter_with_key_path(ref_tree)
        }
        self.update(scope, updates)

    def isolate(
        self,
        scope: Scope,
        entries: tuple[RefEntry[Any], ...],
        *,
        identity: Hashable | None = None,
    ) -> None:
        """Block inherited values for prevalidated leaves at a newly owned Scope."""
        for entry in entries:
            binding = self._binding(entry, create=True)
            if identity is not None:
                binding.bind(scope, identity=identity)
            binding.block(scope)
