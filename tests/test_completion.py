from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable

import pytest

from slyme.context import Context
from slyme.node import Auto, Node, node, sequential_exec, wrapper
from slyme.node.eval import (
    EvaluationPlan,
    EvaluatorDef,
    execute_eval_plan,
    prepare_eval_plan,
)
from slyme.node.exception import NodeExceptionRecord, WrapperExceptionRecord
from slyme.utils.awaitable import resolve


async def test_resolve_preserves_values_and_only_awaits_outer_completion() -> None:
    async def value() -> int:
        return 3

    data = [value()]
    assert await resolve(data) is data
    assert await resolve(data[0]) == 3
    assert await resolve(None) is None
    assert await resolve(2) == 2


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
    def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        events.append("parent")
        return value + 1

    ctx = Context()
    graph = parent(value=child())
    pending = graph(ctx)
    assert inspect.isawaitable(pending)
    assert events == ["call"]
    assert await resolve(pending) == 5
    assert events == ["call", "await", "parent"]
    assert not ctx._owned
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
    def parent(ctx: Context, /, *, values: Auto[list[int]]) -> int:
        return sum(values)

    ctx = Context()
    result = parent(values=[child(index=1), child(index=2)])(ctx)
    assert await asyncio.wait_for(resolve(result), 1) == 3
    assert not ctx._owned
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
        result = await resolve(call_next(ctx))
        events.append("after")
        return result + 1

    ctx = Context()
    graph = value().add_wrappers(forward(), finished())
    pending = graph(ctx)
    assert events == ["forward"]
    assert await resolve(pending) == 6
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
        result = await resolve(call_next(ctx))
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
        await resolve(graph(ctx))
    assert caught.value.exception is failure
    assert caught.value.exception_node is (wrapping if failure_in_wrapper else graph)
    ctx.dispose()


async def test_evaluation_continues_remaining_batches_after_await() -> None:
    calls: list[str] = []
    base = prepare_eval_plan([None, None, None])

    async def asynchronous(ctx, values):
        calls.append("async")
        await asyncio.sleep(0)
        return values

    def synchronous(ctx, values):
        calls.append("sync")
        return values

    plan = EvaluationPlan(
        base.tree_def,
        (
            (EvaluatorDef(asynchronous), (0,), (1,)),
            (EvaluatorDef(synchronous), (2,), (3,)),
        ),
        ((1, 2),),
        3,
    )
    ctx = Context()
    assert await resolve(execute_eval_plan(ctx, plan)) == [1, 2, 3]
    assert calls == ["async", "sync"]
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
        early = await resolve(registration)
        await resolve(early())
        await resolve(early())
    await resolve(ctx.dispose())
    assert events == ["setup", "cleanup"]
    assert not ctx._owned
    early = await resolve(registration)
    await resolve(early())


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
    waiter = asyncio.create_task(resolve(registration))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    disposal = asyncio.create_task(resolve(ctx.dispose()))
    await asyncio.sleep(0)
    assert not disposal.done()
    finish.set()
    await asyncio.wait_for(disposal, 1)
    assert events == ["setup", "cleanup"]
    assert not ctx._owned


@pytest.mark.parametrize("ancestor", [False, True])
async def test_async_setup_cannot_dispose_owner_or_ancestor(ancestor: bool) -> None:
    root = Context()
    ctx = root.fork()
    target = root if ancestor else ctx
    events: list[str] = []

    async def setup():
        await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="setup or cleanup"):
            await resolve(target.dispose())
        return lambda: events.append("cleanup")

    release = await resolve(ctx.effect(setup))
    await resolve(release())
    assert events == ["cleanup"]
    root.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_setup_failure_detaches_registration(asynchronous: bool) -> None:
    ctx = Context()
    failure = ValueError("setup")

    def fail():
        raise failure

    async def async_fail():
        await asyncio.sleep(0)
        raise failure

    with pytest.raises(ValueError) as caught:
        await resolve(ctx.effect(async_fail if asynchronous else fail))
    assert caught.value is failure
    assert not ctx._owned
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
        with pytest.raises(ValueError) as caught:
            await resolve(ctx.dispose())
        assert caught.value is failure
    with pytest.raises(ValueError) as caught:
        await resolve(registration)
    assert caught.value is failure
    assert events == ["last", "first"]
    assert not ctx._owned


async def test_async_setup_cleanup_failure_replays_without_repeating() -> None:
    ctx = Context()
    calls: list[str] = []
    failure = ValueError("cleanup")

    async def setup():
        async def cleanup():
            calls.append("cleanup")
            raise failure

        return cleanup

    release = await resolve(ctx.effect(setup))
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            await resolve(release())
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
    def add(ctx: Context, wrapped: Node, call_next: Callable, /, *, extra: Auto[int]):
        assert events == ["cleanup"]
        return call_next(ctx) + extra

    ctx = Context()
    assert await resolve(value().add_wrappers(add(extra=parameter()))(ctx)) == 5
    assert not ctx._owned
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
    await resolve(
        sequential_exec(
            ctx, [asynchronous(value=1), synchronous(value=2), asynchronous(value=3)]
        )
    )
    assert events == [1, 2, 3]
    ctx.dispose()


async def test_sync_auto_failure_waits_for_async_cleanup_and_keeps_both_errors() -> (
    None
):
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
    def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    ctx = Context()
    with pytest.raises(NodeExceptionRecord) as caught:
        await resolve(parent(value=child())(ctx))
    assert caught.value.exception is failure
    assert caught.value.__cause__ is cleanup_failure
    assert not ctx._owned
    ctx.dispose()


async def test_cleanup_cannot_await_saved_owner_completion() -> None:
    ctx = Context()
    completion = None

    async def cleanup() -> None:
        await resolve(completion)

    ctx.effect(lambda: cleanup)
    completion = ctx.dispose()
    with pytest.raises(RuntimeError, match="setup or cleanup"):
        await asyncio.wait_for(resolve(completion), 1)
    assert not ctx._owned


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
    with pytest.raises(ValueError) as caught:
        await resolve(pending)
    assert caught.value is first_error
    assert events == ["sync", "async", "last"]
