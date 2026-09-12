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

import asyncio
import weakref
from abc import ABC, abstractmethod
from collections.abc import (
    Awaitable,
    Callable,
    Generator,
    Hashable,
    Iterable,
    Iterator,
    Mapping,
)
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from inspect import isawaitable
from typing import Any, Generic, Literal, TypeVar, cast, overload

from slyme.utils.awaitable import resolve

from .compose import Compose
from .ref import Ref
from .schema import Schema, _RefEntry, _RefLeafConfig
from .scope import Scope

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
_Blocked = Enum("_Blocked", ["MARK"])
_BLOCKED = _Blocked.MARK
_DISPOSAL_CHAIN: ContextVar[tuple[object, ...]] = ContextVar(
    "slyme_context_disposal_chain",
    default=(),
)


_Cleanup = Callable[[], None | Awaitable[None]]
_Disposer = Callable[[], None | Awaitable[None]]


class _Completion(Generic[_T]):
    """One lazily scheduled operation whose result survives waiter cancellation."""

    __slots__ = ("_operation", "_check", "_task")

    def __init__(self, operation: Awaitable[_T], check: Callable[[], None]) -> None:
        self._operation: Awaitable[_T] | None = operation
        self._check = check
        self._task: asyncio.Task[_T] | None = None

    @staticmethod
    def _observe(task: asyncio.Task[Any]) -> None:
        if not task.cancelled():
            task.exception()

    async def _wait(self) -> _T:
        self._check()
        if self._task is None:
            self._task = asyncio.create_task(
                resolve(cast(Awaitable[_T], self._operation))
            )
            self._operation = None
            self._task.add_done_callback(self._observe)
        return await asyncio.shield(self._task)

    def __await__(self) -> Generator[Any, None, _T]:
        return self._wait().__await__()


class _Effect:
    """Own setup and cleanup as one registration with a repeatable disposer."""

    __slots__ = (
        "owner",
        "_cleanup",
        "_setup",
        "_pending",
        "_disposing",
        "_disposed",
        "_error",
    )

    def __init__(self, owner: Context) -> None:
        self.owner: Context | None = owner
        self._cleanup: _Cleanup | None = None
        self._setup: _Completion[_Disposer] | None = None
        self._pending: _Completion[None] | None = None
        self._disposing = False
        self._disposed = False
        self._error: BaseException | None = None

    def _release(self) -> None:
        owner = self.owner
        self.owner = None
        if owner is not None:
            owner._forget_owned(self)

    def _chain(self) -> tuple[object, ...]:
        owners: list[object] = [self]
        owner = self.owner
        while owner is not None:
            owners.append(owner)
            owner = owner.parent
        return (*_DISPOSAL_CHAIN.get(), *owners)

    def _check(self) -> None:
        if self in _DISPOSAL_CHAIN.get():
            raise RuntimeError("Context effect cannot await its own setup or cleanup.")

    def _finish(self, error: BaseException | None = None) -> None:
        self._cleanup = None
        self._disposing = False
        self._disposed = True
        self._error = error
        self._release()

    async def _finish_setup(self, setup: Awaitable[_Cleanup]) -> _Disposer:
        token = _DISPOSAL_CHAIN.set(self._chain())
        try:
            self._cleanup = await setup
            return self.dispose
        except BaseException:
            self._release()
            raise
        finally:
            _DISPOSAL_CHAIN.reset(token)

    async def _finish_cleanup(self, cleanup: Awaitable[None]) -> None:
        token = _DISPOSAL_CHAIN.set(self._chain())
        try:
            await cleanup
        except BaseException as error:
            self._finish(error)
            raise
        else:
            self._finish()
        finally:
            _DISPOSAL_CHAIN.reset(token)

    async def _dispose_after_setup(self) -> None:
        try:
            await cast(_Completion[_Disposer], self._setup)
        except BaseException as error:
            self._finish(error)
            raise
        token = _DISPOSAL_CHAIN.set(self._chain())
        try:
            cleanup = self._cleanup
            self._cleanup = None
            if cleanup is not None:
                await resolve(cleanup())
        except BaseException as error:
            self._finish(error)
            raise
        else:
            self._finish()
        finally:
            _DISPOSAL_CHAIN.reset(token)

    def dispose(self) -> None | Awaitable[None]:
        self._check()
        if self._pending is not None:
            return self._pending
        if self._disposing:
            raise RuntimeError("Context effect disposal cannot be re-entered.")
        if self._disposed:
            if self._error is not None:
                raise self._error
            return None
        self._disposing = True
        if self._setup is not None:
            self._pending = _Completion(self._dispose_after_setup(), self._check)
            return self._pending

        owner = self.owner
        guarded = owner._enter_sync_disposal_guard() if owner is not None else ()
        cleanup = self._cleanup
        self._cleanup = None
        try:
            result = cleanup() if cleanup is not None else None
        except BaseException as error:
            self._finish(error)
            raise
        finally:
            Context._exit_sync_disposal_guard(guarded)
        if isawaitable(result):
            self._pending = _Completion(self._finish_cleanup(result), self._check)
            return self._pending
        self._finish()
        return None


class _ContextState(Enum):
    ACTIVE = "active"
    DISPOSING = "disposing"
    DISPOSED = "disposed"


ContextKey = str | Ref[Any]
_RefRole = Literal["any", "leaf", "container"]


class ContextPathError(KeyError):
    """Raised when a Context path cannot be resolved or changed as requested."""

    pass


class _ContextBinding(Compose[Any | _Blocked, Any]):
    """Private one-value composition backing one Context leaf."""

    __slots__ = ("_identity_scopes", "_scope_bindings")

    def __init__(self, scope_bindings: _ScopeBindings) -> None:
        def resolve(values: tuple[Any | _Blocked, ...]) -> Any:
            if not values or values[0] is _BLOCKED:
                raise LookupError("Context value is not visible.")
            return values[0]

        super().__init__(resolve)
        self._identity_scopes: dict[Hashable, set[Scope]] = {}
        self._scope_bindings = scope_bindings

    def _retain_scope(self, scope: Scope, identity: Hashable) -> None:
        scopes = self._identity_scopes.get(identity)
        if scopes is None:
            self._identity_scopes[identity] = {scope}
        else:
            if scope in scopes:
                return
            scopes.add(scope)
        bindings = self._scope_bindings.get(scope)
        if bindings is None:
            bindings = self._scope_bindings[scope] = weakref.WeakKeyDictionary()
        bindings[self] = None

    def bind(self, *scopes: Scope, identity: Hashable) -> None:
        super().bind(*scopes, identity=identity)
        for scope in scopes:
            self._retain_scope(scope, identity)

    def _identity_for(self, scope: Scope, *, create: bool) -> Hashable:
        identity = super()._identity_for(scope, create=create)
        if create:
            self._retain_scope(scope, identity)
        return identity

    def acquire_scope(self, scope: Scope) -> None:
        """Restore an indexed binding when a Scope gains its first live viewer."""
        self._retain_scope(scope, self._identity_for(scope, create=False))

    def _local_value_entry(self, scope: Scope) -> Any | None:
        try:
            identity = self._identity_for(scope, create=False)
        except LookupError:
            return None
        bucket = self._buckets.get(identity)
        if bucket is None:
            return None
        return next(
            (entry for entry in bucket.values() if entry.value is not _BLOCKED),
            None,
        )

    def has_value(self, scope: Scope, *, local: bool = False) -> bool:
        try:
            self.resolve(scope, local=local)
            return True
        except LookupError:
            return False

    def set_value(
        self,
        scope: Scope,
        value: Any,
        *,
        replaceable: bool,
    ) -> None:
        current = self._local_value_entry(scope)
        if current is not None:
            if not replaceable:
                raise ValueError("Context local value is not replaceable.")
            self._remove(current.identity, current.token)
        self._insert(scope, value, position="prepend")

    def add_value(self, scope: Scope, value: Any) -> Callable[[], None]:
        if self._local_value_entry(scope) is not None:
            raise ValueError("Context already has a local value.")
        entry = self._insert(scope, value, position="prepend")
        binding_ref: weakref.ReferenceType[_ContextBinding] | None = weakref.ref(self)
        identity = entry.identity
        token = entry.token

        def dispose() -> None:
            nonlocal binding_ref
            if binding_ref is None:
                return
            binding = binding_ref()
            if binding is not None:
                binding._remove(identity, token)
            binding_ref = None

        return dispose

    def delete_value(self, scope: Scope) -> None:
        current = self._local_value_entry(scope)
        if current is not None:
            self._remove(current.identity, current.token)

    def block(self, scope: Scope) -> None:
        identity = self._identity_for(scope, create=True)
        bucket = self._buckets.get(identity)
        if bucket is not None and any(
            entry.value is _BLOCKED for entry in bucket.values()
        ):
            return
        self._insert(scope, _BLOCKED, position="append")

    def release_scope(self, scope: Scope) -> None:
        """Release one Scope and remove its identity if no bound Scope remains."""
        try:
            identity = self._identity_for(scope, create=False)
        except LookupError:
            return
        scopes = self._identity_scopes.get(identity)
        if scopes is None:
            return
        scopes.discard(scope)
        if not scopes:
            del self._identity_scopes[identity]
            self._clear_bucket(identity)


_ContextData = weakref.WeakKeyDictionary[_RefEntry[Any], _ContextBinding]
# Saved Scopes can regain viewers without retaining withdrawn Schema bindings.
_ScopeBindings = weakref.WeakKeyDictionary[
    Scope, weakref.WeakKeyDictionary[_ContextBinding, None]
]
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
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        pass

    def flatten(self, *, local: bool = False) -> dict[Ref[Any], Any]:
        """Return the visible Context leaves as a flat Ref-to-value mapping."""
        return dict(self._flat_items(local=local))


@dataclass(frozen=True, repr=False, eq=False, init=False)
class Context(ContextElement):
    """Declared runtime data and reversible effects bound to one Scope."""

    parent: Context | None = field(init=False)
    scope: Scope = field(init=False)
    _data: _ContextData = field(init=False)
    _root: Context = field(init=False)
    _schema: Schema | None = field(init=False)
    _owned: dict[Context | _Effect, None] = field(init=False)
    _scope_viewers: dict[Scope, set[Context]] | None = field(init=False)
    _scope_bindings: _ScopeBindings = field(init=False)
    _state: _ContextState = field(init=False)
    _dispose_pending: _Completion[None] | None = field(init=False)
    _dispose_error: BaseException | None = field(init=False)
    _sync_disposal_guard_depth: int = field(init=False)

    @staticmethod
    def _set_nested_value(
        tree: _Tree,
        parts: tuple[str, ...],
        value: Any,
    ) -> None:
        current = tree
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def __init__(
        self,
        data: Mapping[ContextKey, Any] | None = None,
        *,
        schema: Schema | None = None,
        parent: Context | None = None,
        scope: Scope | None = None,
    ) -> None:
        scope_viewers: dict[Scope, set[Context]] | None
        if parent is not None:
            parent._assert_mutable()
            if schema is not None:
                raise TypeError(
                    "A child Context inherits its schema and cannot provide one "
                    "during construction."
                )
            application_root = parent.root
            root_schema = None
            root_data = application_root._data
            bound_scope = parent.scope if scope is None else scope
            scope_viewers = None
            scope_bindings = application_root._scope_bindings
        else:
            root_schema = Schema() if schema is None else schema
            root_data = weakref.WeakKeyDictionary()
            application_root = self
            bound_scope = Scope() if scope is None else scope
            scope_viewers = {}
            scope_bindings = weakref.WeakKeyDictionary()

        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "scope", bound_scope)
        object.__setattr__(self, "_data", root_data)
        object.__setattr__(self, "_root", application_root)
        object.__setattr__(self, "_schema", root_schema)
        object.__setattr__(self, "_owned", {})
        object.__setattr__(self, "_scope_viewers", scope_viewers)
        object.__setattr__(self, "_scope_bindings", scope_bindings)
        object.__setattr__(self, "_state", _ContextState.ACTIVE)
        object.__setattr__(self, "_dispose_pending", None)
        object.__setattr__(self, "_dispose_error", None)
        object.__setattr__(self, "_sync_disposal_guard_depth", 0)

        self._acquire_scope()
        if parent is not None:
            parent._owned[self] = None
        try:
            if data is not None:
                self.update(data)
        except BaseException as error:
            self._finish_dispose(error)
            raise

    def _assert_readable(self) -> None:
        if self._state is _ContextState.DISPOSED:
            raise RuntimeError("Context has been disposed.")

    def _assert_mutable(self) -> None:
        if self._state is _ContextState.DISPOSING:
            raise RuntimeError("Context is being disposed.")
        if self._state is _ContextState.DISPOSED:
            raise RuntimeError("Context has been disposed.")
        ancestor = self.parent
        while ancestor is not None:
            if ancestor._state is _ContextState.DISPOSING:
                raise RuntimeError("An ancestor Context is being disposed.")
            if ancestor._state is _ContextState.DISPOSED:
                raise RuntimeError("An ancestor Context has been disposed.")
            ancestor = ancestor.parent

    def _enter_sync_disposal_guard(self) -> tuple[Context, ...]:
        guarded: list[Context] = []
        current: Context | None = self
        while current is not None:
            object.__setattr__(
                current,
                "_sync_disposal_guard_depth",
                current._sync_disposal_guard_depth + 1,
            )
            guarded.append(current)
            current = current.parent
        return tuple(guarded)

    @staticmethod
    def _exit_sync_disposal_guard(guarded: tuple[Context, ...]) -> None:
        for context in guarded:
            object.__setattr__(
                context,
                "_sync_disposal_guard_depth",
                context._sync_disposal_guard_depth - 1,
            )

    def _assert_disposal_allowed(self) -> None:
        if self._sync_disposal_guard_depth or any(
            owner is self for owner in _DISPOSAL_CHAIN.get()
        ):
            raise RuntimeError(
                "Context disposal cannot be re-entered from effect setup or cleanup."
            )

    def _acquire_scope(self) -> None:
        viewers = cast(dict[Scope, set[Context]], self.root._scope_viewers)
        if any(self in viewers.get(scope, ()) for scope in self.scope.mro):
            raise RuntimeError("Context is already registered as a Scope viewer.")
        acquired: list[Scope] = []
        for scope in self.scope.mro:
            contexts = viewers.get(scope)
            if contexts is None:
                contexts = viewers[scope] = set()
                acquired.append(scope)
            contexts.add(self)
        try:
            for scope in acquired:
                for binding in tuple(self._scope_bindings.get(scope, ())):
                    binding.acquire_scope(scope)
        except BaseException as error:
            try:
                self._release_scope()
            except BaseException as rollback_error:
                raise error from rollback_error
            raise

    def _release_scope(self) -> None:
        viewers = cast(dict[Scope, set[Context]], self.root._scope_viewers)
        if any(self not in viewers.get(scope, ()) for scope in self.scope.mro):
            raise RuntimeError("Context is not registered as a Scope viewer.")
        expired: list[Scope] = []
        for scope in self.scope.mro:
            contexts = viewers[scope]
            contexts.remove(self)
            if not contexts:
                del viewers[scope]
                expired.append(scope)

        first_error: BaseException | None = None
        for scope in expired:
            for binding in tuple(self._scope_bindings.get(scope, ())):
                # Value finalizers may register new viewers for this Scope.
                if scope in viewers:
                    break
                try:
                    binding.release_scope(scope)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        if not viewers:
            self._data.clear()
            self._scope_bindings.clear()
        if first_error is not None:
            raise first_error

    def _forget_owned(self, owned: Context | _Effect) -> None:
        self._owned.pop(owned, None)

    def _adopt_sync_effect(self, cleanup: Callable[[], None]) -> Callable[[], None]:
        self._assert_mutable()
        effect = _Effect(self)
        effect._cleanup = cleanup
        self._owned[effect] = None
        return cast(Callable[[], None], effect.dispose)

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
        """Own setup and its cleanup, accepting immediate or awaitable results.

        Async setup is registered before it starts. Await it to obtain its early
        disposer; owner disposal also waits for setup before cleaning it up.
        Setup and cleanup cannot dispose their owner or an ancestor.
        """
        self._assert_mutable()
        effect = _Effect(self)
        self._owned[effect] = None
        guarded = self._enter_sync_disposal_guard()
        try:
            cleanup = setup()
        except BaseException:
            effect._release()
            raise
        finally:
            self._exit_sync_disposal_guard(guarded)
        if isawaitable(cleanup):
            effect._setup = _Completion(effect._finish_setup(cleanup), effect._check)
            return effect._setup
        effect._cleanup = cleanup
        return effect.dispose

    def _finish_dispose(self, error: BaseException | None) -> BaseException | None:
        self._owned.clear()
        try:
            self._release_scope()
        except BaseException as release_error:
            if error is None:
                error = release_error
        if self.parent is not None:
            self.parent._forget_owned(self)
        object.__setattr__(self, "_dispose_error", error)
        object.__setattr__(self, "_state", _ContextState.DISPOSED)
        return error

    def dispose(self) -> None | Awaitable[None]:
        """Release ownership in LIFO order, returning any unfinished cleanup.

        Synchronous cleanup runs immediately. Await asynchronous completion;
        once awaited, waiter cancellation does not cancel cleanup. Repeated
        calls share that completion and reproduce its terminal failure.
        """
        self._assert_disposal_allowed()
        if self._dispose_pending is not None:
            return self._dispose_pending
        if self._state is _ContextState.DISPOSED:
            if self._dispose_error is not None:
                raise self._dispose_error
            return None
        if self._state is _ContextState.DISPOSING:
            raise RuntimeError("Context disposal is already in progress.")
        object.__setattr__(self, "_state", _ContextState.DISPOSING)
        owned = iter(reversed(tuple(self._owned)))
        first_error: BaseException | None = None
        for item in owned:
            try:
                result = item.dispose()
            except BaseException as error:
                if first_error is None:
                    first_error = error
                continue
            if isawaitable(result):
                pending = _Completion(
                    self._continue_dispose(owned, result, first_error),
                    self._assert_disposal_allowed,
                )
                object.__setattr__(self, "_dispose_pending", pending)
                return pending
        first_error = self._finish_dispose(first_error)
        if first_error is not None:
            raise first_error
        return None

    def adispose(self) -> Awaitable[None]:
        """Dispose with an always-awaitable result, preserving immediate cleanup.

        Synchronous cleanup and errors occur during this call. Await the result
        to finish disposal with the same cancellation and failure guarantees.
        """
        return resolve(self.dispose())

    async def _continue_dispose(
        self,
        owned: Iterator[Context | _Effect],
        pending: Awaitable[None],
        first_error: BaseException | None,
    ) -> None:
        token = _DISPOSAL_CHAIN.set((*_DISPOSAL_CHAIN.get(), self))
        try:
            try:
                await pending
            except BaseException as error:
                if first_error is None:
                    first_error = error
            for item in owned:
                try:
                    await resolve(item.dispose())
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        finally:
            try:
                first_error = self._finish_dispose(first_error)
            finally:
                _DISPOSAL_CHAIN.reset(token)
        if first_error is not None:
            raise first_error

    @property
    def root(self) -> Context:
        """Return the application root shared by this Context hierarchy."""
        return self._root

    @property
    def schema(self) -> Schema:
        """Return the live Schema shared by this Context hierarchy."""
        return cast(Schema, self.root._schema)

    def declare(
        self,
        declarations: Schema | Mapping[str, Any],
    ) -> Callable[[], None]:
        """Declare shared Schema paths owned by this Context."""
        return self.effect(lambda: self.schema.declare(declarations))

    def _validate_entry(
        self,
        key: ContextKey,
        *,
        role: _RefRole = "any",
    ) -> _RefEntry[Any]:
        self._assert_readable()
        path = key if isinstance(key, str) else key.path

        try:
            entry = self.schema._resolve_entry(path)
        except (KeyError, ValueError) as error:
            if isinstance(key, Ref):
                raise ContextPathError(
                    f"Ref path {path!r} is not declared by this Context."
                ) from error
            raise ContextPathError(str(error)) from error

        kind: Literal["leaf", "container"]
        if isinstance(entry.config, _RefLeafConfig):
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
        return self._validate_entry(key, role=role).ref

    def fork(self, *, scope: Scope | None = None) -> Context:
        """Create an owned child sharing this Context's Scope by default."""
        self._assert_mutable()
        return type(self)(parent=self, scope=scope)

    def _fork_for_auto(self) -> Context:
        return self.fork(scope=self.scope.fork())

    def isolate(
        self,
        *refs: ContextKey,
        identity: Hashable | None = None,
    ) -> Context:
        """Create a child that blocks inherited values for selected leaves."""
        self._assert_mutable()
        entries = tuple(self._validate_entry(ref, role="leaf") for ref in refs)
        if identity is not None:
            Compose._validate_identity(identity)
        child = self.fork(scope=self.scope.fork())
        try:
            for entry in entries:
                binding = child._binding(entry, create=True)
                if identity is not None:
                    binding.bind(child.scope, identity=identity)
                binding.block(child.scope)
        except BaseException:
            child.dispose()
            raise
        return child

    @overload
    def _binding(
        self,
        entry: _RefEntry[Any],
        *,
        create: Literal[True],
    ) -> _ContextBinding: ...

    @overload
    def _binding(
        self,
        entry: _RefEntry[Any],
        *,
        create: Literal[False],
    ) -> _ContextBinding | None: ...

    def _binding(
        self,
        entry: _RefEntry[Any],
        *,
        create: bool,
    ) -> _ContextBinding | None:
        binding = self._data.get(entry)
        if binding is None and create:
            binding = _ContextBinding(self._scope_bindings)
            self._data[entry] = binding
        return binding

    def _leaf_value(self, entry: _RefEntry[Any], *, local: bool) -> Any:
        binding = self._binding(entry, create=False)
        if binding is None:
            raise ContextPathError(entry.ref.path)
        try:
            return binding.resolve(self.scope, local=local)
        except LookupError as error:
            raise ContextPathError(entry.ref.path) from error

    def _leaf_items(
        self,
        parts: tuple[str, ...],
        *,
        local: bool,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        for entry in self.schema._leaf_entries(parts):
            try:
                value = self._leaf_value(entry, local=local)
            except ContextPathError:
                continue
            yield entry.ref, value

    def _entry_value(self, entry: _RefEntry[Any], *, local: bool) -> Any:
        if isinstance(entry.config, _RefLeafConfig):
            return self._leaf_value(entry, local=local)
        if not any(self._leaf_items(entry.ref.parts, local=local)):
            raise ContextPathError(entry.ref.path)
        return ContextView(self, entry.ref.parts)

    def _flat_items(
        self,
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        self._assert_readable()
        return self._leaf_items((), local=local)

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        self._assert_readable()
        refs, treedef = CTX_EVAL_ENGINE.flatten(ref_tree)
        entries = [self._validate_entry(ref) for ref in refs]
        values = [self._entry_value(entry, local=local) for entry in entries]
        return CTX_EVAL_ENGINE.unflatten(treedef, values)

    # --- Read Operations ---
    def get(
        self,
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        entry = self._validate_entry(ref)
        try:
            return self._entry_value(entry, local=local)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        entry = self._validate_entry(ref)
        if isinstance(entry.config, _RefLeafConfig):
            try:
                self._leaf_value(entry, local=local)
                return True
            except ContextPathError:
                return False
        return any(self._leaf_items(entry.ref.parts, local=local))

    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        self._assert_readable()
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        visible = tuple(self._leaf_items(parts, local=local))
        if ref is not None and not visible:
            raise ContextPathError(".".join(parts))

        result: list[str] = []
        for name in self.schema._child_names(parts):
            child_parts = (*parts, name)
            if any(
                leaf.parts[: len(child_parts)] == child_parts for leaf, _ in visible
            ):
                result.append(name)
        return tuple(result)

    def _snapshot_tree(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        self._assert_readable()
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        visible = tuple(self._leaf_items(parts, local=local))
        if ref is not None and not visible:
            raise ContextPathError(".".join(parts))

        result: _Tree = {}
        for leaf, value in visible:
            self._set_nested_value(result, leaf.parts[len(parts) :], value)
        return result

    def to_dict(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._snapshot_tree(ref, local=local)

    # --- Write Operations ---
    def _set_entry(self, entry: _RefEntry[Any], value: Any) -> None:
        config = cast(_RefLeafConfig[Any], entry.config)
        try:
            self._binding(entry, create=True).set_value(
                self.scope, value, replaceable=config.replaceable
            )
        except ValueError as error:
            raise ContextPathError(
                f"Cannot replace non-replaceable local path {entry.ref.path!r}; "
                "delete it or use a Context bound to a child Scope."
            ) from error

    def _delete_leaf(self, entry: _RefEntry[Any]) -> None:
        binding = self._binding(entry, create=False)
        if binding is not None:
            binding.delete_value(self.scope)

    def set(self, ref: ContextKey, value: _T) -> None:
        """Set one local binding."""
        self._assert_mutable()
        entry = self._validate_entry(ref, role="leaf")
        self._set_entry(entry, value)

    def delete(self, ref: ContextKey) -> None:
        """Delete a local leaf value or the local values under a container."""
        self._assert_mutable()
        entry = self._validate_entry(ref)
        if isinstance(entry.config, _RefLeafConfig):
            self._delete_leaf(entry)
        else:
            for leaf in tuple(self.schema._leaf_entries(entry.ref.parts)):
                self._delete_leaf(leaf)

    def update(self, updates: Mapping[ContextKey, Any]) -> None:
        """Set local bindings after validating every path and replacement policy.

        Preflight failures leave bindings unchanged. Failures during application
        of the writes do not trigger rollback.
        """
        self._assert_mutable()
        entries = {
            self._validate_entry(ref, role="leaf"): value
            for ref, value in updates.items()
        }
        for entry in entries:
            config = cast(_RefLeafConfig[Any], entry.config)
            if config.replaceable:
                continue
            binding = self._binding(entry, create=False)
            if (
                binding is not None
                and binding._local_value_entry(self.scope) is not None
            ):
                raise ContextPathError(
                    f"Cannot replace non-replaceable local path {entry.ref.path!r}; "
                    "delete it or use a Context bound to a child Scope."
                )
        for entry, value in entries.items():
            self._set_entry(entry, value)

    def drop(self, refs: Iterable[ContextKey]) -> None:
        """Delete local paths after validating all inputs and collecting leaves.

        Preflight failures leave bindings unchanged. Failures during application
        of the deletions do not trigger rollback.
        """
        self._assert_mutable()
        entries = {self._validate_entry(ref) for ref in refs}
        leaves: set[_RefEntry[Any]] = set()
        for entry in entries:
            if isinstance(entry.config, _RefLeafConfig):
                leaves.add(entry)
            else:
                leaves.update(self.schema._leaf_entries(entry.ref.parts))
        for entry in leaves:
            self._delete_leaf(entry)

    def add(self, ref: ContextKey, value: _T) -> Callable[[], None]:
        """Add one local binding owned by this Context."""
        self._assert_mutable()
        entry = self._validate_entry(ref, role="leaf")
        binding = self._binding(entry, create=True)
        if binding.has_value(self.scope, local=True):
            raise ContextPathError(
                f"Cannot add existing local path {entry.ref.path!r}."
            )
        try:
            cleanup = binding.add_value(self.scope, value)
        except ValueError as error:
            raise ContextPathError(
                f"Cannot add existing local path {entry.ref.path!r}."
            ) from error
        try:
            return self._adopt_sync_effect(cleanup)
        except BaseException:
            cleanup()
            raise

    def update_tree(self, ref_tree: Any, value_tree: Any) -> None:
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
        self.update(updates)


@dataclass(frozen=True, repr=False)
class ContextView(ContextElement):
    """
    Read-only view of a subtree within a Context.
    """

    _context: Context
    _parts: tuple[str, ...]

    def _adjust_ref(self, ref: ContextKey | None) -> Ref[Any]:
        if ref is None:
            return self._context._validate_ref(".".join(self._parts))
        if isinstance(ref, str):
            relative_parts = Ref._split_path(ref)
            return self._context._validate_ref(
                ".".join((*self._parts, *relative_parts))
            )
        if ref.parts[: len(self._parts)] != self._parts:
            raise ContextPathError(
                f"Ref path {ref.path!r} is outside this ContextView."
            )
        return self._context._validate_ref(ref)

    def _adjust_ref_tree(self, ref_tree: Any) -> Any:
        def adjust(obj):
            if isinstance(obj, (str, Ref)):
                return self._adjust_ref(obj)
            return obj

        return CTX_EVAL_ENGINE.map(adjust, ref_tree)

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        return self._context.extract(
            self._adjust_ref_tree(ref_tree),
            local=local,
        )

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
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        entry = self._context._validate_entry(
            ".".join(self._parts),
            role="container",
        )
        items = tuple(self._context._leaf_items(entry.ref.parts, local=local))
        if not items:
            raise ContextPathError(entry.ref.path)
        return items

    def to_dict(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref), local=local)


from .tree import CTX_EVAL_ENGINE
