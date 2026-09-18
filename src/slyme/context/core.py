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

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Hashable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, TypeVar, overload

from slyme.utils.tree import TreeEngine

from .default import DATA_TREE_REF, _install
from .lifecycle import Lifecycle, _Cleanup, _Disposer
from .schema import Ref, RefEntry, RefLeafConfig, Schema, _Declaration
from .scope import Scope
from .store import ContextKey, ContextPathError, ContextStore, _RefRole

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
_Tree = dict[str, Any]


class ContextElement(ABC):
    """
    Abstract base class for context-related entities (Context, ContextView).
    """

    __slots__ = ()

    @abstractmethod
    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        pass

    @abstractmethod
    def get(
        self,
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        pass

    @abstractmethod
    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        pass

    @abstractmethod
    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        pass

    @abstractmethod
    def _snapshot_tree(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        pass

    @abstractmethod
    def to_dict(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        """Convert to standard python dictionary recursively."""
        pass

    @abstractmethod
    def _flat_items(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        pass

    def flatten(self, *, local: bool = False) -> dict[Ref[Any], Any]:
        """Return the visible Context leaves as a flat Ref-to-value mapping."""
        return dict(self._flat_items(local=local))


@dataclass(frozen=True, repr=False, eq=False, init=False)
class Context(ContextElement):
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

    def resolve(self, path: str) -> Ref[Any]:
        """Return the declared Ref, independently of whether a value is installed."""
        self._lifecycle.assert_readable()
        return self._schema.resolve(path)

    def resolve_entry(self, path: str) -> RefEntry[Any]:
        """Return the live declaration and metadata at a path."""
        self._lifecycle.assert_readable()
        return self._schema.resolve_entry(path)

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

    def isolate(self, *refs: ContextKey, identity: Hashable | None = None) -> Context:
        """Create an owned child blocking selected inherited leaves."""
        self._lifecycle.assert_active()
        entries = tuple(self._store.validate_entry(ref, role="leaf") for ref in refs)
        child = self.fork(scope=self.scope.fork())
        try:
            self._store.isolate(child.scope, entries, identity=identity)
        except BaseException:
            child.dispose()
            raise
        return child

    def _validate_entry(
        self, ref: ContextKey, *, role: _RefRole = "any"
    ) -> RefEntry[Any]:
        self._lifecycle.assert_readable()
        return self._store.validate_entry(ref, role=role)

    def _entry_value(self, entry: RefEntry[Any], *, local: bool) -> Any:
        if isinstance(entry.config, RefLeafConfig):
            return self._store.leaf_value(self.scope, entry, local=local)
        if entry.ref.parts and not any(
            self._store.leaf_items(self.scope, entry.ref.parts, local=local)
        ):
            raise ContextPathError(entry.ref.path)
        return ContextView(self, entry.ref.parts)

    def get(
        self,
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        """Read a leaf or live container view; defaults apply only to missing values."""
        entry = self._validate_entry(ref)
        try:
            return self._entry_value(entry, local=local)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        self._lifecycle.assert_readable()
        return self._store.exists(self.scope, ref, local=local)

    def keys(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> Iterable[str]:
        self._lifecycle.assert_readable()
        return self._store.keys(self.scope, ref, local=local)

    def _snapshot_tree(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> _Tree:
        self._lifecycle.assert_readable()
        return self._store.to_dict(self.scope, ref, local=local)

    def to_dict(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> dict[str, Any]:
        """Project visible leaves into ordinary nested dictionaries without copying values."""
        return self._snapshot_tree(ref, local=local)

    def _flat_items(
        self, ref: ContextKey | None = None, *, local: bool = False
    ) -> Iterable[tuple[Ref[Any], Any]]:
        self._lifecycle.assert_readable()
        parts = (
            () if ref is None else self._validate_entry(ref, role="container").ref.parts
        )
        items = self._store.leaf_items(self.scope, parts, local=local)
        if not parts:
            return items
        visible = tuple(items)
        if not visible:
            raise ContextPathError(".".join(parts))
        return visible

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        self._lifecycle.assert_readable()
        rules = self.get(DATA_TREE_REF).resolve(self.scope)
        refs, treedef = TreeEngine.flatten(ref_tree, rules=rules)
        entries = [self._store.validate_entry(ref) for ref in refs]
        values = [self._entry_value(entry, local=local) for entry in entries]
        return TreeEngine.unflatten(treedef, values)

    def set(self, ref: ContextKey, value: _T) -> None:
        """Assign one local value at an assign-mode leaf."""
        self._lifecycle.assert_active()
        self._store.set(self.scope, ref, value)

    def delete(self, ref: ContextKey) -> None:
        """Delete local assignments, rejecting any register-mode descendants."""
        self._lifecycle.assert_active()
        self._store.delete(self.scope, ref)

    def update(self, updates: Mapping[ContextKey, Any]) -> None:
        """Assign a batch after validating every path and mode, without write rollback."""
        self._lifecycle.assert_active()
        self._store.update(self.scope, updates)

    def drop(self, refs: Iterable[ContextKey]) -> None:
        """Delete local assignments after validating all selected paths and modes."""
        self._lifecycle.assert_active()
        self._store.drop(self.scope, refs)

    def register(self, ref: ContextKey, value: _T) -> Callable[[], None]:
        """Install an owned register-mode value and return its exact early disposer."""
        return self._lifecycle.effect(
            lambda: self._store.register(self.scope, ref, value)
        )

    def update_tree(self, ref_tree: Any, value_tree: Any) -> None:
        """Assign values from a matching tree after validating paths and modes."""
        self._lifecycle.assert_active()
        rules = self.get(DATA_TREE_REF).resolve(self.scope)
        updates: dict[ContextKey, Any] = {
            self._store._validate_ref(ref): TreeEngine.get_element(value_tree, path)
            for path, ref in TreeEngine.iter_with_key_path(ref_tree, rules=rules)
        }
        self._store.update(self.scope, updates)


@dataclass(frozen=True, repr=False)
class ContextView(ContextElement):
    """
    Read-only view of a subtree within a Context.
    """

    _context: Context
    _parts: tuple[str, ...]

    def _adjust_ref(self, ref: ContextKey | None) -> Ref[Any]:
        if ref is None:
            return self._context._validate_entry(".".join(self._parts)).ref
        if isinstance(ref, str):
            relative_parts = Ref._split_path(ref)
            return self._context._validate_entry(
                ".".join((*self._parts, *relative_parts))
            ).ref
        if ref.parts[: len(self._parts)] != self._parts:
            raise ContextPathError(
                f"Ref path {ref.path!r} is outside this ContextView."
            )
        return self._context._validate_entry(ref).ref

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        rules = self._context.get(DATA_TREE_REF).resolve(self._context.scope)
        refs, treedef = TreeEngine.flatten(ref_tree, rules=rules)
        adjusted = [self._adjust_ref(ref) for ref in refs]
        values = [self._context.get(ref, local=local) for ref in adjusted]
        return TreeEngine.unflatten(treedef, values)

    def get(
        self,
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        return self._context.get(self._adjust_ref(ref), default, local=local)

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        return self._context.exists(self._adjust_ref(ref), local=local)

    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        return self._context.keys(self._adjust_ref(ref), local=local)

    def _snapshot_tree(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        return self._context._snapshot_tree(self._adjust_ref(ref), local=local)

    def _flat_items(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        return self._context._flat_items(self._adjust_ref(ref), local=local)

    def to_dict(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref), local=local)
