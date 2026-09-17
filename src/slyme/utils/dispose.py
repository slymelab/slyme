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

"""Callable views of asynchronous effect setup."""

from collections.abc import Awaitable, Callable, Generator
from inspect import isawaitable
from typing import Any, Generic, TypeVar, overload

from .execution import SharedAwaitable, await_result

__all__ = ["wrap_disposer"]

_Disposer = Callable[[], None | Awaitable[None]]
_D = TypeVar("_D", bound=_Disposer)


class _AsyncDisposer(Generic[_D]):
    __slots__ = ("_setup",)

    def __init__(self, setup: Awaitable[_D]) -> None:
        self._setup = SharedAwaitable(setup)

    def __await__(self) -> Generator[Any, None, _D]:
        return self._setup.__await__()

    def __call__(self) -> SharedAwaitable[None]:
        async def dispose() -> None:
            cleanup = await self._setup
            await await_result(cleanup())

        return SharedAwaitable(dispose())


@overload
def wrap_disposer(result: Awaitable[_D], /) -> _AsyncDisposer[_D]: ...
@overload
def wrap_disposer(result: _D, /) -> _D: ...
def wrap_disposer(result: _D | Awaitable[_D], /) -> _D | _AsyncDisposer[_D]:
    """Return a disposer unchanged, or wrap asynchronous setup.

    Awaiting the wrapper returns the setup's disposer without calling it.
    Calling the wrapper returns a SharedAwaitable that awaits setup, invokes
    its disposer, and awaits any asynchronous cleanup. Setup is shared across
    all waits and calls, including its failure. The wrapper schedules no work
    until awaited; already scheduled inputs keep running. Cancelling a waiter
    does not cancel the shared work.

    Each call requests disposal separately. Use once on the underlying disposer
    to share cleanup across calls and direct calls to the disposer returned by
    awaiting setup. A synchronous input is returned unchanged.
    """
    if isawaitable(result):
        return _AsyncDisposer(result)
    return result
