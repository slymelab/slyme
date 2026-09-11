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

"""Compose immediate results and awaitables without starting an event loop."""

from collections.abc import Awaitable, Callable
from inspect import isawaitable
from typing import NoReturn, TypeVar, overload

__all__ = ["resolve"]

_T = TypeVar("_T")
_R = TypeVar("_R")


@overload
async def resolve(value: Awaitable[_T]) -> _T: ...
@overload
async def resolve(value: _T | Awaitable[_T]) -> _T: ...
async def resolve(value: _T | Awaitable[_T]) -> _T:
    """Await an execution result, or return an immediate value unchanged.

    Only the outer result is awaited; values inside containers remain data.
    Calling this function does not schedule execution.
    """
    if isawaitable(value):
        return await value
    return value


def _chain(
    value: _T | Awaitable[_T], then: Callable[[_T], _R | Awaitable[_R]]
) -> _R | Awaitable[_R]:
    if not isawaitable(value):
        return then(value)

    async def continue_async() -> _R:
        return await resolve(then(await value))

    return continue_async()


def _guard(
    call: Callable[[], _T | Awaitable[_T]],
    on_error: Callable[[Exception], NoReturn],
) -> _T | Awaitable[_T]:
    try:
        value = call()
    except Exception as error:
        on_error(error)
    if not isawaitable(value):
        return value

    async def continue_async() -> _T:
        try:
            return await value
        except Exception as error:
            on_error(error)

    return continue_async()
