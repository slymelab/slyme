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

from collections.abc import Awaitable, Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypeVar, overload

from slyme.utils.tree import TreeEngine

from .default import DATA_TREE_REF, _install
from .lifecycle import Lifecycle, _Cleanup, _Disposer
from .schema import (
    ContextKey,
    ContextPathError,
    Ref,
    RefEntry,
    RefLeafConfig,
    Schema,
    _Declaration,
)
from .scope import Scope
from .store import _MISSING as _STORE_MISSING
from .store import ContextStore

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(frozen=True, repr=False, eq=False, init=False)
class Context:
    """Coordinate shared declarations and data with one owned Lifecycle.

    Forks retain the same Schema and Store references for their entire lifetime;
    these objects may change their contents, but cannot be replaced on a Context.
    Roots install independent framework Composes under `$`. All reads, including
    framework configuration, follow the bound Scope without a root fallback.
    """

    parent: Context | None = field(init=False)
    scope: Scope = field(init=False)
    root: Context = field(init=False)
    _schema: Schema = field(init=False)
    _store: ContextStore = field(init=False)
    _lifecycle: Lifecycle = field(init=False)

    def __init__(
        self,
        *,
        parent: Context | None = None,
        scope: Scope | None = None,
    ) -> None:
        """Create a lifetime and data view, installing defaults only for a new root."""
        if parent is not None:
            parent._lifecycle.assert_active()
            schema = parent._schema
            store = parent._store
            bound_scope = parent.scope if scope is None else scope
        else:
            schema = Schema()
            store = ContextStore(schema)
            bound_scope = Scope() if scope is None else scope

        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "scope", bound_scope)
        object.__setattr__(self, "root", self if parent is None else parent.root)
        object.__setattr__(self, "_schema", schema)
        object.__setattr__(self, "_store", store)
        try:
            store.acquire_scope(self, bound_scope)
        except BaseException:
            if parent is None:
                store.dispose()
            raise
        try:
            lifecycle = Lifecycle(
                parent=None if parent is None else parent._lifecycle,
                finalize=self._release,
            )
        except BaseException:
            self._release()
            raise
        object.__setattr__(self, "_lifecycle", lifecycle)
        if parent is None:
            try:
                _install(self)
            except BaseException:
                self.dispose()
                raise

    def _release(self) -> None:
        try:
            self._store.release_scope(self, self.scope)
        finally:
            if self.parent is None:
                self._store.dispose()

    @property
    def entries(self) -> tuple[RefEntry[Any], ...]:
        """Snapshot all declared entries, including containers and unset leaves."""
        self._lifecycle.assert_readable()
        return self._schema.entries

    def resolve(
        self,
        key: ContextKey,
        *,
        role: Literal["leaf", "container"] | None = None,
    ) -> Ref[Any]:
        """Return the declared Ref, independently of whether a value is installed."""
        self._lifecycle.assert_readable()
        return self._schema.resolve(key, role=role)

    def resolve_entry(
        self,
        key: ContextKey,
        *,
        role: Literal["leaf", "container"] | None = None,
    ) -> RefEntry[Any]:
        """Return the live declaration and metadata at a path."""
        self._lifecycle.assert_readable()
        return self._schema.resolve_entry(key, role=role)

    def declare(self, declaration: Schema | _Declaration) -> Callable[[], None]:
        """Declare shared paths with cleanup owned by this Context."""
        return self._lifecycle.effect(lambda: self._schema.declare(declaration))

    @overload
    def effect(self, setup: Callable[[], Callable[[], None]]) -> Callable[[], None]: ...
    @overload
    def effect(self, setup: Callable[[], _Cleanup]) -> _Disposer: ...
    @overload
    def effect(
        self, setup: Callable[[], Awaitable[_Cleanup]]
    ) -> Awaitable[_Disposer]: ...
    def effect(
        self, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]
    ) -> _Disposer | Awaitable[_Disposer]:
        """Own setup and cleanup; await asynchronous setup before using its result."""
        return self._lifecycle.effect(setup)

    def dispose(self) -> None | Awaitable[None]:
        """Close the owned subtree, clean resources in LIFO order, then release data.

        Contexts remain readable until their own release, but cannot be mutated.
        Await unfinished cleanup. Repeated calls share the same completion and error.
        """
        return self._lifecycle.dispose()

    def adispose(self) -> Awaitable[None]:
        """Always return an awaitable for disposal, retaining immediate sync cleanup."""
        return self._lifecycle.adispose()

    def fork(self, *, scope: Scope | None = None) -> Context:
        """Create an owned child sharing this Scope unless another is supplied."""
        return type(self)(parent=self, scope=scope)

    def bind(self, *refs: ContextKey, identity: Hashable) -> None:
        """Bind leaves at this Scope to immutable, per-field storage identities.

        Binding does not create a Context or Scope, block inheritance, or move
        existing values. Paths are validated before binding; identity conflicts
        leave earlier bindings in place. Use a new Scope to select new identities.
        """
        self._lifecycle.assert_active()
        entries = tuple(self._schema.resolve_entry(ref, role="leaf") for ref in refs)
        for entry in entries:
            self._store.bind(self.scope, entry, identity=identity)

    def set_blocked(self, ref: ContextKey, *, blocked: bool) -> None:
        """Set the inheritance barrier on one leaf's identity at this Scope.

        Shared identities share their barrier. Values and tokens are unchanged;
        an empty unblocked identity falls back through the reader's Scope MRO.
        Unblocking an unbound leaf does not create storage or bind an identity.
        """
        self._lifecycle.assert_active()
        entry = self._schema.resolve_entry(ref, role="leaf")
        self._store.set_blocked(self.scope, entry, blocked=blocked)

    def _entry_value(
        self,
        entry: RefEntry[Any],
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool,
    ) -> Any:
        if isinstance(entry.config, RefLeafConfig):
            value = self._store.get(self.scope, entry, local=local)
            if value is not _STORE_MISSING:
                return value
        elif self._store.exists(self.scope, entry, local=local):
            return ContextView(self, entry.ref.parts)
        if default is _MISSING:
            raise ContextPathError(entry.ref.path)
        return default

    def get(
        self,
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        """Read a leaf or live container view; defaults apply only to missing values."""
        entry = self.resolve_entry(ref)
        return self._entry_value(entry, default, local=local)

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        entry = self.resolve_entry(ref)
        return self._store.exists(self.scope, entry, local=local)

    def keys(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> Iterable[str]:
        entry = self.resolve_entry("" if ref is None else ref, role="container")
        parts = entry.ref.parts
        depth = len(parts)
        visible_names = {
            leaf.parts[depth]
            for leaf, _ in self._store.items(self.scope, entry, local=local)
        }
        return tuple(
            name for name in self._schema._child_names(parts) if name in visible_names
        )

    def to_dict(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> dict[str, Any]:
        """Project visible leaves into ordinary nested dictionaries without copying values."""
        entry = self.resolve_entry("" if ref is None else ref, role="container")
        depth = len(entry.ref.parts)
        result: dict[str, Any] = {}
        for leaf, value in self._store.items(self.scope, entry, local=local):
            relative_parts = leaf.parts[depth:]
            current = result
            for part in relative_parts[:-1]:
                current = current.setdefault(part, {})
            current[relative_parts[-1]] = value
        return result

    def _flat_items(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> Iterable[tuple[Ref[Any], Any]]:
        entry = self.resolve_entry("" if ref is None else ref, role="container")
        return self._store.items(self.scope, entry, local=local)

    def flatten(self, *, local: bool = False) -> dict[Ref[Any], Any]:
        """Return the visible Context leaves as a flat Ref-to-value mapping."""
        return dict(self._flat_items(local=local))

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        self._lifecycle.assert_readable()
        rules = self.get(DATA_TREE_REF).resolve(self.scope)
        refs, treedef = TreeEngine.flatten(ref_tree, rules=rules)
        entries = [self._schema.resolve_entry(ref) for ref in refs]
        values = [self._entry_value(entry, local=local) for entry in entries]
        return TreeEngine.unflatten(treedef, values)

    def set(self, ref: ContextKey, value: _T) -> None:
        """Assign one local value at an assign-mode leaf."""
        self._lifecycle.assert_active()
        entry = self._schema.resolve_entry(ref, role="leaf")
        self._store.set(self.scope, entry, value)

    def delete(self, ref: ContextKey) -> None:
        """Delete local assignments, rejecting any register-mode descendants."""
        self._lifecycle.assert_active()
        entry = self._schema.resolve_entry(ref)
        self._store.delete(self.scope, entry)

    def update(self, updates: Mapping[ContextKey, Any]) -> None:
        """Assign a batch after validating every path and mode, without write rollback."""
        self._lifecycle.assert_active()
        self._store.update(
            self.scope,
            (
                (self._schema.resolve_entry(ref, role="leaf"), value)
                for ref, value in updates.items()
            ),
        )

    def drop(self, refs: Iterable[ContextKey]) -> None:
        """Delete local assignments after validating all selected paths and modes."""
        self._lifecycle.assert_active()
        self._store.drop(self.scope, (self._schema.resolve_entry(ref) for ref in refs))

    def register(self, ref: ContextKey, value: _T) -> Callable[[], None]:
        """Install an owned register-mode value and return its exact early disposer."""
        return self._lifecycle.effect(
            lambda: self._store.register(
                self.scope, self._schema.resolve_entry(ref, role="leaf"), value
            )
        )

    def update_tree(self, ref_tree: Any, value_tree: Any) -> None:
        """Assign values from a matching tree after validating paths and modes."""
        self._lifecycle.assert_active()
        rules = self.get(DATA_TREE_REF).resolve(self.scope)
        updates = (
            (
                self._schema.resolve_entry(ref, role="leaf"),
                TreeEngine.get_element(value_tree, path),
            )
            for path, ref in TreeEngine.iter_with_key_path(ref_tree, rules=rules)
        )
        self._store.update(self.scope, updates)


@dataclass(frozen=True, repr=False)
class ContextView:
    """Live subtree access through get, exists, keys, to_dict, and flatten.

    Paths are relative strings; the empty string addresses this subtree itself.
    Reads use the owning Context's visibility and lifecycle.
    """

    _context: Context
    _parts: tuple[str, ...]

    def _adjust_key(self, ref: str) -> str:
        return ".".join((*self._parts, *Ref._split_path(ref)))

    def get(
        self,
        ref: str,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        return self._context.get(self._adjust_key(ref), default, local=local)

    def exists(self, ref: str, *, local: bool = False) -> bool:
        return self._context.exists(self._adjust_key(ref), local=local)

    def keys(
        self,
        ref: str = "",
        *,
        local: bool = False,
    ) -> Iterable[str]:
        return self._context.keys(self._adjust_key(ref), local=local)

    def flatten(self, *, local: bool = False) -> dict[Ref[Any], Any]:
        """Return this subtree's visible leaves keyed by absolute Refs."""
        return dict(self._context._flat_items(self._adjust_key(""), local=local))

    def to_dict(
        self,
        ref: str = "",
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_key(ref), local=local)
