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

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Generator
from dataclasses import dataclass
from functools import update_wrapper
from inspect import isawaitable
from types import MethodType
from typing import (
    Any,
    Concatenate,
    Generic,
    Literal,
    ParamSpec,
    TypeVar,
    cast,
    overload,
)

__all__ = [
    "Continuation",
    "Flatten",
    "FlatResult",
    "continuation",
    "run",
    "await_result",
    "SharedAwaitable",
    "once",
]

_P = ParamSpec("_P")
_BoundP = ParamSpec("_BoundP")
_T = TypeVar("_T")
_Return = TypeVar("_Return", covariant=True)
_Instance = TypeVar("_Instance")


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


@dataclass(frozen=True, slots=True)
class Flatten(Generic[_Return]):
    """Request one generator execution on the yielding caller's driver.

    Only yielding this object expands its generator. The generator is already
    created, but construction does not advance it. Hand its exclusive driving
    to the interpreter; create a fresh generator for another execution.

    Call mode returns the generator's final value unchanged. Start mode returns
    a FlatResult at completion or at the first asynchronous suspension.
    """

    generator: Generator[Any, Any, _Return]
    mode: Literal["call", "start"] = "call"


@dataclass(frozen=True, slots=True)
class FlatResult(Generic[_Return]):
    """A flat start's outcome, distinct from the generator's business result.

    With state='done', value is the final result, even if it is awaitable.
    With state='pending', value is an unscheduled awaitable that completes the
    execution and returns a FlatResult with state='done'. Keeping that envelope
    across the asynchronous boundary also prevents JavaScript Promise adoption
    from consuming an awaitable business value when this protocol is ported.
    The caller owns the remainder. This object is not awaitable and holds no
    failures: synchronous failures are thrown at the start, asynchronous ones
    on waiting.

    Returning a FlatResult from a generator returns it as business data; it
    does not delegate execution or implicitly unwrap nested results.
    """

    value: _Return | Awaitable[FlatResult[_Return]]
    state: Literal["done", "pending"]


class Continuation(Generic[_P, _Return]):
    """A generator function with direct, flat-call and flat-start entry points.

    Each invocation has independent execution state. Yielded awaitables are
    awaited once; all return values are data and are passed through unchanged.
    Use flat_call/flat_start to compose continuations without nested drivers.
    """

    __wrapped__: Callable[_P, Generator[Any, Any, _Return]]
    __name__: str
    __qualname__: str

    def __init__(self, func: Callable[_P, Generator[Any, Any, _Return]], /) -> None:
        self._func = func
        update_wrapper(self, func, updated=())

    def __call__(
        self, /, *args: _P.args, **kwargs: _P.kwargs
    ) -> _Return | Awaitable[_Return]:
        """Run immediately, returning a value or an unscheduled remainder."""
        return run(self._func(*args, **kwargs))

    def flat_call(self, /, *args: _P.args, **kwargs: _P.kwargs) -> Flatten[_Return]:
        """Request execution on the yielding caller's stack until completion."""
        return Flatten(self._func(*args, **kwargs), mode="call")

    def flat_start(self, /, *args: _P.args, **kwargs: _P.kwargs) -> Flatten[_Return]:
        """Request a FlatResult at completion or the first awaitable yield.

        Creating the request creates the generator without advancing it.
        Yielding it runs the synchronous prefix without scheduling tasks.
        """
        return Flatten(self._func(*args, **kwargs), mode="start")

    @overload
    def __get__(
        self, instance: None, owner: type[object] | None = None
    ) -> Continuation[_P, _Return]: ...
    @overload
    def __get__(
        self: Continuation[Concatenate[_Instance, _BoundP], _Return],
        instance: _Instance,
        owner: type[_Instance] | None = None,
    ) -> Continuation[_BoundP, _Return]: ...
    def __get__(
        self, instance: object | None, owner: type[object] | None = None
    ) -> Continuation[..., _Return]:
        if instance is None:
            return self
        return Continuation(MethodType(self._func, instance))


@overload
def continuation(
    func: Callable[_P, Generator[Any, Any, _T]], /
) -> Continuation[_P, _T]: ...
@overload
def continuation(
    func: None = None, /
) -> Callable[[Callable[_P, Generator[Any, Any, _T]]], Continuation[_P, _T]]: ...
def continuation(
    func: Callable[_P, Generator[Any, Any, _T]] | None = None, /
) -> (
    Continuation[_P, _T]
    | Callable[[Callable[_P, Generator[Any, Any, _T]]], Continuation[_P, _T]]
):
    """Decorate a generator function, with or without parentheses.

    Direct calls immediately drive a fresh execution. Yield .flat_call() to
    share the caller's driver, or .flat_start() to receive a FlatResult without
    waiting for asynchronous completion. None of these entry points schedules
    tasks. Both methods construct Flatten requests holding fresh generators.

    Return values are unchanged, including awaitables and Flatten requests.
    Use an explicit yield to await an operation. Compose continuations through
    flat_call/flat_start: directly calling and yielding a returned awaitable
    cannot distinguish asynchronous execution from awaitable business data.
    """
    if func is None:
        return continuation

    return Continuation(func)


def _advance_generator(
    stack: list[Generator[Any, Any, Any]],
    method: Literal["send", "throw"],
    argument: Any,
) -> tuple[bool, Any]:
    # Different yields may exchange unrelated types.
    # Each start owns a separate stack. Its parent is restored in O(1), with
    # no scans or copies of the surrounding frames at an async boundary.
    parents: list[list[Generator[Any, Any, Any]]] = []
    while True:
        if not stack:
            if parents:
                stack = parents.pop()
                if method == "send":
                    argument = FlatResult(argument, state="done")
            elif method == "throw":
                raise argument
            else:
                return True, argument

        generator = stack[-1]
        try:
            value = (
                generator.send(argument)
                if method == "send"
                else generator.throw(argument)
            )
        except StopIteration as finished:
            stack.pop()
            method, argument = "send", finished.value
            continue
        except BaseException as error:
            stack.pop()
            method, argument = "throw", error
            continue

        if isinstance(value, Flatten):
            if value.mode == "start":
                parents.append(stack)
                stack = [value.generator]
            else:
                stack.append(value.generator)
            method, argument = "send", None
            continue

        if isawaitable(value):
            if not parents:
                return False, value
            value = FlatResult(_resume_generator(value, stack), state="pending")
            stack = parents.pop()
        method, argument = "send", value


async def _resume_generator(
    pending: Awaitable[Any], stack: list[Generator[Any, Any, Any]]
) -> FlatResult[Any]:
    while True:
        try:
            value = await pending
        except BaseException as error:
            done, value = _advance_generator(stack, "throw", error)
        else:
            done, value = _advance_generator(stack, "send", value)
        if done:
            return FlatResult(value, state="done")
        pending = value


async def _resume(
    pending: Awaitable[Any], stack: list[Generator[Any, Any, Any]]
) -> object:
    result = await _resume_generator(pending, stack)
    return result.value


def run(generator: Generator[Any, Any, _T]) -> _T | Awaitable[_T]:
    """Advance a generator immediately until completion or an awaitable yield.

    Yielded Flatten requests use explicit stacks. Call mode returns the child's
    final value; start mode returns a FlatResult. Other yielded values, including
    ordinary generators and FlatResult objects, are sent back unchanged unless
    awaitable. An awaitable yield returns an unscheduled coroutine that awaits
    it once and resumes execution.
    Awaited failures, including cancellation, are thrown at the suspended yield.
    All return values and awaited results are passed through unchanged, without
    interpreting them again. Continuation uses this same execution protocol.

    The caller hands over exclusive driving of the generator and must await any
    asynchronous remainder. No tasks, error aggregation, cancellation shielding,
    or result caching are provided. Generator reuse follows Python's protocol.
    """
    stack = [generator]
    done, value = _advance_generator(stack, "send", None)
    if done:
        return cast(_T, value)
    return cast(Awaitable[_T], _resume(value, stack))
