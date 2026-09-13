from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from inspect import isawaitable

import pytest

from slyme.utils.continuation import BatchError, Continuation, Result, await_result


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


async def test_aunwrap_runs_sync_prefix_and_returns_an_awaitable() -> None:
    events = []
    chain = Continuation.call(lambda: events.append("call")).then(lambda _: 7)
    assert not isawaitable(chain)
    assert events == []
    result = chain.aunwrap()
    assert isawaitable(result)
    assert events == ["call"]
    assert await result == 7
    assert await Continuation(3).aunwrap() == 3


def test_aunwrap_preserves_immediate_synchronous_errors() -> None:
    def fail():
        raise ValueError("immediate")

    with pytest.raises(ValueError, match="immediate"):
        Continuation.call(fail).aunwrap()


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

    assert await Continuation(initial()).then(str).aunwrap() == "2"
    inner = Continuation(4)
    assert Continuation(inner).unwrap() is inner
    assert await Continuation(inner.unwrap()).aunwrap() == 4
    assert (
        await Continuation(2)
        .then(lambda value: Continuation(value + 3).unwrap())
        .aunwrap()
        == 5
    )
    data = [initial()]
    assert await Continuation(data).aunwrap() is data
    assert await data[0] == 2
    marker = object()
    assert Continuation(marker).unwrap() is marker


async def test_pending_execution_rejects_reuse_and_additional_steps() -> None:
    async def initial():
        return 2

    chain = Continuation.call(initial).then(lambda value: value + 1)
    pending = chain.unwrap()
    with pytest.raises(RuntimeError, match="once"):
        await chain.aunwrap()
    with pytest.raises(RuntimeError, match="extended"):
        chain.then(str)
    assert await await_result(pending) == 3


def test_callback_cannot_reenter_or_extend_its_executing_chain() -> None:
    chain = Continuation(1)

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
    chain = Continuation(payload).then(lambda value: None)
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
        await Continuation.call(operation)
        .catch(recover)
        .then(lambda value: value + 1)
        .aunwrap()
        == 6
    )
    assert (
        await Continuation.call(operation).then(str, lambda error: str(error)).aunwrap()
        == "failure"
    )
    with pytest.raises(ValueError) as caught:
        await Continuation.call(operation).then(str).aunwrap()
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

    chain = Continuation(initial() if asynchronous else 1).then(
        fail, lambda error: events.append("wrong handler")
    )
    assert await chain.catch(lambda error: str(error)).aunwrap() == "success failed"
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
        await chain.aunwrap()
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
        await chain.aunwrap()
    assert caught.value is cancellation
    assert events == []
    assert (
        await Continuation.call(operation)
        .catch(lambda error: 7, exceptions=BaseException)
        .aunwrap()
        == 7
    )
    assert (
        await Continuation.call(operation)
        .then(str, lambda error: "cancelled", exceptions=BaseException)
        .aunwrap()
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

    task = asyncio.create_task(Continuation.call(operation).aunwrap())
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

    sync = Continuation(0)
    asynchronous = Continuation(0)
    for _ in range(5000):
        sync = sync.then(increment)
        asynchronous = asynchronous.then(aincrement)
    assert sync.unwrap() == 5000
    assert await asynchronous.aunwrap() == 5000


def test_sequential_defers_calls_until_unwrap_and_collects_results() -> None:
    visited = []

    def call(value: int) -> int:
        visited.append(value)
        return value * 2

    chain = Continuation.sequential([1, 2, 3], call)
    assert visited == []
    assert chain.unwrap() == [2, 4, 6]
    assert visited == [1, 2, 3]
    assert Continuation.sequential([], call).unwrap() == []


async def test_sequential_consumes_iterators_after_previous_completion() -> None:
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

    chain = Continuation.sequential(values(), call)
    assert events == []
    result = chain.unwrap()
    assert events == ["yield:first"]
    assert await await_result(result) == [None, None]
    assert events == ["yield:first", "first", "finished", "yield:second", "second"]


def test_sequential_stops_consuming_when_a_call_fails() -> None:
    events = []

    def values():
        yield 1
        events.append("unreachable")
        yield 2

    def fail(value):
        raise ValueError(value)

    with pytest.raises(BatchError) as caught:
        Continuation.sequential(values(), fail).unwrap()
    assert len(caught.value.results) == 1
    assert isinstance(caught.value.results[0].error, ValueError)
    assert events == []


async def test_sequential_supports_recovery_without_consuming_remaining_inputs() -> (
    None
):
    visited = []

    async def fail(value):
        visited.append(value)
        raise ValueError("failed")

    result = await (
        Continuation.sequential([1, 2], fail)
        .catch(lambda error: str(error.results[0].error), exceptions=BatchError)
        .then(lambda result: (result, "done"))
        .aunwrap()
    )
    assert result == ("failed", "done")
    assert visited == [1]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("continue_on_error", [False, True])
async def test_sequential_retains_successes_and_failures(
    asynchronous: bool,
    continue_on_error: bool,
) -> None:
    failure = ValueError("failure")
    visited = []

    def call(value):
        visited.append(value)
        if value in (1, 3):
            raise failure
        return failure if value == 2 else None

    async def acall(value):
        await asyncio.sleep(0)
        return call(value)

    with pytest.raises(BatchError) as caught:
        await Continuation.sequential(
            range(5),
            acall if asynchronous else call,
            continue_on_error=continue_on_error,
        ).aunwrap()
    expected = [Result(value=None), Result(error=failure)]
    if continue_on_error:
        expected += [Result(value=failure), Result(error=failure), Result(value=None)]
    assert caught.value.results == expected
    assert visited == list(range(len(expected)))


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("continue_on_error", [False, True])
async def test_sequential_records_iterator_failure(
    asynchronous: bool,
    continue_on_error: bool,
) -> None:
    failure = ValueError("iterator")

    def values():
        yield 1
        raise failure

    async def acall(value):
        return value

    with pytest.raises(BatchError) as caught:
        await Continuation.sequential(
            values(),
            acall if asynchronous else lambda value: value,
            continue_on_error=continue_on_error,
        ).aunwrap()
    assert caught.value.results == [Result(value=1), Result(error=failure)]


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("continue_on_error", [False, True])
async def test_sequential_cancellation_obeys_continue_on_error(
    asynchronous: bool,
    continue_on_error: bool,
) -> None:
    cancellation = asyncio.CancelledError("cleanup")
    visited = []

    def call(value):
        visited.append(value)
        if value == 0:
            raise cancellation
        return value

    async def acall(value):
        return call(value)

    with pytest.raises(BatchError) as caught:
        await Continuation.sequential(
            [0, 1],
            acall if asynchronous else call,
            continue_on_error=continue_on_error,
        ).aunwrap()
    assert caught.value.results[0].error is cancellation
    assert visited == ([0, 1] if continue_on_error else [0])


@pytest.mark.parametrize("method", [Continuation.sequential, Continuation.batch])
def test_collection_records_failure_to_create_iterator(method) -> None:
    failure = ValueError("iteration unavailable")

    class Values:
        def __iter__(self):
            raise failure

    with pytest.raises(BatchError) as caught:
        method(Values(), str).unwrap()
    assert caught.value.results == [Result(error=failure)]


async def test_long_sequential_execution_has_bounded_stack_and_no_hidden_tasks() -> (
    None
):
    async def acall(value):
        return value

    tasks = asyncio.all_tasks()
    assert Continuation.sequential(range(5000), int).unwrap() == list(range(5000))
    assert await Continuation.sequential(range(5000), acall).aunwrap() == list(
        range(5000)
    )
    assert asyncio.all_tasks() == tasks
