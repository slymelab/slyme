from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from slyme.context import Context, Schema
from slyme.node import Node, Wrapper, node, wrapper
from slyme.utils.continuation import await_result, run


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("origin", ["node", "wrapper"])
@pytest.mark.parametrize(
    "error_type", [asyncio.CancelledError, KeyboardInterrupt, SystemExit]
)
async def test_node_and_wrapper_preserve_control_exceptions(
    asynchronous: bool,
    origin: str,
    error_type: type[BaseException],
) -> None:
    failure = error_type()

    def run():
        if origin == "node":
            raise failure
        return 1

    @node
    def target(ctx: Context, /) -> int:
        return run()

    @node
    async def atarget(ctx: Context, /) -> int:
        await asyncio.sleep(0)
        return run()

    @wrapper
    def around(ctx: Context, wrapped: Node, call_next: Callable, /):
        if origin == "wrapper":
            raise failure
        return call_next(ctx)

    @wrapper
    async def aaround(ctx: Context, wrapped: Node, call_next: Callable, /):
        await asyncio.sleep(0)
        return await await_result(around()(ctx, wrapped, call_next))

    graph = atarget() if asynchronous else target()
    graph.add_wrappers(aaround() if asynchronous else around())
    ctx = Context()
    try:
        with pytest.raises(error_type) as caught:
            await graph.acall(ctx)
        assert caught.value is failure
    finally:
        await ctx.adispose()


def test_compose_snapshots_order_but_reads_live_wrapper_parameters() -> None:
    events = []

    @node
    def target(ctx: Context, /) -> int:
        events.append("target")
        return 3

    graph = target()

    @wrapper
    def around(ctx: Context, wrapped: Node, call_next: Callable, /, *, label: str):
        assert wrapped is graph
        events.append((label, "before"))

        def execute():
            value = yield call_next(ctx)
            events.append((label, "after"))
            return value

        return run(execute())

    outer, inner = around(label="outer"), around(label="inner")
    wrappers = [outer, inner]
    chain = Wrapper.compose(iter(wrappers), wrapped=graph, call_next=graph)
    assert events == []
    wrappers.clear()
    inner.set("label", "updated")
    ctx = Context()
    for _ in range(2):
        assert chain(ctx) == 3
    assert (
        events
        == [
            ("outer", "before"),
            ("updated", "before"),
            "target",
            ("updated", "after"),
            ("outer", "after"),
        ]
        * 2
    )
    assert Wrapper.compose([], wrapped=graph, call_next=graph) is graph
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_compose_preserves_context_replacement_and_multiple_next_calls(
    asynchronous: bool,
) -> None:
    ctx = Context({"value": 1}, schema=Schema({"value": Schema.leaf()}))
    child = ctx.fork(scope=ctx.scope.fork())
    child.set("value", 2)

    @node
    def value(ctx: Context, /) -> int:
        return ctx.get("value")

    @node
    async def avalue(ctx: Context, /) -> int:
        return ctx.get("value")

    @wrapper
    def twice(ctx: Context, wrapped: Node, call_next: Callable, /):
        def execute():
            first = yield call_next(ctx)
            second = yield call_next(child)
            return first, second

        return run(execute())

    graph = avalue() if asynchronous else value()
    chain = Wrapper.compose([twice()], wrapped=graph, call_next=graph)
    assert await await_result(chain(ctx)) == (1, 2)
    ctx.dispose()


def test_compose_wrapper_can_short_circuit_without_calling_the_target() -> None:
    @node
    def target(ctx: Context, /) -> None:
        raise AssertionError("target must not run")

    @wrapper
    def stop(ctx: Context, wrapped: Node, call_next: Callable, /) -> str:
        return "stopped"

    graph = target()
    chain = Wrapper.compose([stop()], wrapped=graph, call_next=graph)
    ctx = Context()
    assert chain(ctx) == "stopped"
    ctx.dispose()
