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
from collections.abc import Awaitable, Callable, Generator
from contextvars import ContextVar
from dataclasses import InitVar, dataclass, field
from enum import Enum
from inspect import isawaitable
from typing import Any, Generic, NoReturn, TypeVar, cast, overload

from slyme.utils.exception import exception_group
from slyme.utils.execution import await_result, continuation

__all__ = ["Lifecycle"]

_T = TypeVar("_T")
_Cleanup = Callable[[], None | Awaitable[None]]
_Disposer = Callable[[], None | Awaitable[None]]
_DISPOSAL_CHAIN: ContextVar[tuple[object, ...]] = ContextVar(
    "slyme_lifecycle_disposal_chain",
    default=(),
)


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

    def __init__(self, owner: Lifecycle) -> None:
        self.owner: Lifecycle | None = owner
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
            raise RuntimeError(
                "Lifecycle effect cannot await its own setup or cleanup."
            )

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
            Lifecycle._exit_sync_disposal_guard(guarded)
        return self._await_cleanup(result) if isawaitable(result) else None

    def dispose(self) -> None | Awaitable[None]:
        self._check()
        if self._pending is not None:
            return self._pending
        if self._disposing:
            raise RuntimeError("Lifecycle effect disposal cannot be re-entered.")
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


class _LifecycleState(Enum):
    ACTIVE = "active"
    CLOSING = "closing"
    DISPOSING = "disposing"
    DISPOSED = "disposed"


@dataclass(frozen=True, repr=False, eq=False)
class Lifecycle:
    """Own effects and child lifetimes, independently of data and visibility.

    The synchronous finalizer runs after all owned cleanup, even on failure.
    It is not an effect and cannot be revoked independently of this lifetime.
    """

    parent: Lifecycle | None = None
    finalize: InitVar[Callable[[], None] | None] = field(default=None, kw_only=True)
    _finalizer: Callable[[], None] | None = field(default=None, init=False)
    _owned: dict[Lifecycle | _Effect, None] = field(default_factory=dict, init=False)
    _state: _LifecycleState = field(default=_LifecycleState.ACTIVE, init=False)
    _dispose_pending: _Completion[None] | None = field(default=None, init=False)
    _dispose_error: BaseException | None = field(default=None, init=False)
    _sync_disposal_guard_depth: int = field(default=0, init=False)

    def __post_init__(self, finalize: Callable[[], None] | None) -> None:
        if self.parent is not None:
            self.parent.assert_active()
            self.parent._owned[self] = None
        object.__setattr__(self, "_finalizer", finalize)

    def assert_readable(self) -> None:
        if self._state is _LifecycleState.DISPOSED:
            raise RuntimeError("Lifecycle has been disposed.")

    def assert_active(self) -> None:
        if self._state is _LifecycleState.ACTIVE:
            return
        if self._state is _LifecycleState.DISPOSED:
            raise RuntimeError("Lifecycle has been disposed.")
        if self._state is _LifecycleState.CLOSING:
            raise RuntimeError("An ancestor Lifecycle is being disposed.")
        raise RuntimeError("Lifecycle is being disposed.")

    def _close_subtree(self) -> None:
        """Forbid mutations throughout the ownership subtree before cleanup."""
        if self._state is not _LifecycleState.ACTIVE:
            return
        pending = [self]
        while pending:
            lifecycle = pending.pop()
            object.__setattr__(lifecycle, "_state", _LifecycleState.CLOSING)
            for child in lifecycle._owned:
                if (
                    isinstance(child, Lifecycle)
                    and child._state is _LifecycleState.ACTIVE
                ):
                    pending.append(child)

    def _enter_sync_disposal_guard(self) -> tuple[Lifecycle, ...]:
        guarded: list[Lifecycle] = []
        current: Lifecycle | None = self
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
    def _exit_sync_disposal_guard(guarded: tuple[Lifecycle, ...]) -> None:
        for lifecycle in guarded:
            object.__setattr__(
                lifecycle,
                "_sync_disposal_guard_depth",
                lifecycle._sync_disposal_guard_depth - 1,
            )

    def _assert_disposal_allowed(self) -> None:
        if self._sync_disposal_guard_depth or any(
            owner is self for owner in _DISPOSAL_CHAIN.get()
        ):
            raise RuntimeError(
                "Lifecycle disposal cannot be re-entered from effect setup or cleanup."
            )

    def _forget_owned(self, owned: Lifecycle | _Effect) -> None:
        self._owned.pop(owned, None)

    @overload
    def effect(self, setup: Callable[[], Callable[[], None]]) -> Callable[[], None]: ...
    @overload
    def effect(self, setup: Callable[[], _Cleanup]) -> _Disposer: ...
    @overload
    def effect(
        self, setup: Callable[[], Awaitable[_Cleanup]]
    ) -> Awaitable[_Disposer]: ...

    @overload
    def effect(
        self, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]
    ) -> _Disposer | Awaitable[_Disposer]: ...
    def effect(
        self, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]
    ) -> _Disposer | Awaitable[_Disposer]:
        """Own setup and its cleanup, accepting immediate or awaitable results.

        Async setup is registered before it starts. Await it to obtain its early
        disposer; owner disposal also waits for setup before cleaning it up.
        Setup and cleanup cannot dispose their owner or an ancestor.
        """
        self.assert_active()
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
        finalizer = self._finalizer
        object.__setattr__(self, "_finalizer", None)
        try:
            if finalizer is not None:
                finalizer()
        except BaseException as release_error:
            if error is None:
                error = release_error
            else:
                error.__cause__ = release_error
        if self.parent is not None:
            self.parent._forget_owned(self)
        object.__setattr__(self, "_dispose_error", error)
        object.__setattr__(self, "_state", _LifecycleState.DISPOSED)
        return error

    def dispose(self) -> None | Awaitable[None]:
        """Release ownership in LIFO order, returning any unfinished cleanup.

        Synchronous cleanup runs immediately. Await asynchronous completion;
        once awaited, waiter cancellation does not cancel cleanup. Repeated
        calls share that completion and reproduce its terminal failure.
        Owned cleanup failures are grouped in cleanup execution order.
        Mutations in the entire ownership subtree are forbidden before the
        first cleanup; each Lifecycle remains readable until its own release.
        """
        self._assert_disposal_allowed()
        if self._dispose_pending is not None:
            return self._dispose_pending
        if self._state is _LifecycleState.DISPOSED:
            if self._dispose_error is not None:
                raise self._dispose_error
            return None
        if self._state is _LifecycleState.DISPOSING:
            raise RuntimeError("Lifecycle disposal is already in progress.")
        self._close_subtree()
        object.__setattr__(self, "_state", _LifecycleState.DISPOSING)
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
                    raise exception_group("Lifecycle dispose failed", errors)
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
