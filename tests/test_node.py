from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from slyme.context import Context, ContextPathError, Ref, Schema
from slyme.context.default import EVALUATORS_REF, NODE_TREE_REF
from slyme.node import (
    Auto,
    Node,
    Wrapper,
    eval_tree,
    node,
    sequential,
    sequential_exec,
    wrapper,
)
from slyme.node.exception import (
    NodeExceptionRecord,
    WrapperExceptionRecord,
)
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result
from slyme.utils.tree import TreeEngine

R = Schema(
    {
        "auto": {
            "async_temporary": Schema.leaf(mode="register"),
            "dynamic": Schema.leaf(),
            "inherited": Schema.leaf(),
            "temporary": Schema.leaf(mode="register"),
            "local": Schema.leaf(),
        },
        "async_value": Schema.leaf(),
        "input": {
            "base": Schema.leaf(),
            "label": Schema.leaf(),
            "value": Schema.leaf(),
        },
        "names": {"outer": Schema.leaf()},
        "output": {"value": Schema.leaf()},
        "sync": Schema.leaf(),
        "value": Schema.leaf(),
    }
)


async def test_acall_runs_sync_work_immediately_and_preserves_result_identity() -> None:
    events: list[str] = []
    value = object()

    @node
    def immediate(ctx: Context, /) -> object:
        events.append("called")
        return value

    ctx = Context()
    tasks = asyncio.all_tasks()
    result = immediate().acall(ctx)
    assert events == ["called"]
    assert asyncio.all_tasks() == tasks
    assert await result is value
    ctx.dispose()


def test_acall_preserves_immediate_node_errors() -> None:
    failure = ValueError("immediate failure")

    @node
    def failing(ctx: Context, /) -> int:
        raise failure

    ctx = Context()
    instance = failing()
    with pytest.raises(NodeExceptionRecord) as caught:
        instance.acall(ctx)
    assert caught.value.__cause__ is failure
    assert caught.value.exception_node is instance
    ctx.dispose()


@pytest.mark.parametrize("fails", [False, True])
async def test_acall_awaits_async_results_and_preserves_errors(fails: bool) -> None:
    events: list[str] = []
    failure = ValueError("asynchronous failure")

    @node
    async def delayed(ctx: Context, /) -> int:
        events.append("started")
        await asyncio.sleep(0)
        if fails:
            raise failure
        return 42

    ctx = Context()
    instance = delayed()
    result = instance.acall(ctx)
    assert not events
    if fails:
        with pytest.raises(NodeExceptionRecord) as caught:
            await result
        assert caught.value.__cause__ is failure
        assert caught.value.exception_node is instance
    else:
        assert await result == 42
    assert events == ["started"]
    ctx.dispose()


async def test_acall_composes_async_auto_and_wrapper_with_sync_parent() -> None:
    events: list[str] = []

    @node
    async def child(ctx: Context, /) -> int:
        async def cleanup() -> None:
            await asyncio.sleep(0)
            events.append("cleanup")

        ctx.effect(lambda: cleanup)
        return 6

    @node
    def parent(ctx: Context, /, *, value: int) -> int:
        assert events == ["cleanup"]
        return value * 2

    @wrapper
    async def increment(ctx: Context, wrapped: Node, call_next: Callable, /) -> int:
        return await await_result(call_next(ctx)) + 1

    ctx = Context()
    initial_owned = tuple(ctx._lifecycle._owned)
    graph = parent(value=Auto(child())).add_wrappers(increment())
    assert await graph.acall(ctx) == 13
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


def test_schema_integrates_with_auto_during_graph_assembly() -> None:
    schema = Schema({"input": {"value": Schema.leaf()}})

    @node
    def increment(ctx: Context, /, *, value: int) -> int:
        return value + 1

    ctx = Context()
    ctx.declare(R)
    value_ref = schema.resolve("input.value")
    ctx.set(value_ref, 4)
    assert increment(value=Auto(value_ref))(ctx) == 5

    def misspelled() -> Node[int]:
        return increment(value=Auto(schema.resolve("input.vlaue")))

    with pytest.raises(ContextPathError) as caught:
        misspelled()
    assert caught.value.args == ("Context path 'input.vlaue' is not declared.",)
    ctx.dispose()


def test_prebuilt_node_uses_schema_declared_after_runtime_fork() -> None:
    plugin_schema = Schema({"plugin": {"value": Schema.leaf()}})

    @node
    def read(ctx: Context, /, *, value: int) -> int:
        return value

    plugin_ref = plugin_schema.resolve("plugin.value")
    graph = read(value=Auto(plugin_ref))
    root = Context()
    turn = root.fork()

    root.declare(plugin_schema)
    turn.set(plugin_ref, 4)

    assert graph(turn) == 4


def test_auto_evaluation_for_refs_nodes_and_nested_containers() -> None:
    calls: list[str] = []

    @node
    def child(ctx: Context, /, *, offset: int) -> int:
        calls.append("child")
        return offset + 1

    @node
    def parent(
        ctx: Context,
        /,
        *,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return payload

    ctx = Context()
    ctx.declare(R)
    ctx.set(R.resolve("input.base"), 4)
    graph = parent(
        payload=Auto(
            {
                "raw": R.resolve("input.base"),
                "computed": child(offset=Auto(R.resolve("input.base"))),
                "constant": [1, 2],
            }
        )
    )
    assert graph(ctx) == {"raw": 4, "computed": 5, "constant": [1, 2]}
    assert calls == ["child"]
    assert eval_tree(ctx, [R.resolve("input.base"), 9]) == [4, 9]


def test_node_call_uses_live_mutable_parameter_containers() -> None:
    @node
    def mutate_argument(
        ctx: Context,
        /,
        *,
        values: list[int],
        state: dict[str, int],
    ) -> tuple[tuple[int, ...], int]:
        values.append(4)
        state["calls"] += 1
        return tuple(values), state["calls"]

    instance = mutate_argument(values=[1, 2, 3], state={"calls": 0})
    values = instance.get("values")
    state = instance.get("state")

    context_1 = Context()
    context_1.declare(R)
    assert instance(context_1) == ((1, 2, 3, 4), 1)
    assert instance.get("values") is values
    assert instance.get("state") is state
    context_2 = Context()
    context_2.declare(R)
    assert instance(context_2) == ((1, 2, 3, 4, 4), 2)


async def test_async_node_and_wrapper_calls_use_live_parameters() -> None:
    @wrapper
    async def record(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Awaitable[Any]],
        /,
        *,
        events: list[str],
    ) -> Any:
        events.append("wrapper")
        return await call_next(ctx)

    @node
    async def mutate(
        ctx: Context,
        /,
        *,
        values: list[int],
    ) -> tuple[int, ...]:
        values.append(3)
        return tuple(values)

    wrapper_instance = record(events=[])
    instance = mutate(values=[1, 2]).add_wrappers(wrapper_instance)

    context_7 = Context()
    context_7.declare(R)
    assert await instance(context_7) == (1, 2, 3)
    context_8 = Context()
    context_8.declare(R)
    assert await instance(context_8) == (1, 2, 3, 3)
    assert wrapper_instance.get("events") == ["wrapper", "wrapper"]


def test_wrappers_execute_in_declared_order_and_evaluate_parameters() -> None:
    events: list[str] = []

    @wrapper
    def trace(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
        *,
        name: str,
    ) -> Any:
        events.append(f"before:{name}:{wrapped.func.__name__}")
        result = call_next(ctx)
        events.append(f"after:{name}")
        return result

    @node
    def work(ctx: Context, /, *, value: int) -> int:
        events.append("work")
        return value

    ctx = Context()
    ctx.declare(R)
    ctx.set(R.resolve("names.outer"), "outer")
    result = work(value=7).add_wrappers(
        trace(name=Auto(R.resolve("names.outer"))), trace(name=Auto("inner"))
    )(ctx)

    assert result == 7
    assert events == [
        "before:outer:work",
        "before:inner:work",
        "work",
        "after:inner",
        "after:outer",
    ]


def test_node_and_wrapper_exceptions_preserve_provenance() -> None:
    failure = RuntimeError("boom")
    wrapper_failure = LookupError("wrapper boom")

    @node
    def fails(ctx: Context, /) -> None:
        raise failure

    instance = fails()
    with pytest.raises(NodeExceptionRecord) as exc_info:
        context_3 = Context()
        context_3.declare(R)
        instance(context_3)
    assert exc_info.value.exception_node is instance
    assert exc_info.value.__cause__ is failure
    assert "exception_node" in str(exc_info.value)
    assert exc_info.value.args == (instance,)

    @wrapper
    def broken_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        raise wrapper_failure

    wrapped_instance = fails().add_wrappers(broken_wrapper())
    with pytest.raises(WrapperExceptionRecord) as wrapper_exc:
        context_4 = Context()
        context_4.declare(R)
        wrapped_instance(context_4)
    assert wrapper_exc.value.wrapped_node is wrapped_instance
    assert isinstance(wrapper_exc.value.exception_node, Wrapper)
    assert wrapper_exc.value.__cause__ is wrapper_failure
    assert wrapper_exc.value.args == (
        wrapper_exc.value.exception_node,
        wrapped_instance,
    )
    assert "exception_wrapper" in str(wrapper_exc.value)


async def test_async_node_wrapper_and_mixed_evaluation() -> None:
    events: list[str] = []

    @node
    def sync_child(ctx: Context, /, *, value: int) -> int:
        events.append("sync")
        return value + 1

    @node
    async def async_child(ctx: Context, /, *, value: int) -> int:
        await asyncio.sleep(0)
        events.append("async")
        return value + 2

    @wrapper
    async def async_trace(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Awaitable[Any]],
        /,
        *,
        label: str,
    ) -> Any:
        events.append(f"before:{label}")
        value = await call_next(ctx)
        events.append("after")
        return value

    @node
    async def parent(
        ctx: Context,
        /,
        *,
        values: list[int],
    ) -> int:
        return sum(values)

    ctx = Context()
    ctx.declare(R)
    ctx.update({R.resolve("input.value"): 3, R.resolve("input.label"): "trace"})
    graph = parent(
        values=Auto(
            [
                sync_child(value=Auto(R.resolve("input.value"))),
                async_child(value=Auto(R.resolve("input.value"))),
            ]
        )
    ).add_wrappers(async_trace(label=Auto(R.resolve("input.label"))))

    assert await graph(ctx) == 9
    assert events[0] == "before:trace"
    assert set(events[1:3]) == {"sync", "async"}
    assert events[-1] == "after"
    assert await await_result(eval_tree(ctx, {"x": R.resolve("input.value")})) == {
        "x": 3
    }


async def test_evaluation_promotes_async_node() -> None:
    @node
    async def async_child(ctx: Context, /) -> int:
        return 1

    child = async_child()
    context_11 = Context()
    context_11.declare(R)
    assert await await_result(eval_tree(context_11, child)) == 1


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("result", [[], [1, 2]])
async def test_evaluator_result_count_is_validated(
    monkeypatch: pytest.MonkeyPatch, asynchronous: bool, result: list[int]
) -> None:
    async def evaluate(ctx, values):
        return result

    ctx = Context()
    ctx.effect(
        lambda: ctx.get(EVALUATORS_REF).register(
            ctx.scope, {int: evaluate if asynchronous else lambda ctx, values: result}
        )
    )
    ctx.declare(R)
    with pytest.raises(BaseExceptionGroup) as caught:
        await await_result(eval_tree(ctx, [1]))
    assert isinstance(caught.value.exceptions[0], ValueError)
    ctx.dispose()


def test_auto_nodes_receive_isolated_child_contexts() -> None:
    inherited = R.resolve("auto.inherited")
    temporary = R.resolve("auto.temporary")
    seen: list[Context] = []

    @node
    def child(ctx: Context, /) -> int:
        assert ctx.get(inherited) == 4
        ctx.register(temporary, len(seen))
        seen.append(ctx)
        return len(seen)

    @node
    def parent(
        ctx: Context,
        /,
        *,
        left: int,
        right: int,
    ) -> tuple[int, int]:
        return left, right

    ctx = Context()
    ctx.declare(R)
    ctx.set(inherited, 4)
    shared_child = child()
    assert parent(left=Auto(shared_child), right=Auto(shared_child))(ctx) == (1, 2)
    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert seen[0].parent is ctx
    assert seen[1].parent is ctx
    assert seen[0].scope is not seen[1].scope
    assert seen[0].scope.parents == (ctx.scope,)
    assert seen[1].scope.parents == (ctx.scope,)
    assert not ctx.exists(temporary)


def test_auto_realizes_each_original_leaf_only_once() -> None:
    dynamic = R.resolve("auto.dynamic")
    calls = 0

    @node
    def child(ctx: Context, /) -> int:
        nonlocal calls
        calls += 1
        return calls

    @node
    def parent(ctx: Context, /, *, value: Any) -> Any:
        return value

    produced_node = child()
    produced_mapping = {"child": produced_node}
    ctx = Context()
    ctx.declare(R)

    ctx.set(dynamic, produced_node)
    assert parent(value=Auto(dynamic))(ctx) is produced_node

    ctx.set(dynamic, produced_mapping)
    assert parent(value=Auto(dynamic))(ctx) is produced_mapping
    assert calls == 0


async def test_async_auto_nodes_receive_isolated_child_contexts() -> None:
    temporary = R.resolve("auto.async_temporary")
    started = 0
    both_started = asyncio.Event()
    seen: list[Context] = []

    @node
    async def child(ctx: Context, /) -> int:
        nonlocal started
        ctx.register(temporary, started)
        seen.append(ctx)
        started += 1
        if started == 2:
            both_started.set()
        await both_started.wait()
        return started

    @node
    async def parent(ctx: Context, /, *, values: list[int]) -> int:
        return sum(values)

    ctx = Context()
    ctx.declare(R)
    result = await asyncio.wait_for(
        parent(values=Auto([child(), child()]))(ctx),
        timeout=5,
    )
    assert result == 4
    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert seen[0].parent is ctx
    assert seen[1].parent is ctx
    assert seen[0].scope is not seen[1].scope
    assert seen[0].scope.parents == (ctx.scope,)
    assert seen[1].scope.parents == (ctx.scope,)
    assert not ctx.exists(temporary)


def test_sync_auto_disposes_child_effects_before_parent_execution() -> None:
    events: list[str] = []

    @node
    def child(ctx: Context, /) -> int:
        ctx.effect(lambda: lambda: events.append("cleanup"))
        events.append("child")
        return 1

    @node
    def parent(ctx: Context, /, *, value: int) -> int:
        assert events == ["child", "cleanup"]
        return value

    context_5 = Context()
    context_5.declare(R)
    assert parent(value=Auto(child()))(context_5) == 1


async def test_sync_auto_promotes_async_cleanup_before_parent_execution() -> None:
    events: list[str] = []

    @node
    def child(ctx: Context, /) -> int:
        async def cleanup() -> None:
            await asyncio.sleep(0)
            events.append("cleanup")

        ctx.effect(lambda: cleanup)
        events.append("child")
        return 1

    @node
    def parent(ctx: Context, /, *, value: int) -> int:
        assert events == ["child", "cleanup"]
        events.append("parent")
        return value

    ctx = Context()
    ctx.declare(R)
    initial_owned = tuple(ctx._lifecycle._owned)
    pending = parent(value=Auto(child()))(ctx)
    assert inspect.isawaitable(pending)
    assert events == ["child"]
    assert await await_result(pending) == 1
    assert events == ["child", "cleanup", "parent"]
    assert tuple(ctx._lifecycle._owned) == initial_owned


async def test_async_auto_awaits_child_cleanup_before_parent_execution() -> None:
    events: list[str] = []

    @node
    async def child(ctx: Context, /) -> int:
        async def cleanup() -> None:
            await asyncio.sleep(0)
            events.append("cleanup")

        ctx.effect(lambda: cleanup)
        events.append("child")
        return 1

    @node
    async def parent(ctx: Context, /, *, value: int) -> int:
        assert events == ["child", "cleanup"]
        return value

    context_9 = Context()
    context_9.declare(R)
    assert await parent(value=Auto(child()))(context_9) == 1


async def test_async_auto_runs_sync_nodes_inline_with_isolated_contexts() -> None:
    owner_thread = threading.get_ident()
    observed: list[tuple[int, int, object]] = []

    @node
    def child(ctx: Context, /, *, value: int) -> tuple[int, object]:
        ctx.set("auto.local", value)
        observed.append((value, threading.get_ident(), ctx.scope))
        return ctx.get("auto.local", local=True), ctx.scope

    @node
    async def parent(
        ctx: Context,
        /,
        *,
        values: list[tuple[int, object]],
    ) -> list[tuple[int, object]]:
        return values

    ctx = Context()
    ctx.declare(R)
    results = await parent(values=Auto([child(value=1), child(value=2)]))(ctx)

    assert [value for value, _ in results] == [1, 2]
    assert [entry[:2] for entry in observed] == [
        (1, owner_thread),
        (2, owner_thread),
    ]
    assert results[0][1] is not results[1][1]
    assert not ctx.exists("auto.local")


def test_sync_auto_chains_node_failure_under_cleanup_failure() -> None:
    def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    @node
    def child(ctx: Context, /) -> int:
        ctx.effect(lambda: fail_cleanup)
        raise ValueError("node failed")

    @node
    def parent(ctx: Context, /, *, value: int) -> int:
        return value

    child_node = child()
    with pytest.raises(NodeExceptionRecord) as caught:
        context_6 = Context()
        context_6.declare(R)
        parent(value=Auto(child_node))(context_6)

    group = caught.value.__cause__
    assert isinstance(group, BaseExceptionGroup)
    child_errors = group.exceptions[0]
    assert isinstance(child_errors, BaseExceptionGroup)
    cleanup_error = child_errors.exceptions[0]
    assert isinstance(cleanup_error, BaseExceptionGroup)
    assert isinstance(cleanup_error.exceptions[0], RuntimeError)
    failure = cleanup_error.__context__
    assert isinstance(failure, NodeExceptionRecord)
    assert failure.exception_node is child_node
    assert isinstance(failure.__cause__, ValueError)
    assert child_errors.__cause__ is None


async def test_async_auto_chains_node_failure_under_cleanup_failure() -> None:
    async def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    @node
    async def child(ctx: Context, /) -> int:
        ctx.effect(lambda: fail_cleanup)
        raise ValueError("node failed")

    @node
    async def parent(ctx: Context, /, *, value: int) -> int:
        return value

    child_node = child()
    with pytest.raises(NodeExceptionRecord) as caught:
        context_10 = Context()
        context_10.declare(R)
        await parent(value=Auto(child_node))(context_10)

    group = caught.value.__cause__
    assert isinstance(group, BaseExceptionGroup)
    child_errors = group.exceptions[0]
    assert isinstance(child_errors, BaseExceptionGroup)
    cleanup_error = child_errors.exceptions[0]
    assert isinstance(cleanup_error, BaseExceptionGroup)
    assert isinstance(cleanup_error.exceptions[0], RuntimeError)
    failure = cleanup_error.__context__
    assert isinstance(failure, NodeExceptionRecord)
    assert failure.exception_node is child_node
    assert isinstance(failure.__cause__, ValueError)
    assert child_errors.__cause__ is None


async def test_async_auto_failure_waits_for_siblings_without_cancelling() -> None:
    started = asyncio.Event()
    failed = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()
    cleaned = asyncio.Event()

    @node
    async def waiting(ctx: Context, /) -> int:
        async def cleanup() -> None:
            cleaned.set()

        ctx.effect(lambda: cleanup)
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            raise
        return 1

    @node
    async def failing(ctx: Context, /) -> int:
        await started.wait()
        failed.set()
        raise RuntimeError("child failed")

    @node
    async def parent(ctx: Context, /, *, values: list[int]) -> int:
        return sum(values)

    ctx = Context()
    ctx.declare(R)
    initial_owned = tuple(ctx._lifecycle._owned)
    task = asyncio.create_task(parent(values=Auto([waiting(), failing()]))(ctx))
    await failed.wait()
    await asyncio.sleep(0)
    assert not task.done()
    assert not cancelled.is_set()
    assert not cleaned.is_set()
    release.set()
    with pytest.raises(NodeExceptionRecord) as caught:
        await task
    group = caught.value.__cause__
    assert isinstance(group, BaseExceptionGroup)
    child_errors = group.exceptions[0]
    assert isinstance(child_errors, BaseExceptionGroup)
    failure = child_errors.exceptions[0]
    assert isinstance(failure, NodeExceptionRecord)
    assert isinstance(failure.__cause__, RuntimeError)
    assert str(failure.__cause__) == "child failed"
    assert cleaned.is_set()
    assert not cancelled.is_set()
    assert tuple(ctx._lifecycle._owned) == initial_owned


async def test_cancelled_auto_leaves_sibling_cleanup_owned_without_aggregating_errors() -> (
    None
):
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    children: list[Context] = []
    cleanup_failure = RuntimeError("sibling cleanup failed")

    @node
    async def waiting(ctx: Context, /) -> int:
        children.append(ctx)

        async def cleanup() -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            raise cleanup_failure

        ctx.effect(lambda: cleanup)
        started.set()
        return 1

    @node
    async def failing(ctx: Context, /) -> int:
        await started.wait()
        raise ValueError("primary child failure")

    @node
    async def parent(ctx: Context, /, *, values: list[int]) -> int:
        return sum(values)

    ctx = Context()
    ctx.declare(R)
    initial_owned = tuple(ctx._lifecycle._owned)
    task = asyncio.create_task(parent(values=Auto([failing(), waiting()]))(ctx))
    await cleanup_started.wait()
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.__cause__ is None
        assert children[0] in ctx.children
    finally:
        release_cleanup.set()
        with pytest.raises(BaseExceptionGroup) as cleanup_result:
            await children[0].adispose()
    assert cleanup_result.value.exceptions[0] is cleanup_failure
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


@pytest.mark.parametrize("child_fails", [False, True])
async def test_repeated_auto_cancellation_leaves_child_cleanup_running(
    child_fails: bool,
) -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()

    @node
    async def child(ctx: Context, /) -> int:
        async def cleanup() -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            cleanup_finished.set()

        ctx.effect(lambda: cleanup)
        if child_fails:
            raise ValueError("node failed")
        return 1

    @node
    async def parent(ctx: Context, /, *, value: int) -> int:
        return value

    ctx = Context()
    ctx.declare(R)
    task = asyncio.create_task(parent(value=Auto(child()))(ctx))
    await cleanup_started.wait()
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not cleanup_finished.is_set()
        assert ctx._lifecycle._owned
    finally:
        release_cleanup.set()
        await ctx.adispose()
    assert cleanup_finished.is_set()
    assert not ctx._lifecycle._owned


async def test_cancelled_auto_leaves_cleanup_failure_on_child_context() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()
    children: list[Context] = []
    cleanup_failure = RuntimeError("cleanup failed")

    @node
    async def child(ctx: Context, /) -> int:
        children.append(ctx)

        async def cleanup() -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            raise cleanup_failure

        ctx.effect(lambda: cleanup)
        return 1

    @node
    async def parent(ctx: Context, /, *, value: int) -> int:
        return value

    ctx = Context()
    ctx.declare(R)
    initial_owned = tuple(ctx._lifecycle._owned)
    task = asyncio.create_task(parent(value=Auto(child()))(ctx))
    await cleanup_started.wait()
    try:
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.__cause__ is None
        assert children[0] in ctx.children
    finally:
        release_cleanup.set()
        with pytest.raises(BaseExceptionGroup) as cleanup_result:
            await children[0].adispose()
    assert cleanup_result.value.exceptions[0] is cleanup_failure
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


def test_sequential_nodes_share_context() -> None:
    @node
    def increment(ctx: Context, /, *, source: Ref[int], target: Ref[int]) -> None:
        ctx.set(target, ctx.get(source, 0) + 1)

    first = increment(source=R.resolve("value"), target=R.resolve("value"))
    second = increment(source=R.resolve("value"), target=R.resolve("value"))
    ctx = Context()
    ctx.declare(R)
    sequential_exec(ctx, [first, second])
    assert ctx.get(R.resolve("value")) == 2

    sequential(nodes=[first, second])(ctx)
    assert ctx.get(R.resolve("value")) == 4


async def test_async_sequential_accepts_sync_and_async_nodes() -> None:
    owner_thread = threading.get_ident()
    sync_thread: int | None = None

    @node
    def sync_step(ctx: Context, /) -> None:
        nonlocal sync_thread
        sync_thread = threading.get_ident()
        ctx.set(R.resolve("sync"), True)

    @node
    async def async_step(ctx: Context, /) -> None:
        ctx.set(R.resolve("async_value"), True)

    ctx = Context()
    ctx.declare(R)
    nodes = [sync_step(), async_step()]
    await await_result(sequential_exec(ctx, nodes))
    assert ctx.get(R.resolve("sync")) and ctx.get(R.resolve("async_value"))
    assert sync_thread == owner_thread
    await await_result(sequential(nodes=nodes)(ctx))


async def test_plain_functions_assemble_independent_node_graphs() -> None:
    @node
    def leaf(ctx: Context, /, *, value: int = 1) -> int:
        return value

    def build(*, value: int = 1) -> Node[int]:
        return leaf(value=value)

    first = build()
    second = build(value=2)
    first.set("value", 3)
    ctx = Context()
    ctx.declare(R)
    assert first(ctx) == 3
    assert second(ctx) == 2
    assert build()(ctx) == 1

    @node
    async def async_leaf(ctx: Context, /) -> int:
        return 1

    def build_async() -> Node[int]:
        return async_leaf()

    assert await build_async()(ctx) == 1


def test_node_elements_can_nest_freely() -> None:
    @wrapper
    def sync_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
        *,
        dependency: Any = None,
    ) -> Any:
        return call_next(ctx)

    @node
    def sync_node(ctx: Context, /, *, dependency: Any = None) -> None:
        return None

    @node
    async def async_node(ctx: Context, /, *, dependency: Any = None) -> None:
        return None

    nested_wrapper = sync_wrapper(dependency=sync_node())
    nested_node = sync_node(dependency=nested_wrapper)
    mixed = sync_node(dependency=[nested_node, async_node()])
    assert isinstance(
        mixed.get("dependency")[0].get("dependency"),
        Wrapper,
    )
    assert isinstance(nested_wrapper.get("dependency"), Node)


async def test_wrappers_can_be_added_through_the_public_list() -> None:
    events: list[str] = []

    @wrapper
    def sync_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        events.append("sync wrapper")
        return call_next(ctx)

    @wrapper
    async def async_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Awaitable[Any]],
        /,
    ) -> Any:
        events.append("async wrapper")
        return await call_next(ctx)

    @node
    def sync_node(ctx: Context, /) -> int:
        events.append("sync node")
        return 1

    @node
    async def async_node(ctx: Context, /) -> int:
        events.append("async node")
        return 2

    sync_instance = sync_node()
    sync_instance.wrappers.append(sync_wrapper())
    async_instance = async_node()
    async_instance.wrappers.append(async_wrapper())
    ctx = Context()
    ctx.declare(R)
    assert sync_instance(ctx) == 1
    assert await async_instance(ctx) == 2
    assert events == ["sync wrapper", "sync node", "async wrapper", "async node"]


def test_node_and_wrapper_trees_support_traversal_without_reconstruction() -> None:
    @wrapper
    def trace(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        return call_next(ctx)

    @node
    def child(ctx: Context, /, *, value: int = 1) -> int:
        return value

    @node
    def parent(ctx: Context, /, *, nested: int) -> int:
        return nested

    ctx = Context()
    rules = ctx.get(NODE_TREE_REF).resolve(ctx.scope)
    assert TreeEngine.flatten(child(), rules=rules)[0] == []
    graph = parent(nested=Auto(child(value=1))).add_wrappers(trace())
    for value, expected_leaves in ((graph, [1]), (trace(), [])):
        leaves, definition = TreeEngine.flatten(value, rules=rules)
        assert leaves == expected_leaves
        assert list(TreeEngine.iter(value, rules=rules)) == expected_leaves
        with pytest.raises(TypeError, match="registered for traversal only"):
            TreeEngine.unflatten(definition, leaves)
    ctx.dispose()
