"""Execution adapters preserve parameter types and synchronous return values."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Generator
from typing import Any, TypeVar

from typing_extensions import assert_type

from slyme.utils.execution import (
    Continuation,
    Flatten,
    await_result,
    continuation,
    once,
    run,
)

_T = TypeVar("_T")


@continuation
def decorated(value: int, /, *, label: str = "") -> Generator[Any, Any, str]:
    result = yield value
    return f"{label}{result}"


@continuation()
def boxed(value: _T) -> Generator[Any, Any, list[_T]]:
    yield None
    return [value]


class Example:
    @continuation()
    def method(self, value: int) -> Generator[Any, Any, str]:
        yield value
        return str(value)


async def asynchronous() -> int:
    return 1


@continuation
def returned_async() -> Generator[Any, Any, Awaitable[int]]:
    yield None
    return asynchronous()


@continuation()
def returned_coroutine() -> Generator[Any, Any, Coroutine[Any, Any, int]]:
    yield None
    return asynchronous()


@continuation()
def returned_mixed(value: int) -> Generator[Any, Any, int | Awaitable[int]]:
    yield None
    return asynchronous() if value else 1


@continuation
def returned_nested(
    value: Awaitable[Awaitable[int]],
) -> Generator[Any, Any, Awaitable[Awaitable[int]]]:
    yield None
    return value


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
    assert_type(boxed(1), list[int] | Awaitable[list[int]])
    assert_type(boxed("value"), list[str] | Awaitable[list[str]])
    assert_type(Example().method(1), str | Awaitable[str])
    assert_type(Example.method(Example(), 1), str | Awaitable[str])
    assert_type(returned_async(), int | Awaitable[int])
    assert_type(
        returned_coroutine(),
        int | Awaitable[int],
    )
    assert_type(returned_mixed(1), int | Awaitable[int])
    assert_type(
        Continuation(returned_async.__wrapped__)(),
        int | Awaitable[int],
    )
    assert_type(
        decorated.flat_call(1, label="value").generate(), Generator[Any, Any, str]
    )
    assert_type(Example().method.flat_call(1).generate(), Generator[Any, Any, str])
    assert_type(returned_async.flat_call().generate(), Generator[Any, Any, int])
    # All three entry points keep the same parameter checking, including
    # instance binding and keyword-only parameters.
    assert_type(decorated.flat_call(1, label="value"), Flatten[str])
    assert_type(decorated.flat_start(1, label="value"), Flatten[str])
    assert_type(Example().method.flat_call(1), Flatten[str])
    assert_type(Example().method.flat_start(1), Flatten[str])
    assert_type(Example.method.flat_call(Example(), 1), Flatten[str])
    assert_type(Example.method.flat_start(Example(), 1), Flatten[str])
    decorated.flat_call("value")  # type: ignore[arg-type]
    decorated.flat_start(1, label=2)  # type: ignore[arg-type]
    Example().method.flat_call("value")  # type: ignore[arg-type]
    Example().method.flat_start()  # type: ignore[call-arg]
    assert_type(Flatten(immediate()), Flatten[str])
    assert_type(Flatten(returned_async.__wrapped__()), Flatten[int])
    assert_type(Flatten(returned_coroutine.__wrapped__()), Flatten[int])
    assert_type(Flatten(returned_mixed.__wrapped__(1)), Flatten[int])
    assert_type(
        Flatten(returned_async.__wrapped__()).generate(), Generator[Any, Any, int]
    )
    assert_type(Flatten(mixed(), mode="start"), Flatten[str])
    bound: Callable[[int], str | Awaitable[str]] = Example().method
    assert_type(await await_result(bound(1)), str)
    assert_type(await await_result(decorated(1)), str)
    assert_type(run(immediate()), str | Awaitable[str])
    assert_type(run(mixed()), str | Awaitable[str])
    result: Awaitable[str] = await_result(run(mixed()))
    assert_type(result, Awaitable[str])
    assert_type(await await_result(run(immediate())), str)
    assert_type(await await_result(run(mixed())), str)
    assert_type(once_sync(1, scale=2), int)
    assert_type(once_async(1), Awaitable[int])
    assert_type(once_mixed(1), int | Awaitable[int])
    assert_type(await once_async(1), int)


def check_nested_return(value: Awaitable[Awaitable[int]]) -> None:
    def generate() -> Generator[Any, Any, Awaitable[Awaitable[int]]]:
        yield None
        return value

    assert_type(
        returned_nested(value),
        Awaitable[int] | Awaitable[Awaitable[int]],
    )
    assert_type(returned_nested.flat_call(value), Flatten[Awaitable[int]])
    assert_type(Flatten(generate()), Flatten[Awaitable[int]])
    assert_type(
        Flatten(generate()).generate(),
        Generator[Any, Any, Awaitable[int]],
    )
    assert_type(
        boxed(value),
        list[Awaitable[Awaitable[int]]] | Awaitable[list[Awaitable[Awaitable[int]]]],
    )


@once
@continuation
def once_continuation(value: int, /, *, scale: int = 1) -> Generator[Any, Any, int]:
    return (yield value * scale)


def check_once_continuation(value: Awaitable[Awaitable[int]]) -> None:
    assert_type(once_continuation(1, scale=2), int | Awaitable[int])
    assert_type(once_continuation.flat_call(1), Flatten[int])
    assert_type(once_continuation.flat_start(1), Flatten[int])
    assert_type(once(returned_nested).flat_call(value=value), Flatten[Awaitable[int]])
    once_continuation.flat_call("value")  # type: ignore[arg-type]
