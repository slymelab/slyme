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
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, overload

from slyme.utils.exception import exception_group
from slyme.utils.execution import SharedAwaitable, await_result, continuation, once

if TYPE_CHECKING:
    from .core import Context

__all__ = ["Lifecycle"]

_T = TypeVar("_T")
_Cleanup = Callable[[], None | Awaitable[None]]
_Disposer = Callable[[], None | Awaitable[None]]
_DISPOSAL_CHAIN: ContextVar[tuple[object, ...]] = ContextVar(
    "slyme_lifecycle_disposal_chain",
    default=(),
)


class _GuardedAwaitable(Generic[_T]):
    """Check each waiter before delegating to a shared completion."""

    __slots__ = ("_operation", "_check")

    def __init__(self, operation: Awaitable[_T], check: Callable[[], None]) -> None:
        self._operation = operation
        self._check = check

    def __await__(self) -> Generator[Any, None, _T]:
        self._check()
        return self._operation.__await__()


class _Effect:
    """Own setup and cleanup as one registration with a repeatable disposer."""

    __slots__ = ("owner", "_cleanup", "_setup", "dispose")

    def __init__(self, owner: Lifecycle) -> None:
        self.owner: Lifecycle | None = owner
        self._cleanup: _Cleanup | None = None
        self._setup: _GuardedAwaitable[_Disposer] | None = None
        self.dispose = Lifecycle._disposer(self._dispose, self._check)

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
            parent = owner.ctx.parent
            owner = None if parent is None else parent._lifecycle
        return (*_DISPOSAL_CHAIN.get(), *owners)

    def _check(self) -> None:
        if self in _DISPOSAL_CHAIN.get():
            raise RuntimeError(
                "Lifecycle effect cannot await its own setup or cleanup."
            )

    async def _finish_setup(self, setup: Awaitable[_Cleanup]) -> _Disposer:
        try:
            self._cleanup = await setup
            return self.dispose
        except BaseException:
            self._release()
            raise

    async def _await(self, pending: Awaitable[_T]) -> _T:
        token = _DISPOSAL_CHAIN.set(self._chain())
        try:
            return await pending
        finally:
            _DISPOSAL_CHAIN.reset(token)

    def _invoke(self, callback: Callable[[], _T | Awaitable[_T]]) -> _T | Awaitable[_T]:
        owner = self.owner
        guarded = owner._enter_sync_disposal_guard() if owner is not None else ()
        try:
            result = callback()
        finally:
            Lifecycle._exit_sync_disposal_guard(guarded)
        return self._await(result) if isawaitable(result) else result

    @continuation
    def _dispose(self) -> Generator[Any, Any, None]:
        try:
            yield self._setup
            cleanup, self._cleanup = self._cleanup, None
            if cleanup is not None:
                yield self._invoke(cleanup)
        finally:
            self._cleanup = None
            self._release()


@continuation
def _sequential(effects: Sequence[_Effect]) -> Generator[Any, Any, None]:
    errors = []
    for effect in effects:
        try:
            yield effect.dispose()
        except BaseException as error:
            errors.append(error)
    if errors:
        raise exception_group("Lifecycle dispose failed", errors)


@continuation
def _batch(effects: Sequence[_Effect]) -> Generator[Any, Any, None]:
    errors: list[tuple[int, BaseException]] = []
    pending: list[Awaitable[None]] = []

    @continuation
    def dispose(index: int, effect: _Effect) -> Generator[Any, Any, None]:
        try:
            yield effect.dispose()
        except BaseException as error:
            errors.append((index, error))

    for index, effect in enumerate(effects):
        result = dispose(index, effect)
        if isawaitable(result):
            pending.append(result)

    if pending:

        async def gather() -> None:
            await asyncio.gather(*pending)

        yield gather()
    if errors:
        errors.sort(key=lambda item: item[0])
        raise exception_group(
            "Lifecycle dispose failed", [error for _, error in errors]
        )


class _LifecycleState(Enum):
    ACTIVE = "active"
    CLOSING = "closing"
    DISPOSING = "disposing"
    DISPOSED = "disposed"


@dataclass(frozen=True, repr=False, eq=False)
class Lifecycle:
    """Manage one Context's effects, disposal mode, and disposal state.

    Ownership ancestry comes from Context. Child disposal is an owned effect;
    dispose_mode selects sequential or batch cleanup. Context data is released
    after all effects finish, including on failure.
    """

    ctx: Context
    dispose_mode: Literal["sequential", "batch"] = field(
        default="sequential", kw_only=True
    )
    _owned: dict[_Effect, None] = field(default_factory=dict, init=False)
    _state: _LifecycleState = field(default=_LifecycleState.ACTIVE, init=False)
    _dispose_once: _Disposer = field(init=False)
    _sync_disposal_guard_depth: int = field(default=0, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "_dispose_once",
            self._disposer(self._dispose, self._assert_disposal_allowed),
        )

    @staticmethod
    def _disposer(operation: _Disposer, check: Callable[[], None]) -> _Disposer:
        execute = once(operation)
        completion: _GuardedAwaitable[None] | None = None

        def dispose() -> None | Awaitable[None]:
            nonlocal completion
            check()
            result = execute()
            if isawaitable(result):
                if completion is None:
                    completion = _GuardedAwaitable(result, check)
                return completion
            return result

        return dispose

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
        pending = [self.ctx]
        while pending:
            ctx = pending.pop()
            object.__setattr__(ctx._lifecycle, "_state", _LifecycleState.CLOSING)
            for child in ctx._children:
                if child._lifecycle._state is _LifecycleState.ACTIVE:
                    pending.append(child)

    def _enter_sync_disposal_guard(self) -> tuple[Lifecycle, ...]:
        guarded: list[Lifecycle] = []
        ctx: Context | None = self.ctx
        while ctx is not None:
            current = ctx._lifecycle
            object.__setattr__(
                current,
                "_sync_disposal_guard_depth",
                current._sync_disposal_guard_depth + 1,
            )
            guarded.append(current)
            ctx = ctx.parent
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

    def _own(self, cleanup: _Cleanup | None = None) -> _Effect:
        effect = _Effect(self)
        effect._cleanup = cleanup
        self._owned[effect] = None
        return effect

    def _forget_owned(self, owned: _Effect) -> None:
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
        effect = self._own()
        try:
            cleanup = effect._invoke(setup)
        except BaseException:
            effect._release()
            raise
        if isawaitable(cleanup):
            effect._setup = _GuardedAwaitable(
                SharedAwaitable(effect._finish_setup(cleanup)), effect._check
            )
            return effect._setup
        effect._cleanup = cleanup
        return effect.dispose

    def dispose(self) -> None | Awaitable[None]:
        """Release owned effects using this Lifecycle's disposal mode.

        Synchronous cleanup runs immediately. Await asynchronous completion;
        once awaited, waiter cancellation does not cancel cleanup. Repeated
        calls share that completion and reproduce its terminal failure.
        Disposers are called in reverse registration order. Sequential mode
        waits between calls; batch mode joins all asynchronous results.
        Failures are grouped in call order, not completion order.
        Mutations in the entire ownership subtree are forbidden before the
        first cleanup; each Lifecycle remains readable until its own release.
        Failures are not retrieved merely to suppress asyncio diagnostics.
        """
        return self._dispose_once()

    def _dispose(self) -> None | Awaitable[None]:
        self._close_subtree()
        object.__setattr__(self, "_state", _LifecycleState.DISPOSING)
        owned = tuple(reversed(self._owned))
        execute_owned = _sequential if self.dispose_mode == "sequential" else _batch

        @continuation
        def execute() -> Generator[Any, Any, None]:
            error: BaseException | None = None
            try:
                yield execute_owned(owned)
            except BaseException as caught:
                error = caught
            finally:
                self._owned.clear()
                try:
                    self.ctx._release()
                except BaseException as release_error:
                    if error is None:
                        error = release_error
                    else:
                        error.__cause__ = release_error
                finally:
                    object.__setattr__(self, "_state", _LifecycleState.DISPOSED)
            if error is not None:
                raise error

        result = execute()
        return self._await_dispose(result) if isawaitable(result) else None

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
