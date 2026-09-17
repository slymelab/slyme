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
    Mapping,
)
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from inspect import isawaitable
from typing import Any, Generic, Literal, NoReturn, TypeVar, cast, overload

from slyme.utils.exception import exception_group
from slyme.utils.execution import await_result, continuation

from .compose import Compose
from .ref import Ref
from .schema import RefEntry, RefLeafConfig, Schema, _Declaration
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
                await_result(cast(Awaitable[_T], self._operation))
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

    def _fail_dispose(self, error: BaseException) -> NoReturn:
        self._finish(error)
        raise error

    async def _await_cleanup(self, pending: Awaitable[None]) -> None:
        token = _DISPOSAL_CHAIN.set(self._chain())
        try:
            await pending
        finally:
            _DISPOSAL_CHAIN.reset(token)

    def _dispose_cleanup(self) -> None | Awaitable[None]:
        owner = self.owner
        guarded = owner._enter_sync_disposal_guard() if owner is not None else ()
        cleanup = self._cleanup
        self._cleanup = None

        @continuation
        def execute() -> Generator[Any, Any, None]:
            try:
                if cleanup is not None:
                    yield cleanup()
            except BaseException as error:
                self._fail_dispose(error)
            self._finish()

        try:
            result = execute()
        finally:
            Context._exit_sync_disposal_guard(guarded)
        return self._await_cleanup(result) if isawaitable(result) else None

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

        @continuation
        def execute() -> Generator[Any, Any, None]:
            try:
                yield self._setup
            except BaseException as error:
                self._fail_dispose(error)
            yield self._dispose_cleanup()

        result = execute()
        if isawaitable(result):
            self._pending = _Completion(result, self._check)
            return self._pending
        return None


class _ContextState(Enum):
    ACTIVE = "active"
    CLOSING = "closing"
    DISPOSING = "disposing"
    DISPOSED = "disposed"


ContextKey = str | Ref[Any]
_RefRole = Literal["any", "leaf", "container"]


class ContextPathError(KeyError):
    """Raised when a Context path cannot be resolved or changed as requested."""

    pass


class _ContextBinding:
    """One current value and an optional inheritance barrier per identity."""

    __slots__ = (
        "_path",
        "_values",
        "_blocked",
        "_scope_identities",
        "_identity_scopes",
        "_scope_bindings",
        "__weakref__",
    )

    def __init__(self, path: str, scope_bindings: _ScopeBindings) -> None:
        self._path = path
        self._values: dict[Hashable, tuple[object, Any]] = {}
        self._blocked: set[Hashable] = set()
        self._scope_identities: weakref.WeakKeyDictionary[Scope, Hashable] = (
            weakref.WeakKeyDictionary()
        )
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
        paths = self._scope_bindings.get(scope)
        if paths is None:
            paths = self._scope_bindings[scope] = set()
        paths.add(self._path)

    def bind(self, *scopes: Scope, identity: Hashable) -> None:
        Compose._bind_identities(self._scope_identities, scopes, identity=identity)
        for scope in scopes:
            self._retain_scope(scope, identity)

    def _identity_for(self, scope: Scope, *, create: bool) -> Hashable:
        identity = Compose._identity_for(self._scope_identities, scope, create=create)
        if create:
            self._retain_scope(scope, identity)
        return identity

    def acquire_scope(self, scope: Scope) -> None:
        """Restore an indexed binding when a Scope gains its first live viewer."""
        self._retain_scope(scope, self._identity_for(scope, create=False))

    def _local_value_entry(self, scope: Scope) -> tuple[object, Any] | None:
        try:
            identity = self._identity_for(scope, create=False)
        except LookupError:
            return None
        return self._values.get(identity)

    def resolve(self, scope: Scope, *, local: bool = False) -> Any:
        values: list[Any] = []
        for identity in Compose._scoped_identities(
            self._scope_identities, scope, local=local
        ):
            entry = self._values.get(identity)
            if entry is not None:
                values.append(entry[1])
            elif identity in self._blocked:
                values.append(_BLOCKED)
        if not values or values[0] is _BLOCKED:
            raise LookupError("Context value is not visible.")
        return values[0]

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
        if current is not None and not replaceable:
            raise ValueError("Context local value is not replaceable.")
        identity = self._identity_for(scope, create=True)
        self._values[identity] = (object(), value)

    def add_value(self, scope: Scope, value: Any) -> Callable[[], None]:
        if self._local_value_entry(scope) is not None:
            raise ValueError("Context already has a local value.")
        identity = self._identity_for(scope, create=True)
        token = object()
        self._values[identity] = (token, value)
        binding_ref: weakref.ReferenceType[_ContextBinding] | None = weakref.ref(self)

        def dispose() -> None:
            nonlocal binding_ref
            if binding_ref is None:
                return
            binding = binding_ref()
            if binding is not None:
                current = binding._values.get(identity)
                if current is not None and current[0] is token:
                    del binding._values[identity]
                del current
            binding_ref = None

        return dispose

    def delete_value(self, scope: Scope) -> None:
        try:
            identity = self._identity_for(scope, create=False)
        except LookupError:
            return
        self._values.pop(identity, None)

    def block(self, scope: Scope) -> None:
        self._blocked.add(self._identity_for(scope, create=True))

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
            # Remove the barrier before value finalizers can reuse this identity.
            self._blocked.discard(identity)
            self._values.pop(identity, None)


_ContextData = weakref.WeakKeyDictionary[RefEntry[Any], _ContextBinding]
_ScopeBindings = dict[Scope, set[str]]
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
    _seen_scopes: weakref.WeakSet[Scope] = field(init=False)
    _state: _ContextState = field(init=False)
    _dispose_pending: _Completion[None] | None = field(init=False)
    _dispose_error: BaseException | None = field(init=False)
    _sync_disposal_guard_depth: int = field(init=False)

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
            seen_scopes = application_root._seen_scopes
        else:
            root_schema = Schema() if schema is None else schema
            root_data = weakref.WeakKeyDictionary()
            application_root = self
            bound_scope = Scope() if scope is None else scope
            scope_viewers = {}
            scope_bindings = {}
            seen_scopes = weakref.WeakSet()

        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "scope", bound_scope)
        object.__setattr__(self, "_data", root_data)
        object.__setattr__(self, "_root", application_root)
        object.__setattr__(self, "_schema", root_schema)
        object.__setattr__(self, "_owned", {})
        object.__setattr__(self, "_scope_viewers", scope_viewers)
        object.__setattr__(self, "_scope_bindings", scope_bindings)
        object.__setattr__(self, "_seen_scopes", seen_scopes)
        object.__setattr__(self, "_state", _ContextState.ACTIVE)
        object.__setattr__(self, "_dispose_pending", None)
        object.__setattr__(self, "_dispose_error", None)
        object.__setattr__(self, "_sync_disposal_guard_depth", 0)

        self._acquire_scope()
        if root_schema is not None:
            root_schema._contexts.add(self)
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
        if self._state is _ContextState.ACTIVE:
            return
        if self._state is _ContextState.DISPOSED:
            raise RuntimeError("Context has been disposed.")
        if self._state is _ContextState.CLOSING:
            raise RuntimeError("An ancestor Context is being disposed.")
        raise RuntimeError("Context is being disposed.")

    def _close_subtree(self) -> None:
        """Forbid mutations throughout the ownership subtree before cleanup."""
        if self._state is not _ContextState.ACTIVE:
            return
        pending = [self]
        while pending:
            context = pending.pop()
            object.__setattr__(context, "_state", _ContextState.CLOSING)
            for child in context._owned:
                if isinstance(child, Context) and child._state is _ContextState.ACTIVE:
                    pending.append(child)

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
                if scope not in self._seen_scopes:
                    self._seen_scopes.add(scope)
                    continue
                # Active indexes end with their viewers; saved Scopes retain
                # their immutable identities and restore ownership on reuse.
                for entry in tuple(self.schema._entries.values()):
                    binding = self._data.get(entry)
                    if binding is not None and scope in binding._scope_identities:
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
            for path in tuple(self._scope_bindings.get(scope, ())):
                # Value finalizers may register new viewers for this Scope.
                if scope in viewers:
                    break
                entry = self.schema._entries.get(path)
                if entry is None:
                    continue
                binding = self._data.get(entry)
                if binding is None:
                    continue
                try:
                    binding.release_scope(scope)
                except BaseException as error:
                    if first_error is None:
                        first_error = error
            if scope not in viewers:
                self._scope_bindings.pop(scope, None)
        if not viewers:
            self._data.clear()
            self._scope_bindings.clear()
        if first_error is not None:
            raise first_error

    def _forget_owned(self, owned: Context | _Effect) -> None:
        self._owned.pop(owned, None)

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
            else:
                error.__cause__ = release_error
        if self.parent is not None:
            self.parent._forget_owned(self)
        else:
            self.schema._contexts.discard(self)
        object.__setattr__(self, "_dispose_error", error)
        object.__setattr__(self, "_state", _ContextState.DISPOSED)
        return error

    def dispose(self) -> None | Awaitable[None]:
        """Release ownership in LIFO order, returning any unfinished cleanup.

        Synchronous cleanup runs immediately. Await asynchronous completion;
        once awaited, waiter cancellation does not cancel cleanup. Repeated
        calls share that completion and reproduce its terminal failure.
        Owned cleanup failures are grouped in cleanup execution order.
        Mutations in the entire ownership subtree are forbidden before the
        first cleanup; each Context remains readable until its own release.
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
        self._close_subtree()
        object.__setattr__(self, "_state", _ContextState.DISPOSING)
        owned = reversed(tuple(self._owned))

        def finish(error: BaseException | None = None) -> None:
            error = self._finish_dispose(error)
            if error is not None:
                raise error

        @continuation
        def execute() -> Generator[Any, Any, None]:
            errors = []
            try:
                for item in owned:
                    try:
                        yield item.dispose()
                    except BaseException as e:
                        errors.append(e)
                if errors:
                    raise exception_group("Context dispose failed", errors)
            except BaseException as error:
                finish(error)
            else:
                finish()

        result = execute()
        if isawaitable(result):
            pending = _Completion(
                self._await_dispose(result), self._assert_disposal_allowed
            )
            object.__setattr__(self, "_dispose_pending", pending)
            return pending
        return None

    def adispose(self) -> Awaitable[None]:
        """Dispose with an always-awaitable result, preserving immediate cleanup.

        Synchronous cleanup and errors occur during this call. Await the result
        to finish disposal with the same cancellation and failure guarantees.
        """
        return await_result(self.dispose())

    async def _await_dispose(self, pending: Awaitable[None]) -> None:
        token = _DISPOSAL_CHAIN.set((*_DISPOSAL_CHAIN.get(), self))
        try:
            await pending
        finally:
            _DISPOSAL_CHAIN.reset(token)

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
        declaration: Schema | _Declaration,
    ) -> Callable[[], None]:
        """Declare shared Schema paths owned by this Context."""
        return self.effect(lambda: self.schema.declare(declaration))

    def _validate_entry(
        self,
        key: ContextKey,
        *,
        role: _RefRole = "any",
    ) -> RefEntry[Any]:
        self._assert_readable()
        path = key if isinstance(key, str) else key.path

        try:
            entry = self.schema.resolve_entry(path)
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
        return self._validate_entry(key, role=role).ref

    def fork(self, *, scope: Scope | None = None) -> Context:
        """Create an owned child sharing this Context's Scope by default."""
        self._assert_mutable()
        return type(self)(parent=self, scope=scope)

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
            binding = _ContextBinding(entry.ref.path, self._scope_bindings)
            self._data[entry] = binding
        return binding

    def _remove_binding(self, entry: RefEntry[Any]) -> None:
        """Withdraw one Schema definition from this application's active index."""
        binding = self._data.pop(entry, None)
        if binding is None:
            return
        current = self.schema._entries.get(entry.ref.path)
        replacement = self._data.get(current) if current is not None else None
        for scopes in binding._identity_scopes.values():
            for scope in scopes:
                # Another application's cleanup may have redeclared this path.
                if replacement is not None and scope in replacement._scope_identities:
                    continue
                paths = self._scope_bindings.get(scope)
                if paths is not None:
                    paths.discard(entry.ref.path)
                    if not paths:
                        del self._scope_bindings[scope]
        binding._identity_scopes.clear()
        binding._blocked.clear()
        binding._values.clear()

    def _leaf_value(self, entry: RefEntry[Any], *, local: bool) -> Any:
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

    def _entry_value(self, entry: RefEntry[Any], *, local: bool) -> Any:
        if isinstance(entry.config, RefLeafConfig):
            return self._leaf_value(entry, local=local)
        if entry.ref.parts and not any(self._leaf_items(entry.ref.parts, local=local)):
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
        """Read a value or container view; the empty path always has a root view."""
        entry = self._validate_entry(ref)
        try:
            return self._entry_value(entry, local=local)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        entry = self._validate_entry(ref)
        if isinstance(entry.config, RefLeafConfig):
            try:
                self._leaf_value(entry, local=local)
                return True
            except ContextPathError:
                return False
        return not entry.ref.parts or any(
            self._leaf_items(entry.ref.parts, local=local)
        )

    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        self._assert_readable()
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        visible = tuple(self._leaf_items(parts, local=local))
        if parts and not visible:
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
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._snapshot_tree(ref, local=local)

    # --- Write Operations ---
    def _set_entry(self, entry: RefEntry[Any], value: Any) -> None:
        config = cast(RefLeafConfig[Any], entry.config)
        try:
            self._binding(entry, create=True).set_value(
                self.scope, value, replaceable=config.replaceable
            )
        except ValueError as error:
            raise ContextPathError(
                f"Cannot replace non-replaceable local path {entry.ref.path!r}; "
                "delete it or use a Context bound to a child Scope."
            ) from error

    def _delete_leaf(self, entry: RefEntry[Any]) -> None:
        binding = self._binding(entry, create=False)
        if binding is not None:
            binding.delete_value(self.scope)

    def set(self, ref: ContextKey, value: _T) -> None:
        """Set one local binding."""
        self._assert_mutable()
        entry = self._validate_entry(ref, role="leaf")
        self._set_entry(entry, value)

    def delete(self, ref: ContextKey) -> None:
        """Delete local values under a path; the empty path covers all leaves.

        Deletion changes only the identity bound to this Scope for each leaf.
        Schema declarations, inheritance barriers, and effects remain intact.
        """
        self._assert_mutable()
        entry = self._validate_entry(ref)
        if isinstance(entry.config, RefLeafConfig):
            self._delete_leaf(entry)
        else:
            leaves = {
                leaf.ref.path: leaf
                for leaf in self.schema._leaf_entries(entry.ref.parts)
            }
            for path in leaves.keys() & self._scope_bindings.get(self.scope, set()):
                self._delete_leaf(leaves[path])

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
            config = cast(RefLeafConfig[Any], entry.config)
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
        leaves: dict[str, RefEntry[Any]] = {}
        for entry in entries:
            if isinstance(entry.config, RefLeafConfig):
                leaves[entry.ref.path] = entry
            else:
                leaves.update(
                    (leaf.ref.path, leaf)
                    for leaf in self.schema._leaf_entries(entry.ref.parts)
                )
        for path in leaves.keys() & self._scope_bindings.get(self.scope, set()):
            self._delete_leaf(leaves[path])

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
            self._assert_mutable()
            effect = _Effect(self)
            effect._cleanup = cleanup
            self._owned[effect] = None
            return cast(Callable[[], None], effect.dispose)
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
        if entry.ref.parts and not items:
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
