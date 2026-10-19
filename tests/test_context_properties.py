from __future__ import annotations

import asyncio

import pytest

from slyme.context import Context, Schema
from slyme.execution import await_result


@pytest.mark.parametrize("fail", [False, True])
def test_state_remains_readable_after_sync_disposal(fail) -> None:
    ctx = Context()
    assert ctx.state == ctx._lifecycle.state == "active"

    def cleanup():
        assert ctx.state == ctx._lifecycle.state == "disposing"
        if fail:
            raise ValueError("cleanup failed")

    ctx.effect(lambda: cleanup)
    if fail:
        with pytest.raises(Exception, match="Lifecycle dispose failed"):
            ctx.dispose()
    else:
        ctx.dispose()
    assert ctx.state == ctx._lifecycle.state == "disposed"


async def test_state_distinguishes_pending_children_from_disposing_owner() -> None:
    ctx = Context()
    child = ctx.fork()
    started = asyncio.Event()
    proceed = asyncio.Event()

    async def cleanup():
        started.set()
        await proceed.wait()

    ctx.effect(lambda: cleanup)
    pending = asyncio.create_task(await_result(ctx.dispose()))
    try:
        await started.wait()
        assert ctx.state == ctx._lifecycle.state == "disposing"
        assert child.state == child._lifecycle.state == "dispose_pending"
    finally:
        proceed.set()
        await pending
    assert ctx.state == child.state == "disposed"


def test_view_exposes_its_owner_and_absolute_path_after_disposal() -> None:
    ctx = Context()
    ctx.declare({"group": {"nested": {"value": Schema.leaf()}}})
    ctx.set("group.nested.value", 1)
    child = ctx.fork()
    root_view = child.get("")
    nested_view = root_view.get("group").get("nested")
    assert root_view.context is nested_view.context is child
    assert root_view.path == ""
    assert nested_view.path == "group.nested"
    ctx.dispose()
    assert root_view.context is nested_view.context is child
    assert root_view.path == ""
    assert nested_view.path == "group.nested"
    with pytest.raises(RuntimeError, match="disposed"):
        nested_view.get("value")
