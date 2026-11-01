from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from slyme.context import Context, Schema
from slyme.execution import await_result, continuation
from slyme.node import Node, auto, node, wrap
from slyme.utils.exception import exception_group


def test_flat_and_nested_wrap_have_the_same_outer_to_inner_order() -> None:
    @node(inject_ctx=True)
    def layer(ctx: Context, wrapped: Node, label: str):
        return f"{label}({wrapped(ctx)})"

    target = node(lambda: "target")
    outer = layer.partial(label="outer")
    inner = layer.partial(label="inner")
    flat = wrap(outer, inner, target)
    nested = wrap(outer, wrap(inner, target))
    ctx = Context()
    try:
        assert flat(ctx) == nested(ctx) == "outer(inner(target))"
        assert flat.func is nested.func is outer.func
        assert wrap(target) is target
        assert "wrapped" not in outer.params and "wrapped" not in inner.params
    finally:
        ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("origin", ["node", "layer"])
@pytest.mark.parametrize(
    "failure_factory",
    [
        asyncio.CancelledError,
        KeyboardInterrupt,
        SystemExit,
        ValueError,
        pytest.param(
            lambda: exception_group("cancelled", [asyncio.CancelledError()]),
            id="control_exception_group",
        ),
    ],
)
async def test_node_and_layer_preserve_original_exceptions(
    asynchronous: bool,
    origin: str,
    failure_factory: Callable[[], BaseException],
) -> None:
    failure = failure_factory()

    def run():
        if origin == "node":
            raise failure
        return 1

    @node(inject_ctx=True)
    def target(ctx: Context, /) -> int:
        return run()

    @node(inject_ctx=True)
    async def atarget(ctx: Context, /) -> int:
        await asyncio.sleep(0)
        return run()

    @node(inject_ctx=True)
    def around(ctx: Context, /, *, wrapped: Node):
        if origin == "layer":
            raise failure
        return wrapped(ctx)

    @node(inject_ctx=True)
    async def aaround(ctx: Context, /, *, wrapped: Node):
        await asyncio.sleep(0)
        return await await_result(around(ctx, wrapped=wrapped))

    graph = atarget.partial() if asynchronous else target.partial()
    graph = wrap(aaround.partial() if asynchronous else around.partial(), graph)
    ctx = Context()
    try:
        with pytest.raises(type(failure)) as caught:
            await await_result(graph(ctx))
        assert caught.value is failure
    finally:
        await await_result(ctx.dispose())


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("origin", ["node", "layer"])
async def test_node_and_layer_preserve_exception_groups(
    asynchronous: bool, origin: str
) -> None:
    failure = exception_group("failed", [ValueError("first"), LookupError("second")])

    @continuation
    def fail():
        if asynchronous:
            yield asyncio.sleep(0)
        raise failure

    graph = node(lambda ctx: fail() if origin == "node" else None, inject_ctx=True)
    around = node(
        lambda ctx, *, wrapped: fail() if origin == "layer" else wrapped(ctx),
        inject_ctx=True,
    )
    if origin == "layer":
        graph = wrap(around, graph)
    expected = type(failure)
    ctx = Context()
    try:
        with pytest.raises(expected) as caught:
            await await_result(graph(ctx))
        assert caught.value is failure
    finally:
        await await_result(ctx.dispose())


def test_compose_retains_original_layers_when_partial_creates_a_replacement() -> None:
    events = []

    @node(inject_ctx=True)
    def target(ctx: Context, /) -> int:
        events.append("target")
        return 3

    graph = target.partial()

    @node(inject_ctx=True)
    @continuation
    def around(ctx: Context, /, *, wrapped: Node, label: str):
        assert wrapped is graph if label == "inner" else wrapped.get("wrapped") is graph
        events.append((label, "before"))
        value = yield wrapped(ctx)
        events.append((label, "after"))
        return value

    outer, inner = around.partial(label="outer"), around.partial(label="inner")
    layers = [outer, inner]
    chain = wrap(*iter(layers), graph)
    assert events == []
    layers.clear()
    replacement = inner.partial(label="updated")
    assert replacement.get("label") == "updated"
    ctx = Context()
    for _ in range(2):
        assert chain(ctx) == 3
    assert (
        events
        == [
            ("outer", "before"),
            ("inner", "before"),
            "target",
            ("inner", "after"),
            ("outer", "after"),
        ]
        * 2
    )
    assert wrap(*[], graph) is graph
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_compose_preserves_context_replacement_and_multiple_next_calls(
    asynchronous: bool,
) -> None:
    ctx = Context()
    ctx.declare(Schema({"value": Schema.leaf()}))
    ctx.set_many({"value": 1})
    child = ctx.fork(scope=ctx.scope.fork())
    child.set("value", 2)

    @node(inject_ctx=True)
    def value(ctx: Context, /) -> int:
        return ctx.get("value")

    @node(inject_ctx=True)
    async def avalue(ctx: Context, /) -> int:
        return ctx.get("value")

    @node(inject_ctx=True)
    @continuation
    def twice(ctx: Context, /, *, wrapped: Node):
        first = yield wrapped(ctx)
        second = yield wrapped(child)
        return first + second

    graph = avalue.partial() if asynchronous else value.partial()
    chain = wrap(*[twice.partial()], graph)
    assert await await_result(chain(ctx)) == 3
    ctx.dispose()


def test_compose_layer_can_short_circuit_without_calling_the_target() -> None:
    @node(inject_ctx=True)
    def target(ctx: Context, /) -> str:
        raise AssertionError("target must not run")

    @node(inject_ctx=True)
    def stop(ctx: Context, /, *, wrapped: Node) -> str:
        return "stopped"

    graph = target.partial()
    chain = wrap(*[stop.partial()], graph)
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

    @node(inject_ctx=True)
    @continuation
    def parameter(ctx: Context, /):
        if asynchronous:
            yield asyncio.sleep(0)
        value = ctx.get("value")
        events.append(("auto", value))
        return value

    @node(inject_ctx=True)
    def target(ctx: Context, /, *, value: int) -> int:
        events.append(("target", value))
        return value

    @node(inject_ctx=True)
    @continuation
    def around(ctx: Context, /, *, wrapped: Node):
        if short_circuit:
            return -1
        first = yield wrapped(child)
        second = yield wrapped(ctx)
        return first + second

    graph = wrap(around.partial(), target.partial(value=auto(parameter.partial())))
    try:
        result = await await_result(graph(ctx))
        if short_circuit:
            assert result == -1
            assert events == []
        else:
            assert result == 3
            assert events == [("auto", 2), ("target", 2), ("auto", 1), ("target", 1)]
    finally:
        await await_result(ctx.dispose())
