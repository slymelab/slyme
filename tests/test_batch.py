from __future__ import annotations

import asyncio
from inspect import isawaitable

import pytest

from slyme.node.eval import _batch
from slyme.utils.continuation import await_result, run
from slyme.utils.exception import BatchError, Result


def test_result_supports_typed_construction_and_exception_values() -> None:
    error = ValueError("data or failure")
    assert Result[ValueError](value=error) == Result(value=error)
    assert Result[ValueError](value=error) != Result[ValueError](error=error)
    assert Result[None](value=None) == Result()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_failed_batch_retains_none_and_exception_return_values(
    asynchronous: bool,
) -> None:
    error = ValueError("data and failure")

    def call(value):
        if value == 1:
            raise error
        return error if value == 0 else None

    async def acall(value):
        await asyncio.sleep(0)
        return call(value)

    with pytest.raises(BatchError) as caught:
        await await_result(_batch(range(3), acall if asynchronous else call))
    assert caught.value.results == [Result(value=error), Result(error=error), Result()]


@pytest.mark.parametrize("values", [[1, 2], (1, 2), range(1, 3)])
def test_sync_evaluation_batch_runs_immediately(values) -> None:
    visited = []

    def call(value):
        visited.append(value)
        return value * 2

    assert _batch(values, call) == [2, 4]
    assert visited == [1, 2]
    assert _batch([], call) == []


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

    with pytest.raises(BatchError) as caught:
        await await_result(_batch(range(3), acall if asynchronous else call))
    assert caught.value.results == [
        Result(error=failure),
        Result(value=7),
        Result(error=failure),
    ]
    assert visited == [0, 1, 2]


async def test_batch_calls_all_items_before_scheduling_and_keeps_result_order() -> None:
    events = []
    release = asyncio.Event()

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

    before = asyncio.all_tasks()
    pending = _batch([0, 1, 2], call)
    assert isawaitable(pending)
    assert asyncio.all_tasks() == before
    assert events == [
        ("call", 0),
        ("call", 1),
        ("call", 2),
    ]
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

    with pytest.raises(BatchError) as caught:
        await await_result(_batch([0, 1], call))
    results = caught.value.results
    assert isinstance(results[0].error, ValueError)
    assert results[1] == Result(value=2)
    assert visited == ["later"]


async def test_batch_keeps_exceptions_as_data_and_nested_error_indices() -> None:
    data = ValueError("data")

    async def call(value):
        return data

    assert await await_result(_batch([1, 2], call)) == [data, data]

    def fail(value):
        raise data

    with pytest.raises(BatchError) as caught:
        _batch([1], lambda value: _batch([2], fail))
    inner = caught.value.results[0].error
    assert isinstance(inner, BatchError)
    assert inner.results == [Result(error=data)]


def test_caller_handles_batch_and_postprocessing_errors_with_except() -> None:
    def fail(value):
        raise LookupError(value)

    def recover():
        try:
            values = yield _batch([1], fail)
        except BatchError as error:
            values = [len(error.results)]
        return values[0] + 1

    def postprocess():
        try:
            values = yield _batch([], str)
            return values[0]
        except IndexError:
            return "index"

    assert run(recover()) == 2
    assert run(postprocess()) == "index"


async def test_batch_schedules_only_asynchronous_items() -> None:
    caller = asyncio.current_task()
    sync_tasks = []
    async_tasks = []

    async def acall(value):
        async_tasks.append(asyncio.current_task())
        await asyncio.sleep(0)
        return value

    def call(value):
        if value % 2 == 0:
            return acall(value)
        sync_tasks.append(asyncio.current_task())
        return value

    before = asyncio.all_tasks()
    pending = _batch(range(5), call)
    assert asyncio.all_tasks() == before
    assert await pending == list(range(5))
    assert sync_tasks == [caller, caller]
    assert len(set(async_tasks)) == 3
    assert caller not in async_tasks
    assert asyncio.all_tasks() == before


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
        await await_result(_batch([0, 1], call))
    assert isinstance(caught.value.results[0].error, asyncio.CancelledError)
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

    task = asyncio.create_task(await_result(_batch([1, 2], call)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert finished == [1, 2]
    assert caught.value.__cause__ is None


async def test_caller_cancellation_propagates_without_aggregating_partial_failures() -> (
    None
):
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

    task = asyncio.create_task(await_result(_batch([0, 1], call)))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError) as caught:
        await task
    assert caught.value.__cause__ is None
    assert finished == [1]
