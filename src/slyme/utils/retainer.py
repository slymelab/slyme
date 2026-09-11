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

"""Single-thread-owned reversible registration with conditional final cleanup."""

from __future__ import annotations

from collections.abc import Callable
from typing import Generic, ParamSpec, cast

__all__ = ["Retainer"]

_P = ParamSpec("_P")
_Dispose = Callable[[], None]


class Retainer(Generic[_P]):
    """Wrap registrations with exact disposal and one conditional cleanup.

    ``acquire`` receives each registration's arguments and returns its disposer.
    After successful disposal, ``should_cleanup`` decides whether to permanently
    close this Retainer and invoke ``cleanup``. Construction does not check the
    condition. Storage, duplicate policies, and partial-registration rollback
    belong to the callbacks; no separate reference count is maintained.

    All callbacks must be synchronous and run on the owning thread. They cannot
    re-enter this Retainer's acquisition or another pending disposal. Repeating
    the currently executing disposal is a no-op. Direct storage mutations do not
    trigger condition checks.

    Closing rejects new acquisitions but does not discard outstanding disposers.
    The condition must therefore permit cleanup without invalidating those
    disposers. Already closed Retainers do not check the condition again.
    """

    __slots__ = ("_acquire", "_should_cleanup", "_cleanup", "_busy", "__weakref__")

    def __init__(
        self,
        acquire: Callable[_P, _Dispose],
        *,
        should_cleanup: Callable[[], bool],
        cleanup: _Dispose,
    ) -> None:
        self._acquire: Callable[_P, _Dispose] | None = acquire
        self._should_cleanup: Callable[[], bool] | None = should_cleanup
        self._cleanup: _Dispose | None = cleanup
        self._busy = False

    def _enter(self) -> None:
        if self._busy:
            raise RuntimeError("Retainer callbacks cannot re-enter pending operations.")
        self._busy = True

    @property
    def closed(self) -> bool:
        """Whether final cleanup has started, even if that cleanup failed."""
        return self._cleanup is None

    def acquire(self, *args: _P.args, **kwargs: _P.kwargs) -> _Dispose:
        """Register and return a disposer that executes its operation at most once.

        Repeated calls reproduce the first call's success or failure. A failed
        disposer or condition check skips final cleanup for that call; no callback
        is retried automatically. Failed acquisitions must leave their
        storage consistent without relying on a returned disposal handle.

        A live disposal handle retains this Retainer and its underlying disposer.
        A successful disposal drops those references. Failures retain their
        exceptions and tracebacks for subsequent calls.
        """
        if self.closed:
            raise RuntimeError("Retainer is closed.")
        self._enter()
        try:
            callback = cast(Callable[_P, _Dispose], self._acquire)
            undo = callback(*args, **kwargs)
        finally:
            self._busy = False

        pending: tuple[Retainer[_P], _Dispose] | None = (self, undo)
        error: BaseException | None = None

        def dispose() -> None:
            nonlocal pending, error
            if pending is None:
                if error is not None:
                    raise error
                return
            retainer, undo = pending
            retainer._enter()
            pending = None
            try:
                undo()
                if not retainer.closed:
                    condition = cast(Callable[[], bool], retainer._should_cleanup)
                    if condition():
                        cleanup = cast(_Dispose, retainer._cleanup)
                        retainer._acquire = None
                        retainer._should_cleanup = None
                        retainer._cleanup = None
                        cleanup()
            except BaseException as failure:
                error = failure
                raise
            finally:
                retainer._busy = False

        return dispose
