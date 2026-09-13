from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from inspect import isawaitable

import pytest

from slyme.utils.continuation import Continuation, await_result


def test_chain_building_is_in_place_and_execution_is_single_use() -> None:
    events = []

    def initial():
        events.append("call")
        return 2

    root = Continuation.call(initial)
    left = root.then(lambda value: value + 1)
    right = root.then(lambda value: value * 3).catch(lambda error: 0)
    assert events == []
    assert left is root is right
    assert root.unwrap() == 9
    assert events == ["call"]
    with pytest.raises(RuntimeError, match="once"):
        root.unwrap()
    with pytest.raises(RuntimeError, match="extended"):
        root.then(str)
    with pytest.raises(RuntimeError, match="extended"):
        root.catch(str)


async def test_await_defers_sync_prefix_until_the_await_iterator_runs() -> None:
    events = []
    chain = Continuation.call(lambda: events.append("call")).then(lambda _: 7)
    iterator = chain.__await__()
    assert events == []
    with pytest.raises(StopIteration) as caught:
        next(iterator)
    assert caught.value.value == 7
    assert events == ["call"]
    assert await Continuation.resolve(3) == 3


async def test_unwrap_stops_at_async_result_without_scheduling() -> None:
    events = []

    async def asynchronous(value):
        events.append("await")
        await asyncio.sleep(0)
        return value + 1

    def first():
        events.append("call")
        return 1

    chain = Continuation.call(first).then(asynchronous).then(lambda value: value * 2)
    tasks = asyncio.all_tasks()
    result = chain.unwrap()
    assert isawaitable(result)
    assert events == ["call"]
    assert asyncio.all_tasks() == tasks
    assert await result == 4
    assert events == ["call", "await"]


async def test_root_awaitable_and_nested_continuations_preserve_results() -> None:
    async def initial():
        return 2

    assert await Continuation.resolve(initial()).then(str) == "2"
    assert await Continuation.resolve(Continuation.resolve(4)) == 4
    assert (
        await Continuation.resolve(2).then(
            lambda value: Continuation.resolve(value + 3)
        )
        == 5
    )
    data = [initial()]
    assert await Continuation.resolve(data) is data
    assert await data[0] == 2
    marker = object()
    assert Continuation.resolve(marker).unwrap() is marker


async def test_pending_execution_rejects_reuse_and_additional_steps() -> None:
    async def initial():
        return 2

    chain = Continuation.call(initial).then(lambda value: value + 1)
    pending = chain.unwrap()
    with pytest.raises(RuntimeError, match="once"):
        await chain
    with pytest.raises(RuntimeError, match="extended"):
        chain.then(str)
    assert await await_result(pending) == 3


def test_callback_cannot_reenter_or_extend_its_executing_chain() -> None:
    chain = Continuation.resolve(1)

    def call(value):
        with pytest.raises(RuntimeError, match="once"):
            chain.unwrap()
        with pytest.raises(RuntimeError, match="extended"):
            chain.catch(str)
        return value

    assert chain.then(call).unwrap() == 1


def test_consumed_chain_releases_its_input_and_callbacks() -> None:
    import weakref

    class Payload:
        pass

    payload = Payload()
    reference = weakref.ref(payload)
    chain = Continuation.resolve(payload).then(lambda value: None)
    del payload
    assert reference() is not None
    assert chain.unwrap() is None
    assert reference() is None


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_recovery_handles_sync_and_async_errors(asynchronous: bool) -> None:
    failure = ValueError("failure")

    def fail():
        raise failure

    async def afail():
        await asyncio.sleep(0)
        fail()

    async def recover(error):
        assert error is failure
        await asyncio.sleep(0)
        return 5

    operation = afail if asynchronous else fail
    assert (
        await Continuation.call(operation).catch(recover).then(lambda value: value + 1)
        == 6
    )
    assert (
        await Continuation.call(operation).then(str, lambda error: str(error))
        == "failure"
    )
    with pytest.raises(ValueError) as caught:
        await Continuation.call(operation).then(str)
    assert caught.value is failure


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_paired_recovery_does_not_catch_its_success_callback_error(
    asynchronous: bool,
) -> None:
    events = []

    def fail(value):
        raise ValueError("success failed")

    async def initial():
        return 1

    chain = Continuation.resolve(initial() if asynchronous else 1).then(
        fail, lambda error: events.append("wrong handler")
    )
    assert await chain.catch(lambda error: str(error)) == "success failed"
    assert events == []


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_recovery_can_fail_or_skip_a_nonmatching_exception(
    asynchronous: bool,
) -> None:
    def fail():
        raise ValueError("original")

    async def afail():
        fail()

    def recover(error):
        raise LookupError("recovery") from error

    chain = (
        Continuation.call(afail if asynchronous else fail)
        .catch(lambda error: "wrong", exceptions=KeyError)
        .catch(recover)
    )
    with pytest.raises(LookupError) as caught:
        await chain
    assert isinstance(caught.value.__cause__, ValueError)


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_cancellation_propagates_unless_explicitly_selected(
    asynchronous: bool,
) -> None:
    cancellation = asyncio.CancelledError()
    events = []

    def cancel():
        raise cancellation

    async def acancel():
        cancel()

    operation = acancel if asynchronous else cancel
    chain = Continuation.call(operation).catch(
        lambda error: events.append("wrong handler")
    )
    with pytest.raises(asyncio.CancelledError) as caught:
        await chain
    assert caught.value is cancellation
    assert events == []
    assert (
        await Continuation.call(operation).catch(
            lambda error: 7, exceptions=BaseException
        )
        == 7
    )
    assert (
        await Continuation.call(operation).then(
            str, lambda error: "cancelled", exceptions=BaseException
        )
        == "cancelled"
    )


async def test_waiter_cancellation_reaches_the_operation_without_hidden_tasks() -> None:
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def operation():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    task = asyncio.create_task(await_result(Continuation.call(operation)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


async def test_long_chains_use_bounded_execution_stack() -> None:
    def increment(value):
        return value + 1

    async def aincrement(value):
        return value + 1

    sync = Continuation.resolve(0)
    asynchronous = Continuation.resolve(0)
    for _ in range(5000):
        sync = sync.then(increment)
        asynchronous = asynchronous.then(aincrement)
    assert sync.unwrap() == 5000
    assert await asynchronous == 5000


def test_each_runs_synchronous_calls_immediately_and_discards_results() -> None:
    visited = []

    def call(value: int) -> int:
        visited.append(value)
        return value * 2

    assert Continuation.each([1, 2, 3], call) is None
    assert visited == [1, 2, 3]


async def test_each_consumes_iterators_after_previous_completion() -> None:
    events = []

    def values():
        events.append("yield:first")
        yield 1
        events.append("yield:second")
        yield 2

    async def first():
        events.append("first")
        await asyncio.sleep(0)
        events.append("finished")

    def call(value) -> None | Awaitable[None]:
        return first() if value == 1 else events.append("second")

    result = Continuation.each(values(), call)
    assert events == ["yield:first"]
    await await_result(result)
    assert events == ["yield:first", "first", "finished", "yield:second", "second"]


def test_each_stops_consuming_when_a_call_fails() -> None:
    events = []

    def values():
        yield 1
        events.append("unreachable")
        yield 2

    def fail(value):
        raise ValueError(value)

    with pytest.raises(ValueError):
        Continuation.each(values(), fail)
    assert events == []
