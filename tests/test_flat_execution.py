from __future__ import annotations

import asyncio
import sys
from inspect import isawaitable

import pytest

from slyme.context import Context, Schema
from slyme.node import Auto, create_node, sequential
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result, continuation


def python_depth():
    frame = sys._getframe()
    depth = 0
    while frame is not None:
        depth += 1
        frame = frame.f_back
    return depth


@pytest.mark.parametrize("mode", ["sequential", "batch", "mixed"])
@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("fails", [False, True])
async def test_deep_owned_contexts_dispose_once_with_local_order(
    mode, asynchronous, fails
):
    root = Context()
    current = root
    cleaned, depths = [], []
    failure = ValueError("leaf cleanup")

    def cleanup(index):
        cleaned.append(index)
        depths.append(python_depth())

    for index in range(1500):
        current = current.fork(
            dispose_mode=("batch" if index % 2 else "sequential")
            if mode == "mixed"
            else mode
        )
        current.effect(lambda index=index: lambda: cleanup(index))

    @continuation
    def leaf():
        if asynchronous:
            yield asyncio.sleep(0)
        if fails:
            raise failure

    current.effect(lambda: leaf)
    if fails:
        with pytest.raises(BaseExceptionGroup) as caught:
            result = root.dispose()
            assert isawaitable(result) == asynchronous
            await await_result(result)
        pending, leaves = [caught.value], []
        while pending:
            error = pending.pop()
            if isinstance(error, BaseExceptionGroup):
                pending.extend(error.exceptions)
            else:
                leaves.append(error)
        assert leaves == [failure]
        with pytest.raises(BaseExceptionGroup) as replayed:
            await await_result(root.dispose())
        assert replayed.value is caught.value
    else:
        result = root.dispose()
        assert isawaitable(result) == asynchronous
        assert await await_result(result) is None
        assert await await_result(root.dispose()) is None
    assert sorted(cleaned) == list(range(1500))
    if not asynchronous or mode != "mixed":
        assert cleaned == list(reversed(range(1500)))
    else:
        positions = {value: index for index, value in enumerate(cleaned)}
        assert all(
            positions[index + 1] < positions[index] for index in range(0, 1499, 2)
        )
    assert max(depths) - min(depths) < 20
    assert not root.children
    assert not root._lifecycle._effects
    assert not root._schema._stores


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_deep_auto_nodes_use_a_bounded_python_stack(asynchronous):
    depths = []

    @continuation
    def leaf(ctx):
        if asynchronous:
            yield asyncio.sleep(0)
        return 0

    def increment(ctx, value):
        depths.append(python_depth())
        return value + 1

    graph = create_node(leaf)
    for _ in range(1100):
        graph = create_node(increment, {"value": Auto(graph)})
    root = Context()
    effects = tuple(root._lifecycle._effects)
    try:
        result = graph(root)
        assert isawaitable(result) == asynchronous
        assert await await_result(result) == 1100
        assert max(depths) - min(depths) < 20
        assert not root.children
        assert tuple(root._lifecycle._effects) == effects
    finally:
        await await_result(root.dispose())


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_deep_sequential_nodes_flatten_the_existing_execution_body(asynchronous):
    calls = []

    @continuation
    def leaf(ctx):
        if asynchronous:
            yield asyncio.sleep(0)
        calls.append(1)

    graph = create_node(leaf)
    for _ in range(2500):
        graph = sequential(nodes=[graph])
    ctx = Context()
    try:
        assert await await_result(graph(ctx)) is None
        assert calls == [1]
    finally:
        await await_result(ctx.dispose())


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_installed_continuation_preserves_binding_and_flat_entry(asynchronous):
    root = Context()
    root.declare({"value": Schema.leaf()})
    root.set("value", 7)
    child = root.derive()
    child.set("value", 11)

    @continuation
    def recurse(ctx, depth):
        if depth:
            return 1 + (yield ctx.recurse.flat_call(depth - 1))
        if asynchronous:
            yield asyncio.sleep(0)
        return ctx.get("value")

    remove = root.install("recurse", recurse)
    try:
        assert await await_result(child.recurse(2000)) == 2011
        assert await await_result(root.recurse(0)) == 7
    finally:
        await await_result(remove())
        await await_result(root.dispose())
