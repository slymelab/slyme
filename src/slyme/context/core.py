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

from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal, TypeVar, overload

from slyme.utils.execution import once
from slyme.utils.tree import TreeEngine

from .compose import Compose
from .default import DATA_TREE_REF, _install
from .lifecycle import Lifecycle, _Cleanup, _Disposer, _Effect
from .schema import (
    ContextKey,
    ContextPathError,
    Ref,
    RefEntry,
    RefLeafConfig,
    Schema,
    _Declaration,
)
from .scope import Identity, Scope, ScopeBinding
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
    Context owns the parent/child tree; Lifecycle owns effects, disposal mode,
    and disposal state.
    Roots install independent framework Composes under `$`. All reads, including
    framework configuration, follow the bound Scope without a root fallback.
    """

    parent: Context | None = field(init=False)
    scope: Scope = field(init=False)
    root: Context = field(init=False)
    _children: dict[Context, _Effect] = field(init=False)
    _schema: Schema = field(init=False)
    _store: ContextStore = field(init=False)
    _lifecycle: Lifecycle = field(init=False)

    def __init__(
        self,
        *,
        parent: Context | None = None,
        scope: Scope | None = None,
        dispose_mode: Literal["sequential", "batch"] = "sequential",
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
        object.__setattr__(self, "_children", {})
        object.__setattr__(self, "_schema", schema)
        object.__setattr__(self, "_store", store)
        object.__setattr__(
            self, "_lifecycle", Lifecycle(self, dispose_mode=dispose_mode)
        )
        object.__setattr__(self, "_finalize", once(self._finalize))
        if parent is not None:
            parent._children[self] = parent._lifecycle._register_effect(
                lambda: self.dispose
            )
        try:
            store.acquire_scope(self, bound_scope)
            if parent is None:
                _install(self)
        except BaseException:
            # NOTE: Initialization and its registered cleanup must remain internal
            # and synchronous; this rollback cannot await asynchronous disposal.
            self.dispose()
            raise

    def _finalize(self) -> None:
        """Finish internal bookkeeping once after effect cleanup, retaining failure."""
        try:
            # NOTE: acquire_scope must register this viewer for the entire Scope
            # MRO before restoring bindings; release relies on those registrations
            # even when construction fails.
            self._store.release_scope(self, self.scope)
        finally:
            if self.parent is None:
                self._store.dispose()
            else:
                self.parent._children.pop(self).finalize()

    @property
    def dispose_mode(self) -> Literal["sequential", "batch"]:
        """Return the immutable disposal mode owned by this Context's Lifecycle."""
        return self._lifecycle.dispose_mode

    @property
    def children(self) -> tuple[Context, ...]:
        """Snapshot children in creation order, retaining those still disposing."""
        return tuple(self._children)

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
        """Own setup and cleanup; await asynchronous setup before using its result.

        Setup and cleanup must not dispose themselves, their owner, or an
        ancestor, or wait for disposal containing themselves. These reentrant
        calls and wait cycles are unsupported and are not checked.
        """
        return self._lifecycle.effect(setup)

    def dispose(self) -> None | Awaitable[None]:
        """Close the subtree, finish cleanup in dispose_mode, then release data.

        Contexts remain readable until their own release, but cannot be mutated.
        Sequential cleanup waits in LIFO order; batch cleanup joins all items.
        Await unfinished cleanup. Repeated calls share the same completion and error.
        """
        return self._lifecycle.dispose()

    def fork(
        self,
        *,
        scope: Scope | None = None,
        dispose_mode: Literal["sequential", "batch"] = "sequential",
    ) -> Context:
        """Create an owned child, sharing Scope but not inheriting disposal mode."""
        return type(self)(parent=self, scope=scope, dispose_mode=dispose_mode)

    def derive(
        self,
        *,
        label: Any | None = None,
        parents: Scope | tuple[Scope, ...] | None = None,
        bindings: Mapping[ContextKey | Compose[Any, Any], ScopeBinding | Identity]
        | None = None,
        dispose_mode: Literal["sequential", "batch"] = "sequential",
    ) -> Context:
        """Create an owned child and one new Scope configured for all targets.

        Parents defaults to this Scope, independently of lifecycle ownership;
        an empty tuple creates an independent Scope. Path keys configure declared
        leaves; Compose keys configure contribution layers without replacing
        Context values. Each ScopeBinding selects private or shared storage and
        allows ancestor fallback unless it or its Identity is blocked.
        An Identity is shorthand for ScopeBinding(identity=identity).
        Unselected targets inherit. None or empty bindings create a new Scope
        without explicit bindings. derive() is equivalent to
        fork(scope=self.scope.fork()). Disposal mode defaults to sequential,
        independently of the parent.
        """
        self._lifecycle.assert_active()
        prepared = tuple(
            (
                target
                if isinstance(target, Compose)
                else self._schema.resolve_entry(target, role="leaf"),
                ScopeBinding(binding) if isinstance(binding, Identity) else binding,
            )
            for target, binding in (bindings or {}).items()
        )
        child = self.fork(
            scope=Scope(
                label=label, parents=self.scope if parents is None else parents
            ),
            dispose_mode=dispose_mode,
        )
        try:
            for target, binding in prepared:
                if isinstance(target, Compose):
                    target._bind(child.scope, binding)
                else:
                    self._store.bind(child.scope, target, binding)
        except BaseException:
            child.dispose()
            raise
        return child

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

    def flatten(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> dict[Ref[Any], Any]:
        """Return a container's visible leaves keyed by absolute Refs; default to root."""
        entry = self.resolve_entry("" if ref is None else ref, role="container")
        return dict(self._store.items(self.scope, entry, local=local))

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
        return self._context.flatten(self._adjust_key(""), local=local)

    def to_dict(
        self,
        ref: str = "",
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_key(ref), local=local)
