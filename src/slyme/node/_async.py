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

"""Cancellation-safe waits for already-started asynchronous work."""

import asyncio
from typing import TypeVar

_T = TypeVar("_T")


async def finish_uninterruptibly(
    future: asyncio.Future[_T],
) -> asyncio.CancelledError | None:
    """Finish an operation and return cancellation requested of its waiter."""
    cancellation: asyncio.CancelledError | None = None
    while not future.done():
        try:
            await asyncio.shield(future)
        except asyncio.CancelledError as error:
            if future.cancelled():
                break
            if cancellation is None:
                cancellation = error
        except BaseException:
            break

    return cancellation


async def wait_uninterruptibly(future: asyncio.Future[_T]) -> _T:
    """Defer cancellation until an already-started operation has finished."""
    cancellation = await finish_uninterruptibly(future)

    if cancellation is not None:
        try:
            future.result()
        except asyncio.CancelledError:
            raise cancellation from None
        except BaseException as error:
            raise cancellation from error
        raise cancellation
    return future.result()
