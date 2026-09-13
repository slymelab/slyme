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

"""Single-use synchronous and asynchronous result composition."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable, Iterator
from dataclasses import dataclass
from functools import partial
from inspect import isawaitable
from itertools import chain
from typing import Any, Generic, TypeVar, cast, overload

__all__ = ["BatchError", "Continuation", "Result", "await_result"]

_T = TypeVar("_T")
_R = TypeVar("_R")
_S = TypeVar("_S")
_E = TypeVar("_E", bound=BaseException)


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


_Step = tuple[
    Callable[[Any], Any] | None,
    Callable[[Any], Any] | None,
    type[BaseException],
]


@dataclass(frozen=True)
class Result(Generic[_T]):
    """One completed call: a returned value or a raised error.

    Successful values, including None and exception objects, use ``value``.
    Failed calls use ``error`` and leave ``value`` as None.
    """

    value: _T | None = None
    error: BaseException | None = None

    @staticmethod
    def _collect(results: list[Result[_R]]) -> list[_R]:
        if any(result.error is not None for result in results):
            raise BatchError(results)
        return [cast(_R, result.value) for result in results]


class BatchError(Exception):
    """Ordered results of completed calls, including successes and failures.

    Nested batches retain their own BatchError and local indices. An iterator
    failure occupies the next input slot. Fail-fast execution records only the
    attempted prefix; unconsumed inputs have no result.
    """

    def __init__(self, results: Iterable[Result[Any]]) -> None:
        self.results = list(results)
        failed = [
            i for i, result in enumerate(self.results) if result.error is not None
        ]
        super().__init__(f"Batch failed at input indices {failed}.")


class Continuation(Generic[_T]):
    """One mutable, single-use chain of immediate or asynchronous operations.

    Unlike JavaScript Promise.then, ``then`` and ``catch`` append to the same
    object; aliases do not create independent branches or subscribers. Building
    the chain runs no callbacks. ``unwrap`` consumes it, executing synchronous
    work eagerly and returning any asynchronous remainder. ``aunwrap`` provides
    an always-awaitable result; the chain itself is not awaitable. An executing
    or consumed chain cannot be extended or executed again. Only ``batch``
    schedules tasks, when its asynchronous remainder is awaited. No result
    caching, cancellation shielding, or resource ownership is provided.
    """

    __slots__ = ("_value", "_steps")

    @overload
    def __init__(self, value: Awaitable[_T]) -> None: ...
    @overload
    def __init__(self, value: _T | Awaitable[_T]) -> None: ...
    def __init__(self, value: _T | Awaitable[_T]) -> None:
        """Wrap an existing result without awaiting it or calling its value."""
        self._value: Any = value
        self._steps: list[_Step] | None = []

    @overload
    @staticmethod
    def call(operation: Callable[[], Awaitable[_R]]) -> Continuation[_R]: ...
    @overload
    @staticmethod
    def call(operation: Callable[[], _R | Awaitable[_R]]) -> Continuation[_R]: ...
    @staticmethod
    def call(operation: Callable[[], _R | Awaitable[_R]]) -> Continuation[_R]:
        """Defer a call, including synchronous errors, until execution."""
        return Continuation(None).then(lambda _: operation())

    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], Awaitable[_R]],
        on_rejected: Callable[[Exception], Awaitable[_S]],
    ) -> Continuation[_R | _S]: ...
    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], _R | Awaitable[_R]],
        on_rejected: Callable[[Exception], Awaitable[_S]],
    ) -> Continuation[_R | _S]: ...
    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], Awaitable[_R]],
        on_rejected: Callable[[Exception], _S | Awaitable[_S]] | None = None,
    ) -> Continuation[_R | _S]: ...
    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], _R | Awaitable[_R]],
        on_rejected: Callable[[Exception], _S | Awaitable[_S]] | None = None,
    ) -> Continuation[_R | _S]: ...
    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], Awaitable[_R]],
        on_rejected: Callable[[_E], Awaitable[_S]],
        *,
        exceptions: type[_E],
    ) -> Continuation[_R | _S]: ...
    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], _R | Awaitable[_R]],
        on_rejected: Callable[[_E], Awaitable[_S]],
        *,
        exceptions: type[_E],
    ) -> Continuation[_R | _S]: ...
    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], Awaitable[_R]],
        on_rejected: Callable[[_E], _S | Awaitable[_S]],
        *,
        exceptions: type[_E],
    ) -> Continuation[_R | _S]: ...
    @overload
    def then(
        self,
        on_fulfilled: Callable[[_T], _R | Awaitable[_R]],
        on_rejected: Callable[[_E], _S | Awaitable[_S]],
        *,
        exceptions: type[_E],
    ) -> Continuation[_R | _S]: ...
    def then(
        self,
        on_fulfilled: Callable[[_T], Any],
        on_rejected: Callable[[Any], Any] | None = None,
        *,
        exceptions: type[BaseException] = Exception,
    ) -> Continuation[Any]:
        """Append success and optional Exception recovery callbacks.

        The rejection callback handles only upstream errors, not errors from
        its paired success callback. Cancellation propagates by default.
        """
        self._append((on_fulfilled, on_rejected, exceptions))
        return self

    @overload
    def catch(
        self, on_rejected: Callable[[Exception], Awaitable[_R]]
    ) -> Continuation[_T | _R]: ...
    @overload
    def catch(
        self, on_rejected: Callable[[Exception], _R | Awaitable[_R]]
    ) -> Continuation[_T | _R]: ...
    @overload
    def catch(
        self,
        on_rejected: Callable[[_E], Awaitable[_R]],
        *,
        exceptions: type[_E],
    ) -> Continuation[_T | _R]: ...
    @overload
    def catch(
        self,
        on_rejected: Callable[[_E], _R | Awaitable[_R]],
        *,
        exceptions: type[_E],
    ) -> Continuation[_T | _R]: ...
    def catch(
        self,
        on_rejected: Callable[[Any], Any],
        *,
        exceptions: type[BaseException] = Exception,
    ) -> Continuation[Any]:
        """Append recovery for the selected exception class and its subclasses."""
        self._append((None, on_rejected, exceptions))
        return self

    def _append(self, step: _Step) -> None:
        if self._steps is None:
            raise RuntimeError("A consumed Continuation cannot be extended.")
        self._steps.append(step)

    def unwrap(self) -> _T | Awaitable[_T]:
        """Run now, returning a value or an unscheduled asynchronous remainder."""
        if self._steps is None:
            raise RuntimeError("A Continuation can only be executed once.")
        remaining = iter(self._steps)
        self._steps = None
        value, self._value = self._value, None
        error: BaseException | None = None
        for fulfilled, rejected, exceptions in remaining:
            if isawaitable(value):
                return self._continue(
                    value, (fulfilled, rejected, exceptions), remaining
                )
            callback = fulfilled if error is None else rejected
            if callback is None or (
                error is not None and not isinstance(error, exceptions)
            ):
                continue
            try:
                value = callback(value if error is None else error)
                error = None
            except BaseException as caught:
                value, error = None, caught
        if error is not None:
            raise error
        return value

    @staticmethod
    async def _continue(
        pending: Awaitable[Any],
        first: _Step,
        remaining: Iterator[_Step],
    ) -> Any:
        value: Any = None
        error: BaseException | None = None
        try:
            value = await pending
        except BaseException as caught:
            error = caught
        for fulfilled, rejected, exceptions in chain((first,), remaining):
            callback = fulfilled if error is None else rejected
            if callback is not None and (
                error is None or isinstance(error, exceptions)
            ):
                try:
                    value = await await_result(
                        callback(value if error is None else error)
                    )
                    error = None
                except BaseException as caught:
                    value, error = None, caught
        if error is not None:
            raise error
        return value

    def aunwrap(self) -> Awaitable[_T]:
        """Unwrap now and return an awaitable, preserving immediate sync work."""
        return await_result(self.unwrap())

    @overload
    @staticmethod
    def sequential(
        values: Iterable[_R],
        call: Callable[[_R], Awaitable[_S]],
        *,
        continue_on_error: bool = False,
    ) -> Continuation[list[_S]]: ...
    @overload
    @staticmethod
    def sequential(
        values: Iterable[_R],
        call: Callable[[_R], _S | Awaitable[_S]],
        *,
        continue_on_error: bool = False,
    ) -> Continuation[list[_S]]: ...
    @staticmethod
    def sequential(
        values: Iterable[_R],
        call: Callable[[_R], _S | Awaitable[_S]],
        *,
        continue_on_error: bool = False,
    ) -> Continuation[list[_S]]:
        """Build ordered calls, returning their values or raising BatchError.

        Iteration starts on execution. Each next input is consumed only after
        the preceding call completes. Entirely synchronous calls stay synchronous.
        Failure, including cancellation, stops iteration unless continue_on_error
        is enabled. Iterator failures always stop iteration. No tasks are scheduled
        and no cancellation shielding is provided.
        """

        def execute() -> list[_S] | Awaitable[list[_S]]:
            results: list[Result[_S]] = []

            def record(error: BaseException) -> None:
                results.append(Result(error=error))
                if not continue_on_error:
                    raise BatchError(results)

            def calls() -> Iterator[None | Awaitable[None]]:
                try:
                    iterator = iter(values)
                except BaseException as error:
                    results.append(Result(error=error))
                    return
                while True:
                    try:
                        value = next(iterator)
                    except StopIteration:
                        return
                    except BaseException as error:
                        results.append(Result(error=error))
                        return
                    yield (
                        Continuation.call(partial(call, value))
                        .then(
                            lambda value: results.append(Result(value=value)),
                            record,
                            exceptions=BaseException,
                        )
                        .unwrap()
                    )

            iterator = calls()

            async def remaining(pending: Awaitable[None]) -> list[_S]:
                await pending
                for result in iterator:
                    await await_result(result)
                return Result._collect(results)

            for result in iterator:
                if isawaitable(result):
                    return remaining(result)
            return Result._collect(results)

        return Continuation.call(execute)

    @overload
    @staticmethod
    def batch(
        values: Iterable[_R], call: Callable[[_R], Awaitable[_S]]
    ) -> Continuation[list[_S]]: ...
    @overload
    @staticmethod
    def batch(
        values: Iterable[_R], call: Callable[[_R], _S | Awaitable[_S]]
    ) -> Continuation[list[_S]]: ...
    @staticmethod
    def batch(
        values: Iterable[_R], call: Callable[[_R], _S | Awaitable[_S]]
    ) -> Continuation[list[_S]]:
        """Build independent calls, returning ordered results or BatchError.

        Every input is attempted despite earlier call failures. Execution stays
        synchronous until a call returns an awaitable; awaiting the remainder
        schedules it and the remaining calls concurrently. BatchError retains
        both successful values and failures. Child cancellation is an indexed failure;
        cancelling the batch waiter follows asyncio cancellation propagation.
        If cancellation coincides with other failures, BatchError retains them
        and chains the cancellation as its cause; otherwise cancellation propagates.
        An iterator failure stops enumeration but still waits for started calls.
        """

        def execute() -> list[_S] | Awaitable[list[_S]]:
            try:
                iterator = iter(values)
            except BaseException as error:
                raise BatchError([Result(error=error)]) from error
            results: list[Result[_S]] = []

            async def remaining(first: Awaitable[_S]) -> list[_S]:
                async def evaluate(value: _R) -> _S:
                    return await await_result(call(value))

                tasks = {len(results) - 1: asyncio.create_task(await_result(first))}
                try:
                    for value in iterator:
                        index = len(results)
                        results.append(Result())
                        tasks[index] = asyncio.create_task(evaluate(value))
                except BaseException as error:
                    results.append(Result(error=error))

                cancellation: asyncio.CancelledError | None = None
                try:
                    await asyncio.gather(*tasks.values(), return_exceptions=True)
                except asyncio.CancelledError as error:
                    cancellation = error
                for index, task in tasks.items():
                    try:
                        results[index] = Result(value=task.result())
                    except BaseException as error:
                        results[index] = Result(error=error)
                if cancellation is not None:
                    if any(
                        result.error is not None
                        and not isinstance(result.error, asyncio.CancelledError)
                        for result in results
                    ):
                        raise BatchError(results) from cancellation
                    raise cancellation
                return Result._collect(results)

            try:
                for value in iterator:
                    try:
                        result = call(value)
                    except BaseException as error:
                        results.append(Result(error=error))
                        continue
                    if isawaitable(result):
                        results.append(Result())
                        return remaining(result)
                    results.append(Result(value=result))
            except BaseException as error:
                results.append(Result(error=error))
            return Result._collect(results)

        return Continuation.call(execute)
