from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from slyme.context import Context, Schema
from slyme.node import Auto, Node, Wrapper, create_node, create_wrapper, node, wrapper
from slyme.node.exception import (
    NodeException,
    NodeExceptionRecord,
    WrapperExceptionRecord,
)
from slyme.utils.exception import exception_group
from slyme.utils.execution import await_result, continuation


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("origin", ["node", "wrapper"])
@pytest.mark.parametrize(
    "failure_factory",
    [
        asyncio.CancelledError,
        KeyboardInterrupt,
        SystemExit,
        NodeException,
        pytest.param(
            lambda: exception_group("cancelled", [asyncio.CancelledError()]),
            id="control_exception_group",
        ),
    ],
)
async def test_node_and_wrapper_preserve_control_and_framework_exceptions(
    asynchronous: bool,
    origin: str,
    failure_factory: Callable[[], BaseException],
) -> None:
    failure = failure_factory()

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
        with pytest.raises(type(failure)) as caught:
            await await_result(graph(ctx))
        assert caught.value is failure
    finally:
        await await_result(ctx.dispose())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("origin", ["node", "wrapper"])
async def test_node_and_wrapper_record_exception_groups_as_cause(
    asynchronous: bool, origin: str
) -> None:
    failure = exception_group("failed", [ValueError("first"), LookupError("second")])

    @continuation
    def fail():
        if asynchronous:
            yield asyncio.sleep(0)
        raise failure

    graph = create_node(lambda ctx: fail() if origin == "node" else None)
    around = create_wrapper(
        lambda ctx, wrapped, call_next: (
            fail() if origin == "wrapper" else call_next(ctx)
        )
    )
    if origin == "wrapper":
        graph.add_wrappers(around)
    expected = NodeExceptionRecord if origin == "node" else WrapperExceptionRecord
    ctx = Context()
    try:
        with pytest.raises(expected) as caught:
            await await_result(graph(ctx))
        assert caught.value.__cause__ is failure
        assert caught.value.exception_node is (graph if origin == "node" else around)
    finally:
        await await_result(ctx.dispose())


def test_compose_snapshots_order_but_reads_live_wrapper_parameters() -> None:
    events = []

    @node
    def target(ctx: Context, /) -> int:
        events.append("target")
        return 3

    graph = target()

    @wrapper
    @continuation
    def around(ctx: Context, wrapped: Node, call_next: Callable, /, *, label: str):
        assert wrapped is graph
        events.append((label, "before"))
        value = yield call_next(ctx)
        events.append((label, "after"))
        return value

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
    ctx = Context()
    ctx.declare(Schema({"value": Schema.leaf()}))
    ctx.update({"value": 1})
    child = ctx.fork(scope=ctx.scope.fork())
    child.set("value", 2)

    @node
    def value(ctx: Context, /) -> int:
        return ctx.get("value")

    @node
    async def avalue(ctx: Context, /) -> int:
        return ctx.get("value")

    @wrapper
    @continuation
    def twice(ctx: Context, wrapped: Node, call_next: Callable, /):
        first = yield call_next(ctx)
        second = yield call_next(child)
        return first, second

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


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("short_circuit", [False, True])
async def test_node_auto_uses_each_delegated_context_and_skips_short_circuits(
    asynchronous: bool,
    short_circuit: bool,
) -> None:
    ctx = Context()
    ctx.declare({"value": Schema.leaf()})
    ctx.set("value", 1)
    child = ctx.derive()
    child.set("value", 2)
    events = []

    @node
    @continuation
    def parameter(ctx: Context, /):
        if asynchronous:
            yield asyncio.sleep(0)
        value = ctx.get("value")
        events.append(("auto", value))
        return value

    @node
    def target(ctx: Context, /, *, value):
        events.append(("target", value))
        return value

    @wrapper
    @continuation
    def around(ctx: Context, wrapped: Node, call_next: Callable, /):
        if short_circuit:
            return "stopped"
        first = yield call_next(child)
        second = yield call_next(ctx)
        return first, second

    graph = target(value=Auto(parameter())).add_wrappers(around())
    try:
        result = await await_result(graph(ctx))
        if short_circuit:
            assert result == "stopped"
            assert events == []
        else:
            assert result == (2, 1)
            assert events == [("auto", 2), ("target", 2), ("auto", 1), ("target", 1)]
    finally:
        await await_result(ctx.dispose())
