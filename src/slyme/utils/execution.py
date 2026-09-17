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

"""Execute synchronous or asynchronous operations and share their results."""

import asyncio
from collections.abc import Awaitable, Callable, Generator
from functools import wraps
from inspect import isawaitable
from typing import Any, Generic, ParamSpec, TypeVar, cast, overload

__all__ = ["continuation", "run", "await_result", "SharedAwaitable", "once"]

_P = ParamSpec("_P")
_T = TypeVar("_T")


@overload
def continuation(
    func: Callable[_P, Generator[Any, Any, _T]], /
) -> Callable[_P, _T | Awaitable[_T]]: ...
@overload
def continuation(
    func: None = None, /
) -> Callable[
    [Callable[_P, Generator[Any, Any, _T]]], Callable[_P, _T | Awaitable[_T]]
]: ...
def continuation(
    func: Callable[_P, Generator[Any, Any, _T]] | None = None, /
) -> (
    Callable[_P, _T | Awaitable[_T]]
    | Callable[
        [Callable[_P, Generator[Any, Any, _T]]], Callable[_P, _T | Awaitable[_T]]
    ]
):
    """Decorate a generator function, with or without parentheses.

    Each call drives a fresh generator through run(), immediately returning its
    result or an unscheduled asynchronous remainder. Arguments and function
    metadata are preserved; calls do not share execution state.
    """
    if func is None:
        return continuation

    @wraps(func)
    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _T | Awaitable[_T]:
        return run(func(*args, **kwargs))

    return wrapped


@overload
async def await_result(value: Awaitable[_T]) -> _T: ...
@overload
async def await_result(value: _T | Awaitable[_T]) -> _T: ...
async def await_result(value: _T | Awaitable[_T]) -> _T:
    """Await the outer result, leaving values inside containers untouched.

    Calling this coroutine function does not schedule execution.
    """
    if isawaitable(value):
        return await value
    return value


class SharedAwaitable(Generic[_T]):
    """Lazily schedule one awaitable and share its completion among waiters.

    The first wait schedules the operation on the current event loop. Later
    waits reuse its result or failure; an already scheduled input keeps running.
    Cancelling a waiter does not cancel the operation. Its owner must still
    await completion and handle failures. No automatic logging is performed.

    Concurrent waits must use the same event loop. An operation cannot await
    its own completion. Ordinary continuation results remain single-use unless
    explicitly wrapped.
    """

    __slots__ = ("_operation", "_task")

    def __init__(self, operation: Awaitable[_T], /) -> None:
        self._operation: Awaitable[_T] | None = operation
        self._task: asyncio.Future[_T] | None = None

    async def _wait(self) -> _T:
        if self._task is None:
            self._task = asyncio.ensure_future(cast(Awaitable[_T], self._operation))
            self._operation = None
        if self._task is asyncio.current_task():
            raise RuntimeError("An operation cannot await its own completion.")
        return await asyncio.shield(self._task)

    def __await__(self) -> Generator[Any, None, _T]:
        return self._wait().__await__()


@overload
def once(
    callback: Callable[_P, Awaitable[_T]], /
) -> Callable[_P, SharedAwaitable[_T]]: ...
@overload
def once(callback: Callable[_P, _T], /) -> Callable[_P, _T]: ...
def once(callback: Callable[_P, object], /) -> Callable[_P, object]:
    """Invoke a callback once and share its result, including failure.

    The first call supplies the arguments. Synchronous results and exceptions
    are replayed on subsequent calls. An asynchronous result always returns the
    same SharedAwaitable, scheduled only when first awaited; its failure stays
    asynchronous even after completion. Failed operations are not retried.

    Reentry before the first call returns raises RuntimeError. The callback is
    consumed before invocation and is not retained through function metadata.
    This wrapper is intended for a single thread.
    """
    pending: Callable[_P, object] | None = callback
    result: object
    failure: BaseException | None = None
    running = False

    def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> object:
        nonlocal pending, result, failure, running
        if pending is None:
            if running:
                raise RuntimeError("A once callback cannot re-enter before returning.")
            if failure is not None:
                raise failure
            return result
        current, pending, running = pending, None, True

        try:
            value = current(*args, **kwargs)
            result = SharedAwaitable(value) if isawaitable(value) else value
        except BaseException as error:
            failure = error
            raise
        finally:
            running = False
        return result

    return wrapped


def run(generator: Generator[Any, Any, _T]) -> _T | Awaitable[_T]:
    """Advance a generator immediately until completion or an awaitable yield.

    Ordinary yielded values are sent back unchanged. An awaitable yield returns
    an unscheduled coroutine that awaits it and resumes the same generator.
    Awaited failures, including cancellation, are thrown at the suspended yield.
    Only yielded values are awaited; the generator's return value is unchanged.

    The caller hands over exclusive driving of the generator and must await any
    asynchronous remainder. No tasks, error aggregation, cancellation shielding,
    or result caching are provided. Generator reuse follows Python's protocol.
    """

    # Different yields may exchange unrelated types; the return type stays _T.
    def advance(method: Callable[[Any], Any], argument: Any) -> tuple[bool, Any]:
        while True:
            try:
                value = method(argument)
            except StopIteration as finished:
                return True, finished.value
            if isawaitable(value):
                return False, value
            method, argument = generator.send, value

    async def resume(pending: Awaitable[Any]) -> _T:
        while True:
            try:
                value = await pending
            except BaseException as error:
                done, value = advance(generator.throw, error)
            else:
                done, value = advance(generator.send, value)
            if done:
                return cast(_T, value)
            pending = value

    done, value = advance(generator.send, None)
    return cast(_T, value) if done else resume(value)
