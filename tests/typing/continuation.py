"""Generator return types survive synchronous and asynchronous driving."""

from collections.abc import Awaitable, Generator

from typing_extensions import assert_type

from slyme.utils.continuation import await_result, run


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
    assert_type(run(immediate()), str | Awaitable[str])
    assert_type(run(mixed()), str | Awaitable[str])
    result: Awaitable[str] = await_result(run(mixed()))
    assert_type(result, Awaitable[str])
    assert_type(await await_result(run(immediate())), str)
    assert_type(await await_result(run(mixed())), str)
