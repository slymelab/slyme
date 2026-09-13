from __future__ import annotations

import asyncio
from inspect import isawaitable

import pytest

from slyme.utils.continuation import BatchError, Continuation


def test_sync_batch_building_is_lazy_and_results_stay_synchronous() -> None:
    visited = []

    def values():
        visited.append("iterate")
        yield 1
        yield 2

    def call(value):
        visited.append(value)
        return value * 2

    chain = Continuation.batch(values(), call).then(tuple)
    assert visited == []
    assert chain.unwrap() == (2, 4)
    assert visited == ["iterate", 1, 2]
    assert Continuation.batch([], call).unwrap() == []


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_batch_collects_failures_at_input_indices(asynchronous: bool) -> None:
    failure = ValueError("shared failure")
    visited = []

    def call(value):
        visited.append(value)
        if value != 1:
            raise failure
        return 7

    async def acall(value):
        await asyncio.sleep(0)
        return call(value)

    chain = Continuation.batch(range(3), acall if asynchronous else call)
    with pytest.raises(BatchError) as caught:
        await chain.aunwrap()
    assert caught.value.errors == {0: failure, 2: failure}
    assert visited == [0, 1, 2]


async def test_batch_schedules_after_first_awaitable_and_keeps_result_order() -> None:
    events = []
    release = asyncio.Event()

    def values():
        for value in range(3):
            events.append(("input", value))
            yield value

    async def waiting():
        events.append("waiting")
        await release.wait()
        return "async"

    def call(value):
        events.append(("call", value))
        if value == 1:
            return waiting()
        if value == 2:
            release.set()
        return value

    chain = Continuation.batch(values(), call)
    before = asyncio.all_tasks()
    pending = chain.unwrap()
    assert isawaitable(pending)
    assert asyncio.all_tasks() == before
    assert events == [("input", 0), ("call", 0), ("input", 1), ("call", 1)]
    assert await pending == [0, "async", 2]


async def test_batch_waits_for_later_calls_after_sync_failure() -> None:
    visited = []

    async def later():
        visited.append("later")
        return 2

    def call(value):
        if value == 0:
            raise ValueError("first")
        return later()

    chain = Continuation.batch([0, 1], call)
    assert await chain.catch(lambda error: list(error.errors)).aunwrap() == [0]
    assert visited == ["later"]


async def test_batch_keeps_exceptions_as_data_and_nested_error_indices() -> None:
    data = ValueError("data")

    async def call(value):
        return data

    assert await Continuation.batch([1, 2], call).aunwrap() == [data, data]

    def fail(value):
        raise data

    with pytest.raises(BatchError) as caught:
        Continuation.batch(
            [1], lambda value: Continuation.batch([2], fail).unwrap()
        ).unwrap()
    inner = caught.value.errors[0]
    assert isinstance(inner, BatchError)
    assert inner.errors == {0: data}


def test_batch_catch_handles_aggregate_errors_and_later_callback_errors() -> None:
    def fail(value):
        raise LookupError(value)

    assert (
        Continuation.batch([1], fail)
        .catch(lambda error: [len(error.errors)], exceptions=BatchError)
        .then(lambda values: values[0] + 1)
        .unwrap()
    ) == 2
    assert (
        Continuation.batch([], str)
        .then(lambda values: values[0])
        .catch(lambda error: "index", exceptions=IndexError)
        .unwrap()
    ) == "index"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_iterator_failure_waits_for_already_started_calls(
    asynchronous: bool,
) -> None:
    finished = []
    failure = ValueError("iterator")

    def values():
        yield 0
        raise failure

    def call(value):
        finished.append(value)
        return value

    async def acall(value):
        await asyncio.sleep(0)
        return call(value)

    with pytest.raises(BatchError) as caught:
        await Continuation.batch(values(), acall if asynchronous else call).aunwrap()
    assert caught.value.errors == {1: failure}
    assert finished == [0]


async def test_item_cancellation_is_a_batch_failure_without_cancelling_siblings() -> (
    None
):
    finished = []

    async def call(value):
        if value == 0:
            raise asyncio.CancelledError("item")
        await asyncio.sleep(0)
        finished.append(value)
        return value

    with pytest.raises(BatchError) as caught:
        await Continuation.batch([0, 1], call).aunwrap()
    assert isinstance(caught.value.errors[0], asyncio.CancelledError)
    assert finished == [1]


@pytest.mark.parametrize("suppress", [False, True])
async def test_caller_cancellation_waits_for_submitted_calls(suppress: bool) -> None:
    started = asyncio.Event()
    finished = []

    async def call(value):
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await asyncio.sleep(0)
            if not suppress:
                raise
        finally:
            finished.append(value)
        return value

    task = asyncio.create_task(Continuation.batch([1, 2], call).aunwrap())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert finished == [1, 2]
    assert caught.value.__cause__ is None


async def test_caller_cancellation_preserves_other_failures() -> None:
    started = asyncio.Event()
    failure = ValueError("failed before cancellation")
    finished = []

    async def call(value):
        if value == 0:
            raise failure
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            finished.append(value)

    task = asyncio.create_task(Continuation.batch([0, 1], call).aunwrap())
    await started.wait()
    task.cancel()
    with pytest.raises(BatchError) as caught:
        await task
    assert caught.value.errors[0] is failure
    assert isinstance(caught.value.errors[1], asyncio.CancelledError)
    assert isinstance(caught.value.__cause__, asyncio.CancelledError)
    assert finished == [1]
