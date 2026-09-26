from __future__ import annotations

import asyncio
import weakref
from inspect import isawaitable, signature

import pytest

from slyme.utils.execution import (
    Continuation,
    FlatResult,
    Flatten,
    SharedAwaitable,
    await_result,
    continuation,
    once,
    run,
)


@pytest.mark.parametrize("parentheses", [False, True])
def test_decorator_preserves_arguments_metadata_and_restarts_each_call(parentheses):
    events = []

    def execute(value, /, *extra, scale=2, **kwargs):
        """Scale the supplied values."""
        events.append(value)
        total = yield value + sum(extra) + sum(kwargs.values())
        return total * scale

    wrapped = (continuation() if parentheses else continuation)(execute)
    assert isinstance(wrapped, Continuation)
    assert events == []
    assert wrapped.__wrapped__ is execute
    assert wrapped.__name__ == execute.__name__
    assert wrapped.__doc__ == execute.__doc__
    assert wrapped(1, 2, scale=3, value=4) == 21
    assert wrapped(5) == 10
    assert events == [1, 5]


def test_bound_flat_calls_preserve_arguments_and_create_fresh_generators() -> None:
    events = []

    class Example:
        def __init__(self, offset):
            self.offset = offset

        def __bool__(self):
            return False

        @continuation
        def method(self, value, /, *extra, scale=2, **kwargs):
            """Run with this instance's offset."""
            events.append(self.offset)
            return (
                yield self.offset + (value + sum(extra) + sum(kwargs.values())) * scale
            )

    first, second = Example(10), Example(20)
    method = first.method
    assert method.__wrapped__.__self__ is first
    assert method.__name__ == "method"
    assert method.__doc__ == Example.method.__doc__
    assert signature(method) == signature(method.__wrapped__)

    def requests():
        return [
            method.flat_call(1, 2, scale=3, value=4),
            second.method.flat_start(1),
            Example.method.flat_call(second, 2),
        ]

    first_requests, second_requests = requests(), requests()
    assert all(isinstance(request, Flatten) for request in first_requests)
    assert all(
        first.generator is not second.generator
        for first, second in zip(first_requests, second_requests, strict=True)
    )
    assert events == []

    @continuation
    def execute(requests):
        results = []
        for request in requests:
            results.append((yield request))
        return results

    assert execute(first_requests) == [31, FlatResult(22, state="done"), 24]
    assert execute(second_requests) == [31, FlatResult(22, state="done"), 24]
    assert first.method(3) == 16
    assert Example.method(first, 4) == 18
    assert events == [10, 20, 20, 10, 20, 20, 10, 10]


@pytest.mark.parametrize(
    "mode", ["direct", "run", "call", "start", "raw_call", "raw_start"]
)
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("return_depth", [0, 1, 2])
async def test_flatten_and_continuation_preserve_return_values(
    mode, asynchronous, return_depth
) -> None:
    expected = object()
    for _ in range(return_depth):
        future = asyncio.get_running_loop().create_future()
        future.set_result(expected)
        expected = future

    def generate():
        if asynchronous:
            yield asyncio.sleep(0)
        return expected

    child = continuation(generate)

    @continuation
    def parent():
        if mode == "call":
            return (yield child.flat_call())
        if mode == "raw_call":
            return (yield Flatten(generate()))
        result = yield (
            child.flat_start() if mode == "start" else Flatten(generate(), mode="start")
        )
        assert isinstance(result, FlatResult)
        assert not isawaitable(result)
        assert result.state == ("pending" if asynchronous else "done")
        if result.state == "pending":
            result = yield result.value
            assert result.state == "done"
        return result.value

    if mode == "direct":
        result = child()
    elif mode == "run":
        result = run(generate())
    else:
        result = parent()
    if asynchronous:
        result = await result
    assert result is expected


async def test_generators_and_returned_requests_remain_data() -> None:
    events = []

    def data():
        events.append("consumed")
        yield 1

    value = data()

    @continuation
    def child():
        return (yield value)

    request = child.flat_call()
    flat_result = FlatResult(request, state="done")

    @continuation
    def return_request():
        yield None
        return request

    async def await_request():
        return request

    @continuation
    def parent():
        assert (yield value) is value
        assert (yield child.flat_call()) is value
        started = yield child.flat_start()
        assert started.state == "done"
        assert started.value is value
        assert (yield return_request.flat_call()) is request
        assert (yield await_request()) is request
        assert (yield flat_result) is flat_result
        assert (yield request) is value

    await await_result(parent())
    assert events == []
    value.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("kind", ["awaitable", "generator", "request", "result"])
async def test_deep_forwarding_preserves_business_data(asynchronous, kind) -> None:
    def unconsumed():
        pytest.fail("Returned generators and requests must remain data")
        yield

    generator = unconsumed()
    awaitable = asyncio.get_running_loop().create_future()
    awaitable.set_result("awaited")
    value = {
        "awaitable": awaitable,
        "generator": generator,
        "request": Flatten(generator),
        "result": FlatResult(awaitable, state="done"),
    }[kind]

    @continuation
    def forward(depth):
        if depth:
            return (yield forward.flat_call(depth - 1))
        if asynchronous:
            yield asyncio.sleep(0)
        return value

    @continuation
    def parent():
        started = yield forward.flat_start(3000)
        assert started.state == ("pending" if asynchronous else "done")
        if started.state == "pending":
            started = yield started.value
            assert started.state == "done"
        return started.value

    try:
        result = parent()
        if asynchronous:
            result = await result
        assert result is value
    finally:
        generator.close()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("raw", [False, True])
async def test_deep_calls_use_an_explicit_stack(asynchronous, raw) -> None:
    def descend(depth):
        if depth:
            request = (
                Flatten(descend(depth - 1)) if raw else wrapped.flat_call(depth - 1)
            )
            return 1 + (yield request)
        if asynchronous:
            yield asyncio.sleep(0)
        return 0

    wrapped = continuation(descend)
    result = run(descend(5000)) if raw else wrapped(5000)
    assert isawaitable(result) == asynchronous
    assert await await_result(result) == 5000


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_deep_starts_detach_only_the_nearest_branch(asynchronous) -> None:
    pending = []

    @continuation
    def descend(depth):
        if depth:
            result = yield descend.flat_start(depth - 1)
            if result.state == "pending":
                pending.append(result.value)
                return 1
            return result.value + 1
        if asynchronous:
            yield asyncio.sleep(0)
        return 0

    # Every outer branch can finish synchronously after the innermost start
    # hands its remainder back. No Python recursion is needed to unwind them.
    assert descend(5000) == 5000
    assert len(pending) == int(asynchronous)
    if pending:
        assert await pending[0] == FlatResult(0, state="done")


async def test_start_runs_sync_prefixes_and_leaves_batch_scheduling_to_caller() -> None:
    events = []
    started = set()
    both = asyncio.Event()

    async def operation(value):
        started.add(value)
        if len(started) == 2:
            both.set()
        await both.wait()
        return value * 2

    @continuation
    def branch(value):
        events.append(value)
        if value:
            return (yield operation(value))
        return 0

    async def join(pending):
        return await asyncio.gather(*pending)

    @continuation
    def batch():
        pending = []
        for value in range(3):
            result = yield branch.flat_start(value)
            if result.state == "pending":
                pending.append(result.value)
            else:
                assert result.value == 0
        results = yield join(pending)
        assert all(result.state == "done" for result in results)
        return [result.value for result in results]

    tasks = asyncio.all_tasks()
    pending = batch()
    assert events == [0, 1, 2]
    assert started == set()
    assert asyncio.all_tasks() == tasks
    assert await asyncio.wait_for(await_result(pending), 1) == [2, 4]
    assert asyncio.all_tasks() == tasks


async def test_nested_starts_restore_parent_stacks_across_multiple_awaits() -> None:
    events = []

    @continuation
    def leaf():
        events.append("leaf")
        yield asyncio.sleep(0)
        events.append("leaf resumed")
        return 2

    @continuation
    def middle():
        pending = yield leaf.flat_start()
        events.append("middle")
        yield asyncio.sleep(0)
        result = yield pending.value
        assert result.state == "done"
        return 3 + result.value

    @continuation
    def parent():
        pending = yield middle.flat_start()
        events.append("parent")
        value = yield leaf.flat_call()
        result = yield pending.value
        assert result.state == "done"
        return value + result.value

    pending = parent()
    assert events == ["leaf", "middle", "parent", "leaf"]
    assert await await_result(pending) == 7
    assert events.count("leaf resumed") == 2


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_nested_starts_preserve_explicitly_returned_states(asynchronous) -> None:
    returned = []

    @continuation
    def leaf():
        if asynchronous:
            yield asyncio.sleep(0)
        return 7

    @continuation
    def middle():
        result = yield leaf.flat_start()
        returned.append(result)
        return result

    @continuation
    def parent():
        return (yield middle.flat_start())

    # Middle finishes synchronously with a state object as its business value,
    # even when that object refers to a still-pending child execution.
    result = parent()
    assert result.state == "done"
    assert result.value is returned[0]
    child = result.value
    assert child.state == ("pending" if asynchronous else "done")
    if child.state == "pending":
        child = await child.value
    assert child == FlatResult(7, state="done")


async def test_deep_call_failures_unwind_through_async_cleanup() -> None:
    failure = ValueError("leaf")
    cleaned = []

    @continuation
    def descend(depth):
        try:
            if depth:
                yield descend.flat_call(depth - 1)
            else:
                raise failure
        finally:
            if depth % 500 == 0:
                yield asyncio.sleep(0)
            cleaned.append(depth)

    @continuation
    def parent():
        try:
            yield descend.flat_call(3000)
        except ValueError as error:
            assert error is failure
            return "recovered"

    assert await await_result(parent()) == "recovered"
    assert cleaned == list(range(3001))


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_start_failures_are_raised_at_start_or_when_awaited(asynchronous) -> None:
    failure = ValueError("branch")
    events = []

    @continuation
    def child():
        if asynchronous:
            yield asyncio.sleep(0)
        raise failure

    @continuation
    def parent():
        try:
            pending = yield child.flat_start()
            events.append("started")
            yield pending.value
        except ValueError as error:
            assert error is failure
            events.append("caught")
        return 1

    assert await await_result(parent()) == 1
    assert events == (["started", "caught"] if asynchronous else ["caught"])


@pytest.mark.parametrize("mode", ["call", "start"])
async def test_returned_awaitable_is_only_awaited_explicitly(mode) -> None:
    events = []
    failure = LookupError("returned")

    async def fail():
        events.append("await")
        raise failure

    @continuation
    def child():
        try:
            yield None
            return fail()
        except LookupError:
            pytest.fail("The child's returned awaitable is data")
        finally:
            events.append("finally")

    @continuation
    def parent():
        try:
            if mode == "call":
                value = yield child.flat_call()
            else:
                result = yield child.flat_start()
                assert result.state == "done"
                value = result.value
            events.append("returned")
            yield value
        except LookupError as error:
            assert error is failure
            events.append("caught")

    pending = parent()
    assert events == ["finally", "returned"]
    await await_result(pending)
    assert events == ["finally", "returned", "await", "caught"]


@pytest.mark.parametrize("mode", ["call", "start"])
async def test_cancelling_managed_calls_runs_child_and_parent_cleanup(mode) -> None:
    started = asyncio.Event()
    events = []

    async def operation():
        started.set()
        await asyncio.Event().wait()

    @continuation
    def child():
        try:
            yield operation()
        finally:
            yield asyncio.sleep(0)
            events.append("child")

    @continuation
    def parent():
        try:
            if mode == "call":
                yield child.flat_call()
            else:
                pending = yield child.flat_start()
                yield pending.value
        finally:
            yield asyncio.sleep(0)
            events.append("parent")

    tasks = asyncio.all_tasks()
    task = asyncio.create_task(await_result(parent()))
    await started.wait()
    assert asyncio.all_tasks() == tasks | {task}
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert events == ["child", "parent"]
    assert asyncio.all_tasks() == tasks


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
