from __future__ import annotations

import asyncio
import weakref
from inspect import isawaitable

import pytest

from slyme.utils.execution import SharedAwaitable, await_result, continuation, once, run


@pytest.mark.parametrize("parentheses", [False, True])
def test_decorator_preserves_arguments_metadata_and_restarts_each_call(parentheses):
    events = []

    def execute(value, /, *extra, scale=2, **kwargs):
        """Scale the supplied values."""
        events.append(value)
        total = yield value + sum(extra) + sum(kwargs.values())
        return total * scale

    wrapped = (continuation() if parentheses else continuation)(execute)
    assert events == []
    assert wrapped.__wrapped__ is execute
    assert wrapped.__name__ == execute.__name__
    assert wrapped.__doc__ == execute.__doc__
    assert wrapped(1, 2, scale=3, value=4) == 21
    assert wrapped(5) == 10
    assert events == [1, 5]


@pytest.mark.parametrize("parentheses", [False, True])
async def test_decorated_calls_have_independent_unscheduled_async_remainders(
    parentheses,
):
    events = []

    async def pending(value):
        events.append(("await", value))
        await asyncio.sleep(0)
        return value * 2

    def execute(value):
        events.append(("call", value))
        result = yield pending(value)
        return result + 1

    wrapped = (continuation() if parentheses else continuation)(execute)
    tasks = asyncio.all_tasks()
    first, second = wrapped(1), wrapped(2)
    assert events == [("call", 1), ("call", 2)]
    assert asyncio.all_tasks() == tasks
    assert await asyncio.gather(first, second) == [3, 5]


@pytest.mark.parametrize("parentheses", [False, True])
async def test_decorated_errors_propagate_at_their_execution_point(parentheses):
    failure = ValueError("failure")

    async def fail():
        raise failure

    def execute(asynchronous):
        if asynchronous:
            yield fail()
        else:
            raise failure

    wrapped = (continuation() if parentheses else continuation)(execute)
    with pytest.raises(ValueError) as synchronous:
        wrapped(False)
    assert synchronous.value is failure
    result = wrapped(True)
    with pytest.raises(ValueError) as asynchronous:
        await result
    assert asynchronous.value is failure


def test_generator_building_is_lazy_and_run_is_immediate() -> None:
    events = []

    def execute():
        events.append("start")
        first = yield 2
        for value in range(first):
            events.append((yield value))
        return first * 3

    generator = execute()
    assert events == []
    assert run(generator) == 6
    assert events == ["start", 0, 1]
    assert run(generator) is None  # Exhaustion follows the generator protocol.


def test_generator_can_return_without_yielding() -> None:
    def execute(values):
        yield from values
        return 7

    assert run(execute([])) == 7


async def test_run_stops_at_first_awaitable_without_scheduling() -> None:
    events = []

    async def asynchronous(value):
        events.append("await")
        await asyncio.sleep(0)
        return value + 1

    def execute():
        events.append("call")
        value = yield 1
        value = yield asynchronous(value)
        events.append("resumed")
        return value * 2

    tasks = asyncio.all_tasks()
    result = run(execute())
    assert isawaitable(result)
    assert events == ["call"]
    assert asyncio.all_tasks() == tasks
    assert await result == 4
    assert events == ["call", "await", "resumed"]
    assert asyncio.all_tasks() == tasks


def test_async_remainder_can_be_created_outside_an_event_loop() -> None:
    async def value():
        await asyncio.sleep(0)
        return 5

    def execute():
        result = yield value()
        return result + 1

    pending = run(execute())
    assert isawaitable(pending)
    assert asyncio.run(await_result(pending)) == 6


async def test_yielded_containers_and_nested_awaitables_are_not_unwrapped() -> None:
    async def value():
        return 3

    pending = value()
    container = [pending]
    failure = ValueError("ordinary data")

    async def return_pending():
        return pending

    def execute():
        assert (yield container) is container
        assert (yield failure) is failure
        assert (yield None) is None
        assert (yield return_pending()) is pending
        return (yield pending)

    assert await await_result(run(execute())) == 3


async def test_final_return_value_is_not_implicitly_awaited() -> None:
    async def value():
        return 4

    pending = value()

    def execute():
        yield 1
        return pending

    assert run(execute()) is pending
    assert await pending == 4

    pending = value()

    def asynchronous():
        yield asyncio.sleep(0)
        return pending

    assert await await_result(run(asynchronous())) is pending
    assert await pending == 4


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("recover_asynchronously", [False, True])
async def test_same_except_handles_sync_and_async_failure(
    asynchronous: bool, recover_asynchronously: bool
) -> None:
    failure = ValueError("operation")

    def fail():
        raise failure

    async def afail():
        await asyncio.sleep(0)
        fail()

    async def recover():
        await asyncio.sleep(0)
        return 7

    def execute():
        try:
            yield afail() if asynchronous else fail()
        except ValueError as error:
            assert error is failure
            return (yield recover()) if recover_asynchronously else 7

    assert await await_result(run(execute())) == 7


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt, SystemExit])
async def test_unhandled_errors_preserve_identity(asynchronous, error_type) -> None:
    failure = error_type("failure")

    async def fail():
        await asyncio.sleep(0)
        raise failure

    def execute():
        if asynchronous:
            yield fail()
        else:
            raise failure

    with pytest.raises(error_type) as caught:
        await await_result(run(execute()))
    assert caught.value is failure


async def test_exception_handler_errors_reach_outer_except() -> None:
    original = ValueError("original")
    replacement = LookupError("replacement")

    async def fail():
        raise original

    def execute():
        try:
            try:
                yield fail()
            except ValueError as error:
                raise replacement from error
        except LookupError as error:
            assert error is replacement
            assert error.__cause__ is original
            return "recovered"

    assert await await_result(run(execute())) == "recovered"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_finally_can_yield_cleanup_after_success_or_failure(asynchronous) -> None:
    events = []

    async def cleanup():
        await asyncio.sleep(0)
        events.append("cleanup")

    def execute(fail):
        try:
            yield 1
            if fail:
                raise ValueError("operation")
            return 5
        finally:
            yield cleanup() if asynchronous else events.append("cleanup")

    assert await await_result(run(execute(False))) == 5
    with pytest.raises(ValueError, match="operation"):
        await await_result(run(execute(True)))
    assert events == ["cleanup", "cleanup"]


async def test_cancellation_is_thrown_at_yield_and_finally_can_await() -> None:
    started = asyncio.Event()
    events = []

    async def operation():
        started.set()
        await asyncio.Event().wait()

    def execute():
        try:
            yield operation()
        finally:
            yield asyncio.sleep(0)
            events.append("cleanup")

    before = asyncio.all_tasks()
    task = asyncio.create_task(await_result(run(execute())))
    await started.wait()
    assert asyncio.all_tasks() == before | {task}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["cleanup"]
    assert asyncio.all_tasks() == before


async def test_cancellation_can_be_handled_by_generator() -> None:
    started = asyncio.Event()

    async def operation():
        started.set()
        await asyncio.Event().wait()

    def execute():
        try:
            yield operation()
        except asyncio.CancelledError:
            return "handled"

    task = asyncio.create_task(await_result(run(execute())))
    await started.wait()
    task.cancel()
    assert await task == "handled"


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_delegated_generators_share_the_driver(asynchronous) -> None:
    def inner():
        value = yield asyncio.sleep(0, result=4) if asynchronous else 4
        return value + 1

    def outer():
        first = yield from inner()
        second = yield run(inner())
        return first + second

    assert await await_result(run(outer())) == 10


async def test_long_loops_use_bounded_execution_stack() -> None:
    async def increment(value):
        return value + 1

    def execute(asynchronous):
        value = 0
        for _ in range(10000):
            value = yield increment(value) if asynchronous else value + 1
        return value

    assert run(execute(False)) == 10000
    assert await await_result(run(execute(True))) == 10000


def test_completed_generator_releases_local_values() -> None:
    class Payload:
        pass

    payload = Payload()
    reference = weakref.ref(payload)

    def execute(value):
        yield value

    generator = execute(payload)
    del payload
    assert run(generator) is None
    assert reference() is None


def test_caller_controls_batch_scheduling_and_sync_fast_path() -> None:
    events = []
    started = set()
    both = asyncio.Event()

    async def operation(value):
        events.append(("start", value))
        started.add(value)
        if len(started) == 2:
            both.set()
        await both.wait()
        return value * 2

    async def gather(pending):
        return await asyncio.wait_for(asyncio.gather(*pending), 1)

    def execute(asynchronous):
        results = []
        pending = {}
        for value in range(4):
            events.append(("call", value))
            result = operation(value) if asynchronous and value % 2 else value * 2
            if isawaitable(result):
                pending[value] = result
            results.append(result)
        if pending:
            resolved = yield gather(pending.values())
            for index, value in zip(pending, resolved, strict=True):
                results[index] = value
        return results

    assert run(execute(False)) == [0, 2, 4, 6]
    events.clear()
    result = run(execute(True))
    assert events == [("call", value) for value in range(4)]
    assert asyncio.run(await_result(result)) == [0, 2, 4, 6]
    assert started == {1, 3}


@pytest.mark.parametrize("result", [None, False, 0, ValueError("returned")])
def test_once_preserves_first_arguments_and_result(result) -> None:
    calls = []

    @once
    def operation(value, *, option):
        calls.append((value, option))
        return result

    assert operation(1, option=2) is result
    assert operation(3, option=4) is result
    assert calls == [(1, 2)]


@pytest.mark.parametrize("failure", [ValueError("failed"), asyncio.CancelledError()])
def test_once_replays_sync_failure_without_retry(failure) -> None:
    calls = []

    @once
    def operation():
        calls.append(1)
        raise failure

    for _ in range(2):
        with pytest.raises(type(failure)) as caught:
            operation()
        assert caught.value is failure
    assert calls == [1]


def test_once_reentry_can_be_handled_by_the_original_call() -> None:
    @once
    def operation():
        with pytest.raises(RuntimeError, match="re-enter"):
            operation()
        return 42

    assert operation() == operation() == 42


def test_once_releases_callback_after_sync_success() -> None:
    class Callback:
        def __call__(self):
            return 42

    callback = Callback()
    reference = weakref.ref(callback)
    operation = once(callback)
    del callback
    assert operation() == 42
    assert reference() is None


@pytest.mark.parametrize("fail", [False, True])
async def test_once_shares_lazy_async_result_and_failure(fail) -> None:
    calls = []
    failure = ValueError("failed")

    @once
    async def operation():
        calls.append(1)
        await asyncio.sleep(0)
        if fail:
            raise failure
        return 42

    result = operation()
    assert isinstance(result, SharedAwaitable)
    assert calls == []
    for _ in range(2):
        assert operation() is result
        if fail:
            with pytest.raises(ValueError) as caught:
                await result
            assert caught.value is failure
        else:
            assert await result == 42
    assert calls == [1]


async def test_shared_waiter_cancellation_does_not_cancel_operation() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = []

    async def operation():
        calls.append(1)
        started.set()
        await finish.wait()
        return 42

    shared = SharedAwaitable(operation())
    first = asyncio.create_task(await_result(shared))
    second = asyncio.ensure_future(shared)
    await started.wait()
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    finish.set()
    assert await second == 42
    assert await shared == 42
    assert calls == [1]


async def test_shared_operation_cannot_wait_for_itself() -> None:
    @once
    async def operation():
        return await operation()

    with pytest.raises(RuntimeError, match="own completion"):
        await asyncio.wait_for(await_result(operation()), 1)
