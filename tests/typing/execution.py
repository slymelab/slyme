"""Execution adapters preserve parameter types and synchronous return values."""

from __future__ import annotations

from collections.abc import Awaitable, Generator
from typing import Any, TypeVar

from typing_extensions import assert_type

from slyme.utils.execution import SharedAwaitable, await_result, continuation, once, run

_T = TypeVar("_T")


@continuation
def decorated(value: int, /, *, label: str = "") -> Generator[Any, Any, str]:
    result = yield value
    return f"{label}{result}"


@continuation()
def identity(value: _T) -> Generator[Any, Any, _T]:
    yield None
    return value


class Example:
    @continuation()
    def method(self, value: int) -> Generator[Any, Any, str]:
        yield value
        return str(value)


async def asynchronous() -> int:
    return 1


@once
def once_sync(value: int, *, scale: int = 1) -> int:
    return value * scale


@once
async def once_async(value: int) -> int:
    return value


@once
def once_mixed(value: int) -> int | Awaitable[int]:
    return value


def immediate() -> Generator[int, int, str]:
    value = yield 1
    return str(value)


def mixed() -> Generator[int | Awaitable[int], int, str]:
    first = yield 1
    second = yield asynchronous()
    return str(first + second)


async def check_types() -> None:
    assert_type(decorated(1, label="value"), str | Awaitable[str])
    assert_type(identity(1), int | Awaitable[int])
    assert_type(identity("value"), str | Awaitable[str])
    assert_type(Example().method(1), str | Awaitable[str])
    assert_type(await await_result(decorated(1)), str)
    assert_type(run(immediate()), str | Awaitable[str])
    assert_type(run(mixed()), str | Awaitable[str])
    result: Awaitable[str] = await_result(run(mixed()))
    assert_type(result, Awaitable[str])
    assert_type(await await_result(run(immediate())), str)
    assert_type(await await_result(run(mixed())), str)
    assert_type(once_sync(1, scale=2), int)
    assert_type(once_async(1), SharedAwaitable[int])
    assert_type(once_mixed(1), int | Awaitable[int])
    assert_type(await once_async(1), int)
