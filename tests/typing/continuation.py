"""Generator return types survive synchronous and asynchronous driving."""

from collections.abc import Awaitable, Generator
from typing import Any, TypeVar

from typing_extensions import assert_type

from slyme.utils.continuation import await_result, continuation, run

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
