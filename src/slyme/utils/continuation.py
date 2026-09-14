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

"""Drive generator control flow with immediate or asynchronous yielded values."""

from collections.abc import Awaitable, Callable, Generator
from inspect import isawaitable
from typing import Any, TypeVar, cast, overload

__all__ = ["run", "await_result"]

_T = TypeVar("_T")


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
