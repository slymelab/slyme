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
from dataclasses import dataclass, field
from enum import Enum
from functools import partial
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, Literal, overload

from slyme.utils.exception import exception_group
from slyme.utils.execution import continuation, once

if TYPE_CHECKING:
    from .core import Context

__all__ = ["Lifecycle"]

_Cleanup = Callable[[], None | Awaitable[None]]
_Disposer = Callable[[], None | Awaitable[None]]


class _Effect:
    """Own setup and cleanup as one registration with a repeatable disposer."""

    __slots__ = ("owner", "_cleanup", "setup", "dispose")

    def __init__(
        self, owner: Lifecycle, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]
    ) -> None:
        self.owner: Lifecycle | None = owner
        self._cleanup: _Cleanup | None = None
        self.setup = once(partial(self._setup, setup))
        self.dispose = once(self._dispose)

    def _release(self) -> None:
        owner = self.owner
        self.owner = None
        if owner is not None:
            owner._forget_owned(self)

    @continuation
    def _setup(
        self, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]
    ) -> Generator[Any, Any, _Disposer]:
        try:
            self._cleanup = yield setup()
            return self.dispose
        except BaseException:
            self._release()
            raise

    @continuation
    def _dispose(self) -> Generator[Any, Any, None]:
        try:
            yield self.setup()
            cleanup, self._cleanup = self._cleanup, None
            if cleanup is not None:
                yield cleanup()
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "_dispose_once", once(self._dispose))

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

    def _own(self, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]) -> _Effect:
        effect = _Effect(self, setup)
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
        Setup and cleanup must not reenter disposal of themselves, their owner,
        or an ancestor, or wait for disposal that includes themselves. Such
        calls are unsupported; lifecycle reentry and wait cycles are not checked.
        """
        self.assert_active()
        return self._own(setup).setup()

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

    @continuation
    def _dispose(self) -> Generator[Any, Any, None]:
        self._close_subtree()
        object.__setattr__(self, "_state", _LifecycleState.DISPOSING)
        owned = tuple(reversed(self._owned))
        execute_owned = _sequential if self.dispose_mode == "sequential" else _batch

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
