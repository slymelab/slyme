"""Result types across synchronous and asynchronous continuation steps."""

from collections.abc import Awaitable

from typing_extensions import assert_type

from slyme.utils.continuation import Continuation


async def asynchronous() -> int:
    return 1


async def stringify(value: int) -> str:
    return str(value)


def recover(error: ValueError) -> str:
    return str(error)


async def arecover(error: Exception) -> str:
    return str(error)


def recover_number(error: Exception) -> int:
    return 0


async def check_types() -> None:
    assert_type(Continuation.each([1, 2], str), None | Awaitable[None])
    assert_type(Continuation.each([1, 2], stringify), None | Awaitable[None])
    assert_type(Continuation.resolve(1), Continuation[int])
    assert_type(Continuation.resolve(asynchronous()), Continuation[int])
    assert_type(Continuation.call(asynchronous), Continuation[int])
    assert_type(Continuation.resolve(1).then(stringify), Continuation[str])
    assert_type(Continuation.resolve(1).unwrap(), int | Awaitable[int])
    assert_type(
        Continuation.resolve(1).catch(recover, exceptions=ValueError),
        Continuation[int | str],
    )
    assert_type(
        Continuation.resolve(1).then(str, recover, exceptions=ValueError),
        Continuation[str],
    )
    assert_type(await Continuation.resolve(1).then(stringify), str)
    assert_type(
        Continuation.resolve(1).then(str, recover_number), Continuation[str | int]
    )
    assert_type(Continuation.resolve(1).catch(arecover), Continuation[int | str])
    assert_type(Continuation.resolve(1).then(int, arecover), Continuation[int | str])
    assert_type(Continuation.resolve(1).then(stringify, arecover), Continuation[str])
    assert_type(
        Continuation.resolve(1).then(int, arecover, exceptions=ValueError),
        Continuation[int | str],
    )
    assert_type(
        Continuation.resolve(1).then(stringify, arecover, exceptions=ValueError),
        Continuation[str],
    )
