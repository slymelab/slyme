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

    def __init__(
        self, owner: Lifecycle, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]
    ) -> None:
        self.owner: Lifecycle | None = owner
        self._setup_callback = setup
        self._cleanup: _Cleanup | None = None
        object.__setattr__(self, "setup", once(self.setup))
        object.__setattr__(self, "dispose", once(self.dispose))
        object.__setattr__(self, "finalize", once(self.finalize))

    def finalize(self) -> None:
        """Finish internal ownership bookkeeping without invoking user callbacks."""
        owner = self.owner
        self.owner = None
        if owner is not None:
            owner._unregister_effect(self)

    @continuation
    def setup(self) -> Generator[Any, Any, _Disposer]:
        try:
            self._cleanup = yield self._setup_callback()
            return self.dispose
        except BaseException:
            self.finalize()
            raise
        finally:
            del self._setup_callback

    @continuation
    def dispose(self) -> Generator[Any, Any, None]:
        try:
            yield self.setup()
            cleanup, self._cleanup = self._cleanup, None
            if cleanup is not None:
                yield cleanup()
        finally:
            self._cleanup = None
            self.finalize()


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
    DISPOSE_PENDING = "dispose_pending"
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
    _effects: dict[_Effect, None] = field(default_factory=dict, init=False)
    _state: _LifecycleState = field(default=_LifecycleState.ACTIVE, init=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dispose", once(self.dispose))

    def _set_state(self, state: _LifecycleState) -> None:
        object.__setattr__(self, "_state", state)

    def assert_readable(self) -> None:
        if self._state is _LifecycleState.DISPOSED:
            raise RuntimeError("Lifecycle has been disposed.")

    def assert_active(self) -> None:
        if self._state is _LifecycleState.ACTIVE:
            return
        if self._state is _LifecycleState.DISPOSED:
            raise RuntimeError("Lifecycle has been disposed.")
        if self._state is _LifecycleState.DISPOSE_PENDING:
            raise RuntimeError("An ancestor Lifecycle is being disposed.")
        raise RuntimeError("Lifecycle is being disposed.")

    def _prepare_dispose(self) -> None:
        """Forbid mutations throughout the ownership subtree before cleanup."""
        if self._state is not _LifecycleState.ACTIVE:
            return
        pending = [self.ctx]
        while pending:
            ctx = pending.pop()
            ctx._lifecycle._set_state(_LifecycleState.DISPOSE_PENDING)
            for child in ctx._children:
                if child._lifecycle._state is _LifecycleState.ACTIVE:
                    pending.append(child)

    def _register_effect(
        self, setup: Callable[[], _Cleanup | Awaitable[_Cleanup]]
    ) -> _Effect:
        """Create and register an effect without running setup."""
        effect = _Effect(self, setup)
        self._effects[effect] = None
        return effect

    def _unregister_effect(self, effect: _Effect) -> None:
        """Remove an effect without running cleanup."""
        self._effects.pop(effect, None)

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
        return self._register_effect(setup).setup()

    @continuation
    def dispose(self) -> Generator[Any, Any, None]:
        """Release owned effects using this Lifecycle's disposal mode.

        Synchronous cleanup runs immediately. Await asynchronous completion;
        once awaited, waiter cancellation does not cancel cleanup. Repeated
        calls share that completion and reproduce its terminal failure.
        Disposers are called in reverse registration order. Sequential mode
        waits between calls; batch mode joins all asynchronous results.
        Failures are grouped in call order, not completion order.
        A finalization failure propagates with any cleanup failure as its
        implicit exception context; disposal state is finalized either way.
        Mutations in the entire ownership subtree are forbidden before the
        first cleanup; each Lifecycle remains readable until its own release.
        Failures are not retrieved merely to suppress asyncio diagnostics.
        """
        self._prepare_dispose()
        self._set_state(_LifecycleState.DISPOSING)
        effects = tuple(reversed(self._effects))
        execute_effects = _sequential if self.dispose_mode == "sequential" else _batch

        try:
            yield execute_effects(effects)
        finally:
            self._effects.clear()
            try:
                self.ctx._finalize()
            finally:
                self._set_state(_LifecycleState.DISPOSED)
