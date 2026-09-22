import asyncio
from dataclasses import FrozenInstanceError

import pytest

from slyme.context import Context, Schema, Scope
from slyme.context.default import DATA_TREE_REF
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result


def test_disposal_mode_is_fixed_and_not_inherited() -> None:
    root = Context(dispose_mode="parallel")
    direct = Context(parent=root)
    fork = root.fork()
    derived = root.derive()
    parallel = root.derive(dispose_mode="parallel")
    detached = root.fork(scope=Scope(), dispose_mode="parallel")
    assert (
        root.dispose_mode
        == parallel.dispose_mode
        == detached.dispose_mode
        == "parallel"
    )
    assert (
        direct.dispose_mode == fork.dispose_mode == derived.dispose_mode == "sequential"
    )
    assert direct.scope is fork.scope is root.scope
    assert derived.scope.parents == parallel.scope.parents == (root.scope,)
    assert detached.scope.parents == ()
    assert root._lifecycle.dispose_mode == "parallel"
    with pytest.raises(FrozenInstanceError):
        root.dispose_mode = "sequential"
    with pytest.raises(FrozenInstanceError):
        root._lifecycle.dispose_mode = "sequential"
    assert root.dispose() is None


@pytest.mark.parametrize("dispose_mode", ["sequential", "parallel"])
@pytest.mark.parametrize("fails", [False, True])
def test_sync_disposal_finishes_inline_in_reverse_registration_order(
    dispose_mode, fails
):
    ctx = Context(dispose_mode=dispose_mode)
    events = []
    failure = ValueError("middle")

    def middle():
        events.append("middle")
        if fails:
            raise failure

    ctx.effect(lambda: lambda: events.append("first"))
    ctx.effect(lambda: middle)
    child = ctx.fork()
    child.effect(lambda: lambda: events.append("child"))
    ctx.effect(lambda: lambda: events.append("last"))
    if fails:
        with pytest.raises(BaseExceptionGroup) as caught:
            ctx.dispose()
        assert caught.value.exceptions == (failure,)
        with pytest.raises(BaseExceptionGroup) as repeated:
            ctx.dispose()
        assert repeated.value is caught.value
    else:
        assert ctx.dispose() is None
        assert ctx.dispose() is None
    assert events == ["last", "child", "middle", "first"]
    assert not ctx.children
    assert not ctx._schema._stores


def test_parallel_disposal_can_begin_outside_an_event_loop() -> None:
    root = Context()
    group = root.fork(dispose_mode="parallel")
    events = []

    async def cleanup(index):
        events.append((index, "start"))
        await asyncio.sleep(0)
        events.append((index, "finish"))

    group.effect(lambda: lambda: cleanup(0))
    group.effect(lambda: lambda: cleanup(1))
    pending = root.dispose()
    assert not events
    assert root.children == (group,)
    asyncio.run(await_result(pending))
    assert set(events[:2]) == {(0, "start"), (1, "start")}
    assert set(events[2:]) == {(0, "finish"), (1, "finish")}
    assert not root.children


async def test_parallel_children_keep_local_lifo_and_parent_declarations() -> None:
    root = Context()
    events = []
    root.effect(lambda: lambda: events.append("root:release"))
    root.declare({"shared": Schema.leaf()})
    root.set("shared", "data")
    rules = root.get(DATA_TREE_REF)
    plugins = root.fork(dispose_mode="parallel")
    started = [asyncio.Event(), asyncio.Event()]
    finish = [asyncio.Event(), asyncio.Event()]
    children = []

    def install(index):
        child = plugins.fork()
        child.declare({f"plugin{index}": Schema.leaf()})
        child.set(f"plugin{index}", index)
        child.effect(lambda: lambda: events.append(f"{index}:resource"))

        async def cleanup():
            started[index].set()
            await finish[index].wait()
            assert child.get("shared") == "data"
            assert child.get(f"plugin{index}") == index
            assert child.get(DATA_TREE_REF) is rules
            events.append(f"{index}:worker")

        child.effect(lambda: cleanup)
        return child

    children.extend(install(index) for index in range(2))
    root.effect(lambda: lambda: events.append("root:last"))
    completion = root.dispose()
    assert events == ["root:last"]
    for child in children:
        with pytest.raises(RuntimeError, match="disposed"):
            child.set("shared", "forbidden")

    waiter = asyncio.create_task(await_result(completion))
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 5)
        finish[0].set()
        await children[0].adispose()
        assert plugins.children == (children[1],)
        assert root.children == (plugins,)
        assert not waiter.done()
        assert root.get("shared") == "data"
        assert events == ["root:last", "0:worker", "0:resource"]
    finally:
        for event in finish:
            event.set()
        await waiter
    assert events == [
        "root:last",
        "0:worker",
        "0:resource",
        "1:worker",
        "1:resource",
        "root:release",
    ]
    assert not root.children and not plugins.children
    assert not root._schema._stores


async def test_parallel_cleanup_collects_sync_async_and_cancellation_failures_in_order():
    root = Context()
    group = root.fork(dispose_mode="parallel")
    old_error, slow_error, new_error = (
        ValueError("old"),
        ValueError("slow"),
        ValueError("new"),
    )
    started, finish, cancelled = asyncio.Event(), asyncio.Event(), asyncio.Event()
    calls = []

    def fail(error):
        calls.append(error)
        raise error

    async def slow():
        started.set()
        await finish.wait()
        fail(slow_error)

    async def cancel():
        cancelled.set()
        raise asyncio.CancelledError()

    group.effect(lambda: lambda: fail(old_error))
    group.effect(lambda: slow)
    group.effect(lambda: cancel)
    group.effect(lambda: lambda: fail(new_error))
    waiter = asyncio.create_task(group.adispose())
    try:
        await asyncio.wait_for(asyncio.gather(started.wait(), cancelled.wait()), 5)
        assert root.children == (group,)
        assert calls == [new_error, old_error]
        assert not waiter.done()
    finally:
        finish.set()
        with pytest.raises(BaseExceptionGroup) as caught:
            await waiter
    errors = caught.value.exceptions
    assert errors[0] is new_error
    assert isinstance(errors[1], asyncio.CancelledError)
    assert errors[2:] == (slow_error, old_error)
    with pytest.raises(BaseExceptionGroup) as repeated:
        await group.adispose()
    assert repeated.value is caught.value
    assert calls == [new_error, old_error, slow_error]
    assert not root.children
    root.dispose()


async def test_parallel_disposal_waits_for_pending_setups_despite_failure() -> None:
    root = Context()
    group = root.fork(dispose_mode="parallel")
    started = [asyncio.Event(), asyncio.Event()]
    finish = asyncio.Event()
    failure = ValueError("setup failed")
    calls = []

    async def setup(index):
        calls.append(index)
        started[index].set()
        await finish.wait()
        if index == 0:
            raise failure
        return lambda: calls.append("cleanup")

    group.effect(lambda: setup(0))
    successful_setup = group.effect(lambda: setup(1))
    waiter = asyncio.create_task(group.adispose())
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 5)
        assert not waiter.done()
        assert root.children == (group,)
    finally:
        finish.set()
        with pytest.raises(BaseExceptionGroup) as caught:
            await waiter
    assert caught.value.exceptions == (failure,)
    assert sorted(calls[:2]) == [0, 1]
    assert calls[2:] == ["cleanup"]
    release = await successful_setup
    assert await successful_setup is release
    assert await await_result(release()) is None
    assert not root.children
    root.dispose()


async def test_branches_share_one_cleanup_without_delaying_independent_successors():
    root = Context()
    group = root.fork(dispose_mode="parallel")
    a_started, b_started = asyncio.Event(), asyncio.Event()
    finish_a, finish_b = asyncio.Event(), asyncio.Event()
    c_done, d_done = asyncio.Event(), asyncio.Event()
    calls = []

    async def a():
        calls.append("a")
        a_started.set()
        await finish_a.wait()

    async def b():
        calls.append("b")
        b_started.set()
        await finish_b.wait()

    dispose_b = group.effect(lambda: b)
    left = group.fork()
    left.effect(lambda: d_done.set)
    left.effect(lambda: dispose_b)
    right = group.fork()
    right.effect(lambda: c_done.set)
    predecessors = right.fork(dispose_mode="parallel")
    predecessors.effect(lambda: a)
    predecessors.effect(lambda: dispose_b)

    waiter = asyncio.create_task(group.adispose())
    try:
        await asyncio.wait_for(asyncio.gather(a_started.wait(), b_started.wait()), 5)
        finish_b.set()
        await asyncio.wait_for(d_done.wait(), 5)
        assert not c_done.is_set()
        assert not waiter.done()
        assert sorted(calls) == ["a", "b"]
    finally:
        finish_a.set()
        finish_b.set()
        await waiter
    assert c_done.is_set()
    assert sorted(calls) == ["a", "b"]
    assert not root.children
    root.dispose()
