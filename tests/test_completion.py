from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable

import pytest

from slyme.context import Context
from slyme.context.default import EVALUATORS_REF
from slyme.node import Auto, Node, eval_tree, node, sequential_exec, wrapper
from slyme.node.exception import NodeExceptionRecord, WrapperExceptionRecord
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result, continuation


async def test_await_result_preserves_values_and_only_awaits_outer_completion() -> None:
    async def value() -> int:
        return 3

    data = [value()]
    assert await await_result(data) is data
    assert await await_result(data[0]) == 3
    assert await await_result(None) is None
    assert await await_result(2) == 2


def test_sync_call_and_disposal_need_no_event_loop() -> None:
    @node
    def value(ctx: Context, /) -> int:
        return 3

    ctx = Context()
    assert value()(ctx) == 3
    assert ctx.dispose() is None


async def test_regular_function_returning_awaitable_needs_no_mode() -> None:
    events: list[str] = []

    @node
    def child(ctx: Context, /) -> Awaitable[int]:
        events.append("call")

        async def finish() -> int:
            events.append("await")
            return 4

        return finish()

    @node
    def parent(ctx: Context, /, *, value: int) -> int:
        events.append("parent")
        return value + 1

    ctx = Context()
    initial_effects = tuple(ctx._lifecycle._effects)
    graph = parent(value=Auto(child()))
    pending = graph(ctx)
    assert inspect.isawaitable(pending)
    assert events == ["call"]
    assert await await_result(pending) == 5
    assert events == ["call", "await", "parent"]
    assert tuple(ctx._lifecycle._effects) == initial_effects
    ctx.dispose()


async def test_sync_parent_waits_for_concurrent_auto_children() -> None:
    started: set[int] = set()
    both = asyncio.Event()

    @node
    async def child(ctx: Context, /, *, index: int) -> int:
        started.add(index)
        if len(started) == 2:
            both.set()
        await both.wait()
        return index

    @node
    def parent(ctx: Context, /, *, values: list[int]) -> int:
        return sum(values)

    ctx = Context()
    initial_effects = tuple(ctx._lifecycle._effects)
    result = parent(values=Auto([child(index=1), child(index=2)]))(ctx)
    assert await asyncio.wait_for(await_result(result), 1) == 3
    assert tuple(ctx._lifecycle._effects) == initial_effects
    ctx.dispose()


async def test_auto_calls_every_sync_prefix_before_scheduling() -> None:
    events = []

    @node
    def child(ctx: Context, /, *, index: int):
        events.append(("call", index))

        async def finish():
            events.append(("await", index))
            await asyncio.sleep(0)
            return index

        return finish()

    @node
    def parent(ctx: Context, /, *, values: list[int]) -> int:
        return sum(values)

    ctx = Context()
    initial_effects = tuple(ctx._lifecycle._effects)
    before = asyncio.all_tasks()
    pending = parent(values=Auto([child(index=1), child(index=2)]))(ctx)
    assert events == [("call", 1), ("call", 2)]
    assert asyncio.all_tasks() == before
    assert await await_result(pending) == 3
    assert events == [("call", 1), ("call", 2), ("await", 1), ("await", 2)]
    assert tuple(ctx._lifecycle._effects) == initial_effects
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_node_sequence_stops_at_first_failure(asynchronous) -> None:
    failure = ValueError("sequence")
    calls = []

    @node
    def child(ctx: Context, /, *, index: int):
        def execute():
            calls.append(index)
            if index == 1:
                raise failure
            return index

        async def finish():
            await asyncio.sleep(0)
            return execute()

        return finish() if asynchronous else execute()

    ctx = Context()
    with pytest.raises(NodeExceptionRecord) as caught:
        await await_result(sequential_exec(ctx, [child(index=i) for i in range(3)]))
    assert calls == [0, 1]
    assert caught.value.__cause__ is failure
    ctx.dispose()


async def test_sync_wrapper_forwards_completion_and_async_wrapper_waits() -> None:
    events: list[str] = []

    @node
    def value(ctx: Context, /) -> int:
        events.append("node")
        return 5

    @wrapper
    def forward(ctx: Context, wrapped: Node, call_next: Callable, /):
        events.append("forward")
        return call_next(ctx)

    @wrapper
    async def finished(ctx: Context, wrapped: Node, call_next: Callable, /):
        events.append("before")
        result = await await_result(call_next(ctx))
        events.append("after")
        return result + 1

    ctx = Context()
    graph = value().add_wrappers(forward(), finished())
    pending = graph(ctx)
    assert events == ["forward"]
    assert await await_result(pending) == 6
    assert events == ["forward", "before", "node", "after"]
    ctx.dispose()


@pytest.mark.parametrize("failure_in_wrapper", [False, True])
async def test_awaited_failures_keep_node_and_wrapper_attribution(
    failure_in_wrapper: bool,
) -> None:
    failure = ValueError("awaited failure")

    @node
    async def value(ctx: Context, /) -> int:
        await asyncio.sleep(0)
        if not failure_in_wrapper:
            raise failure
        return 3

    @wrapper
    async def wrap(ctx: Context, wrapped: Node, call_next: Callable, /):
        result = await await_result(call_next(ctx))
        if failure_in_wrapper:
            raise failure
        return result

    ctx = Context()
    wrapping = wrap()
    graph = value()
    if failure_in_wrapper:
        graph.add_wrappers(wrapping)
    error_type = WrapperExceptionRecord if failure_in_wrapper else NodeExceptionRecord
    with pytest.raises(error_type) as caught:
        await await_result(graph(ctx))
    assert caught.value.__cause__ is failure
    assert caught.value.exception_node is (wrapping if failure_in_wrapper else graph)
    ctx.dispose()


async def test_evaluation_runs_sync_batches_before_awaiting_async_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    async def asynchronous(ctx, values):
        calls.append("async")
        await asyncio.sleep(0)
        return [value + 1 for value in values]

    def synchronous(ctx, values):
        calls.append("sync")
        return [value.upper() for value in values]

    ctx = Context()
    ctx.effect(
        lambda: ctx.get(EVALUATORS_REF).register(
            ctx.scope, {int: asynchronous, str: synchronous}
        )
    )
    marker = object()
    assert await await_result(eval_tree(ctx, [1, marker, "x", 2])) == [
        2,
        marker,
        "X",
        3,
    ]
    assert calls == ["sync", "async"]
    ctx.dispose()


@pytest.mark.parametrize("await_setup", [False, True])
async def test_context_owns_async_setup_before_caller_waits(await_setup: bool) -> None:
    events: list[str] = []
    ctx = Context()

    async def setup():
        events.append("setup")
        await asyncio.sleep(0)

        def cleanup():
            events.append("cleanup")

        return cleanup

    registration = ctx.effect(setup)
    assert events == []
    if await_setup:
        early = await await_result(registration)
        await await_result(early())
        await await_result(early())
    await await_result(ctx.dispose())
    assert events == ["setup", "cleanup"]
    assert not ctx._lifecycle._effects
    early = await await_result(registration)
    await await_result(early())


async def test_owner_disposal_joins_inflight_setup_after_waiter_cancelled() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    events: list[str] = []
    ctx = Context()

    async def setup():
        started.set()
        await finish.wait()
        events.append("setup")

        async def cleanup():
            await asyncio.sleep(0)
            events.append("cleanup")

        return cleanup

    registration = ctx.effect(setup)
    waiter = asyncio.create_task(await_result(registration))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    disposal = asyncio.create_task(await_result(ctx.dispose()))
    await asyncio.sleep(0)
    assert not disposal.done()
    finish.set()
    await asyncio.wait_for(disposal, 1)
    assert events == ["setup", "cleanup"]
    assert not ctx._lifecycle._effects


@pytest.mark.parametrize("phase", ["setup", "cleanup"])
@pytest.mark.parametrize("ancestor", [False, True])
@pytest.mark.parametrize("dispose_mode", ["sequential", "batch"])
async def test_independent_disposer_created_inside_effect_can_wait_for_it(
    phase, ancestor, dispose_mode
) -> None:
    application = Context()
    root = application.fork(dispose_mode=dispose_mode)
    ctx = root.fork(dispose_mode=dispose_mode)
    target = root if ancestor else ctx
    started, finish, requested, disposing = (asyncio.Event() for _ in range(4))
    events = []
    disposals = []

    async def dispose_when_requested():
        await requested.wait()
        completion = target.dispose()
        disposing.set()
        await await_result(completion)

    async def pause():
        disposals.append(asyncio.create_task(dispose_when_requested()))
        started.set()
        await finish.wait()

    async def cleanup():
        if phase == "cleanup":
            await pause()
        events.append("cleanup")

    async def setup():
        await pause()
        events.append("setup")
        return cleanup

    if phase == "setup":
        completion = ctx.effect(setup)
    else:
        completion = ctx.effect(lambda: cleanup)()
    waiter = asyncio.create_task(await_result(completion))
    try:
        await asyncio.wait_for(started.wait(), 1)
        requested.set()
        await asyncio.wait_for(disposing.wait(), 1)
        assert not disposals[0].done()
        assert not waiter.done()
        ctx._lifecycle.assert_readable()
    finally:
        requested.set()
        finish.set()
        await asyncio.wait_for(asyncio.gather(waiter, *disposals), 1)
        await await_result(application.dispose())

    assert events == (["setup", "cleanup"] if phase == "setup" else ["cleanup"])
    assert not ctx._lifecycle._effects
    assert not root._children


async def test_multiple_setup_waiters_receive_the_same_disposer() -> None:
    ctx = Context()
    events = []

    async def cleanup():
        await asyncio.sleep(0)
        events.append("cleanup")

    async def setup():
        events.append("setup")
        await asyncio.sleep(0)
        return cleanup

    registration = ctx.effect(setup)
    left, right = await asyncio.gather(
        await_result(registration), await_result(registration)
    )
    assert left is right
    await asyncio.gather(await_result(left()), await_result(right()))
    await await_result(ctx.dispose())
    assert events == ["setup", "cleanup"]


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_continuation_composes_node_setup_and_disposal(asynchronous) -> None:
    events = []

    async def finish(value):
        await asyncio.sleep(0)
        return value

    def cleanup():
        events.append("cleanup")
        return finish(None) if asynchronous else None

    def setup():
        events.append("setup")
        return finish(cleanup) if asynchronous else cleanup

    @node
    def value(ctx: Context, /):
        events.append("node")
        return finish(5) if asynchronous else 5

    ctx = Context()

    @continuation
    def execute():
        try:
            dispose = yield ctx.effect(setup)
            result = yield value()(ctx)
            yield dispose()
            return result
        finally:
            yield ctx.dispose()

    result = execute()
    assert inspect.isawaitable(result) is asynchronous
    assert await await_result(result) == 5
    assert events == ["setup", "node", "cleanup"]
    assert not ctx._lifecycle._effects


def test_setup_can_request_disposal_after_registration_returns() -> None:
    ctx = Context()
    requested = False
    events = []

    def setup():
        nonlocal requested
        requested = True
        events.append("setup")
        return lambda: events.append("cleanup")

    ctx.effect(setup)
    assert events == ["setup"]
    if requested:
        ctx.dispose()
    assert events == ["setup", "cleanup"]


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_setup_failure_detaches_registration(asynchronous: bool) -> None:
    ctx = Context()
    initial_effects = tuple(ctx._lifecycle._effects)
    failure = ValueError("setup")

    def fail():
        raise failure

    async def async_fail():
        await asyncio.sleep(0)
        raise failure

    with pytest.raises(ValueError) as caught:
        await await_result(ctx.effect(async_fail if asynchronous else fail))
    assert caught.value is failure
    assert tuple(ctx._lifecycle._effects) == initial_effects
    assert ctx.dispose() is None


async def test_setup_failure_during_owner_disposal_keeps_cleaning_and_replays() -> None:
    ctx = Context()
    events: list[str] = []
    failure = ValueError("setup")

    async def setup():
        raise failure

    ctx.effect(lambda: lambda: events.append("first"))
    registration = ctx.effect(setup)
    ctx.effect(lambda: lambda: events.append("last"))
    for _ in range(2):
        with pytest.raises(BaseExceptionGroup) as caught:
            await await_result(ctx.dispose())
        assert caught.value.exceptions == (failure,)
    with pytest.raises(ValueError) as caught:
        await await_result(registration)
    assert caught.value is failure
    assert events == ["last", "first"]
    assert not ctx._lifecycle._effects


async def test_async_setup_cleanup_failure_replays_without_repeating() -> None:
    ctx = Context()
    calls: list[str] = []
    failure = ValueError("cleanup")

    async def setup():
        async def cleanup():
            calls.append("cleanup")
            raise failure

        return cleanup

    release = await await_result(ctx.effect(setup))
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            await await_result(release())
        assert caught.value is failure
    assert calls == ["cleanup"]
    assert ctx.dispose() is None


async def test_sync_wrapper_waits_for_its_async_auto_parameter() -> None:
    events: list[str] = []

    @node
    async def parameter(ctx: Context, /) -> int:
        async def cleanup() -> None:
            await asyncio.sleep(0)
            events.append("cleanup")

        ctx.effect(lambda: cleanup)
        return 2

    @node
    def value(ctx: Context, /) -> int:
        return 3

    @wrapper
    def add(ctx: Context, wrapped: Node, call_next: Callable, /, *, extra: int):
        assert events == ["cleanup"]
        return call_next(ctx) + extra

    ctx = Context()
    initial_effects = tuple(ctx._lifecycle._effects)
    assert (
        await await_result(value().add_wrappers(add(extra=Auto(parameter())))(ctx)) == 5
    )
    assert tuple(ctx._lifecycle._effects) == initial_effects
    ctx.dispose()


async def test_sequential_continues_sync_and_async_steps_after_first_await() -> None:
    events: list[int] = []

    @node
    async def asynchronous(ctx: Context, /, *, value: int) -> None:
        await asyncio.sleep(0)
        events.append(value)

    @node
    def synchronous(ctx: Context, /, *, value: int) -> None:
        events.append(value)

    ctx = Context()
    await await_result(
        sequential_exec(
            ctx, [asynchronous(value=1), synchronous(value=2), asynchronous(value=3)]
        )
    )
    assert events == [1, 2, 3]
    ctx.dispose()


async def test_sync_auto_failure_waits_for_async_cleanup_and_chains_errors() -> None:
    failure = ValueError("node")
    cleanup_failure = RuntimeError("cleanup")

    @node
    def child(ctx: Context, /) -> int:
        async def cleanup():
            await asyncio.sleep(0)
            raise cleanup_failure

        ctx.effect(lambda: cleanup)
        raise failure

    @node
    def parent(ctx: Context, /, *, value: int) -> int:
        return value

    ctx = Context()
    initial_effects = tuple(ctx._lifecycle._effects)
    with pytest.raises(NodeExceptionRecord) as caught:
        await await_result(parent(value=Auto(child()))(ctx))
    group = caught.value.__cause__
    assert isinstance(group, BaseExceptionGroup)
    child_errors = group.exceptions[0]
    assert isinstance(child_errors, BaseExceptionGroup)
    cleanup_error = child_errors.exceptions[0]
    assert isinstance(cleanup_error, BaseExceptionGroup)
    assert cleanup_error.exceptions == (cleanup_failure,)
    node_error = cleanup_error.__context__
    assert isinstance(node_error, NodeExceptionRecord)
    assert node_error.__cause__ is failure
    assert child_errors.__cause__ is None
    assert tuple(ctx._lifecycle._effects) == initial_effects
    ctx.dispose()


async def test_disposal_preserves_sync_failure_while_finishing_async_cleanup() -> None:
    ctx = Context()
    events: list[str] = []
    first_error = ValueError("first failure")

    def fail() -> None:
        events.append("sync")
        raise first_error

    async def async_fail() -> None:
        events.append("async")
        raise RuntimeError("second failure")

    ctx.effect(lambda: lambda: events.append("last"))
    ctx.effect(lambda: async_fail)
    ctx.effect(lambda: fail)
    pending = ctx.dispose()
    assert events == ["sync"]
    with pytest.raises(BaseExceptionGroup) as caught:
        await await_result(pending)
    assert len(caught.value.exceptions) == 2
    assert caught.value.exceptions[0] is first_error
    assert isinstance(caught.value.exceptions[1], RuntimeError)
    assert str(caught.value.exceptions[1]) == "second failure"
    assert events == ["sync", "async", "last"]
