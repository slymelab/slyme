from __future__ import annotations

import asyncio
import inspect
import threading
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any

import pytest

from slyme.context import Context, Ref, Schema
from slyme.node import (
    UNDEFINED,
    Auto,
    Node,
    Wrapper,
    eval_tree,
    node,
    sequential,
    sequential_exec,
    spec,
    wrapper,
)
from slyme.node.eval import EvaluatorDef
from slyme.node.exception import (
    NodeExceptionRecord,
    NodeTerminate,
    WrapperExceptionRecord,
)
from slyme.node.signature import Spec
from slyme.node.tree import NODE_ENGINE
from slyme.utils.awaitable import resolve
from slyme.utils.registry import TypeRegistry

R = Schema(
    {
        "auto": {
            "async_temporary": Schema.leaf(),
            "dynamic": Schema.leaf(),
            "inherited": Schema.leaf(),
            "temporary": Schema.leaf(),
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
    assert caught.value.exception is failure
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
        assert caught.value.exception is failure
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
    def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        assert events == ["cleanup"]
        return value * 2

    @wrapper
    async def increment(ctx: Context, wrapped: Node, call_next: Callable, /) -> int:
        return await resolve(call_next(ctx)) + 1

    ctx = Context()
    graph = parent(value=child()).add_wrappers(increment())
    assert await graph.acall(ctx) == 13
    assert not ctx._owned
    ctx.dispose()


def test_signature_analysis_merges_specs_and_exposes_factory_signature() -> None:
    @node
    def configured(
        ctx: Context,
        /,
        *,
        required: int,
        defaulted: int = 2,
        produced: int = spec(default_factory=lambda: 3),
        dynamic: Auto[int] = spec(default=R.resolve("input.value")),
    ) -> tuple[int, int, int, int]:
        return required, defaulted, produced, dynamic

    # Public signatures retain only keyword-only build-time parameters.
    assert list(inspect.signature(configured).parameters) == [
        "required",
        "defaulted",
        "produced",
        "dynamic",
    ]
    instance = configured(required=1)
    assert instance.get("defaulted") == 2
    assert instance.get("produced") == 3
    assert instance.get("dynamic") == R.resolve("input.value")
    ctx = Context(schema=R)
    ctx.set(R.resolve("input.value"), 4)
    assert instance(ctx) == (1, 2, 3, 4)


def test_schema_integrates_with_auto_during_graph_assembly() -> None:
    schema = Schema({"input": {"value": Schema.leaf()}})

    @node
    def increment(ctx: Context, /, *, value: Auto[int]) -> int:
        return value + 1

    ctx = Context(schema=R)
    value_ref = schema.resolve("input.value")
    ctx.set(value_ref, 4)
    assert increment(value=value_ref)(ctx) == 5

    def misspelled() -> Node[int]:
        return increment(value=schema.resolve("input.vlaue"))

    with pytest.raises(KeyError, match="Did you mean 'value'"):
        misspelled()


def test_prebuilt_node_uses_schema_declared_after_runtime_fork() -> None:
    plugin_schema = Schema({"plugin": {"value": Schema.leaf()}})

    @node
    def read(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    plugin_ref = plugin_schema.resolve("plugin.value")
    graph = read(value=plugin_ref)
    root = Context()
    turn = root.fork()

    root.declare(plugin_schema)
    turn.set(plugin_ref, 4)

    assert graph(turn) == 4


def test_spec_validation_and_missing_parameters() -> None:
    with pytest.raises(ValueError, match="cannot be set at the same time"):
        Spec(default=1, default_factory=lambda: 2)
    with pytest.raises(ValueError, match="auto_eval"):
        Spec().should_eval()
    assert Spec(auto_eval=True).should_eval()
    assert not Spec(auto_eval=False).should_eval()

    @node
    def required(ctx: Context, /, *, value: int) -> int:
        return value

    item = required()
    assert item.get("value") is UNDEFINED
    with pytest.raises(NodeExceptionRecord) as exc_info:
        item(Context(schema=R))
    assert isinstance(exc_info.value.exception, ValueError)
    assert "Missing required parameter" in str(exc_info.value.exception)


def test_decorator_validation_and_factory_behavior() -> None:
    with pytest.raises(TypeError, match="exactly 1 runtime"):

        @node
        def no_context(*, value: int) -> int:
            return value

    with pytest.raises(TypeError, match="exactly 3 runtime"):

        @wrapper
        def bad_wrapper(ctx: Context, /) -> None:
            return None

    @node
    def names_can_overlap_framework_api(
        ctx: Context,
        /,
        *,
        func: int = 1,
        get: int = 2,
        wrappers: int = 3,
    ) -> tuple[int, int, int]:
        return func, get, wrappers

    overlapping = names_can_overlap_framework_api()
    assert overlapping.get("func") == 1
    assert overlapping.get("get") == 2
    assert overlapping.get("wrappers") == 3
    assert overlapping.wrappers == []
    assert overlapping(Context(schema=R)) == (1, 2, 3)

    @node
    async def detected(ctx: Context, /) -> int:
        return 1

    assert detected.element_type is Node
    assert detected.func.__name__ == "detected"


def test_variadic_node_and_wrapper_parameters_are_rejected() -> None:
    with pytest.raises(TypeError, match=r"Variadic parameter '\*args'"):

        @node
        def variadic_node_args(*args: Any) -> None:
            return None

    with pytest.raises(TypeError, match=r"Variadic parameter '\*\*kwargs'"):

        @node
        def variadic_node_kwargs(**kwargs: Any) -> None:
            return None

    with pytest.raises(TypeError, match=r"Variadic parameter '\*args'"):

        @wrapper
        def variadic_wrapper_args(ctx: Context, wrapped: Node[Any], *args: Any) -> Any:
            return None

    with pytest.raises(TypeError, match=r"Variadic parameter '\*\*kwargs'"):

        @wrapper
        def variadic_wrapper_kwargs(
            ctx: Context,
            wrapped: Node[Any],
            /,
            **kwargs: Any,
        ) -> Any:
            return None


def test_node_parameters_are_explicit_and_mutable() -> None:
    @node
    def identity(ctx: Context, /, *, value: int = 1) -> int:
        return value

    instance = identity()
    instance.set("value", 2)
    assert instance(Context(schema=R)) == 2
    assert instance.get("value") == 2
    assert instance.params == {"value": 2}
    assert isinstance(instance.params, MappingProxyType)
    assert instance.specs["value"].default == 1
    assert instance.func is identity.func
    instance.reset("value")
    assert instance.get("value") == 1
    with pytest.raises(KeyError, match="Unknown parameter"):
        instance.get("other")
    with pytest.raises(KeyError, match="Unknown parameter"):
        instance.set("other", 3)
    with pytest.raises(KeyError, match="Unknown parameter"):
        instance.reset("other")
    with pytest.raises(AttributeError):
        instance.value = 3  # type: ignore[attr-defined]
    with pytest.raises(TypeError):
        instance.params["value"] = 3  # type: ignore[index]
    with pytest.raises(TypeError, match="unexpected keyword"):
        identity(other=3)  # type: ignore[call-arg]


def test_auto_evaluation_for_refs_nodes_and_nested_containers() -> None:
    calls: list[str] = []

    @node
    def child(ctx: Context, /, *, offset: Auto[int]) -> int:
        calls.append("child")
        return offset + 1

    @node
    def parent(
        ctx: Context,
        /,
        *,
        payload: Auto[dict[str, Any]],
    ) -> dict[str, Any]:
        return payload

    ctx = Context(schema=R)
    ctx.set(R.resolve("input.base"), 4)
    graph = parent(
        payload={
            "raw": R.resolve("input.base"),
            "computed": child(offset=R.resolve("input.base")),
            "constant": [1, 2],
        }
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

    assert instance(Context(schema=R)) == ((1, 2, 3, 4), 1)
    assert instance.get("values") is values
    assert instance.get("state") is state
    assert instance(Context(schema=R)) == ((1, 2, 3, 4, 4), 2)


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

    assert await instance(Context(schema=R)) == (1, 2, 3)
    assert await instance(Context(schema=R)) == (1, 2, 3, 3)
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
        name: Auto[str],
    ) -> Any:
        events.append(f"before:{name}:{wrapped.func.__name__}")
        result = call_next(ctx)
        events.append(f"after:{name}")
        return result

    @node
    def work(ctx: Context, /, *, value: int) -> int:
        events.append("work")
        return value

    ctx = Context(schema=R)
    ctx.set(R.resolve("names.outer"), "outer")
    result = work(value=7).add_wrappers(
        trace(name=R.resolve("names.outer")), trace(name="inner")
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
    @node
    def fails(ctx: Context, /) -> None:
        raise RuntimeError("boom")

    instance = fails()
    with pytest.raises(NodeExceptionRecord) as exc_info:
        instance(Context(schema=R))
    assert exc_info.value.exception_node is instance
    assert isinstance(exc_info.value.exception, RuntimeError)
    assert "exception_node" in str(exc_info.value)
    assert exc_info.value.__cause__ is exc_info.value.exception

    @wrapper
    def broken_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        raise LookupError("wrapper boom")

    wrapped_instance = fails().add_wrappers(broken_wrapper())
    with pytest.raises(WrapperExceptionRecord) as wrapper_exc:
        wrapped_instance(Context(schema=R))
    assert wrapper_exc.value.wrapped_node is wrapped_instance
    assert isinstance(wrapper_exc.value.exception_node, Wrapper)
    assert isinstance(wrapper_exc.value.exception, LookupError)
    assert "exception_wrapper" in str(wrapper_exc.value)


def test_node_terminate_sets_source_once() -> None:
    @node
    def stop(ctx: Context, /) -> None:
        raise NodeTerminate("done")

    instance = stop()
    with pytest.raises(NodeTerminate) as exc_info:
        instance(Context(schema=R))
    assert exc_info.value.source_node is instance
    assert exc_info.value.msg == "done"
    exc_info.value.msg = "changed"
    assert "changed" in str(exc_info.value)


async def test_async_node_wrapper_and_mixed_evaluation() -> None:
    events: list[str] = []

    @node
    def sync_child(ctx: Context, /, *, value: Auto[int]) -> int:
        events.append("sync")
        return value + 1

    @node
    async def async_child(ctx: Context, /, *, value: Auto[int]) -> int:
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
        label: Auto[str],
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
        values: Auto[list[int]],
    ) -> int:
        return sum(values)

    ctx = Context(schema=R)
    ctx.update({R.resolve("input.value"): 3, R.resolve("input.label"): "trace"})
    graph = parent(
        values=[
            sync_child(value=R.resolve("input.value")),
            async_child(value=R.resolve("input.value")),
        ]
    ).add_wrappers(async_trace(label=R.resolve("input.label")))

    assert await graph(ctx) == 9
    assert events[0] == "before:trace"
    assert set(events[1:3]) == {"sync", "async"}
    assert events[-1] == "after"
    assert await resolve(eval_tree(ctx, {"x": R.resolve("input.value")})) == {"x": 3}


async def test_evaluation_promotes_async_node() -> None:
    @node
    async def async_child(ctx: Context, /) -> int:
        return 1

    child = async_child()
    assert await resolve(eval_tree(Context(schema=R), child)) == 1


def test_evaluator_result_count_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = TypeRegistry[Any, EvaluatorDef]("test_evaluator")
    evaluator = EvaluatorDef(lambda _ctx, _values: [])
    registry.register(evaluator, key=int)
    monkeypatch.setattr("slyme.node.eval.EVALUATOR_REGISTRY", registry)
    with pytest.raises(ValueError, match="expected 1"):
        eval_tree(Context(schema=R), [1])


async def _empty_async() -> list[Any]:
    return []


async def test_async_evaluator_result_count_is_validated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = TypeRegistry[Any, EvaluatorDef]("test_evaluator")
    evaluator = EvaluatorDef(lambda _ctx, _values: _empty_async())
    registry.register(evaluator, key=int)
    monkeypatch.setattr("slyme.node.eval.EVALUATOR_REGISTRY", registry)
    with pytest.raises(ValueError, match="expected 1"):
        await resolve(eval_tree(Context(schema=R), [1]))


def test_auto_nodes_receive_isolated_child_contexts() -> None:
    inherited = R.resolve("auto.inherited")
    temporary = R.resolve("auto.temporary")
    seen: list[Context] = []

    @node
    def child(ctx: Context, /) -> int:
        assert ctx.get(inherited) == 4
        ctx.add(temporary, len(seen))
        seen.append(ctx)
        return len(seen)

    @node
    def parent(
        ctx: Context,
        /,
        *,
        left: Auto[int],
        right: Auto[int],
    ) -> tuple[int, int]:
        return left, right

    ctx = Context(schema=R)
    ctx.set(inherited, 4)
    shared_child = child()
    assert parent(left=shared_child, right=shared_child)(ctx) == (1, 2)
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
    def parent(ctx: Context, /, *, value: Auto[Any]) -> Any:
        return value

    produced_node = child()
    produced_mapping = {"child": produced_node}
    ctx = Context(schema=R)

    ctx.set(dynamic, produced_node)
    assert parent(value=dynamic)(ctx) is produced_node

    ctx.set(dynamic, produced_mapping)
    assert parent(value=dynamic)(ctx) is produced_mapping
    assert calls == 0


async def test_async_auto_nodes_receive_isolated_child_contexts() -> None:
    temporary = R.resolve("auto.async_temporary")
    started = 0
    both_started = asyncio.Event()
    seen: list[Context] = []

    @node
    async def child(ctx: Context, /) -> int:
        nonlocal started
        ctx.add(temporary, started)
        seen.append(ctx)
        started += 1
        if started == 2:
            both_started.set()
        await both_started.wait()
        return started

    @node
    async def parent(ctx: Context, /, *, values: Auto[list[int]]) -> int:
        return sum(values)

    ctx = Context(schema=R)
    result = await asyncio.wait_for(
        parent(values=[child(), child()])(ctx),
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
    def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        assert events == ["child", "cleanup"]
        return value

    assert parent(value=child())(Context(schema=R)) == 1


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
    def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        assert events == ["child", "cleanup"]
        events.append("parent")
        return value

    ctx = Context(schema=R)
    pending = parent(value=child())(ctx)
    assert inspect.isawaitable(pending)
    assert events == ["child"]
    assert await resolve(pending) == 1
    assert events == ["child", "cleanup", "parent"]
    assert not ctx._owned


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
    async def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        assert events == ["child", "cleanup"]
        return value

    assert await parent(value=child())(Context(schema=R)) == 1


async def test_async_auto_runs_sync_nodes_inline_with_isolated_contexts() -> None:
    owner_thread = threading.get_ident()
    observed: list[tuple[int, int, object]] = []

    @node
    def child(ctx: Context, /, *, value: int) -> tuple[int, object]:
        ctx.set("auto.temporary", value)
        observed.append((value, threading.get_ident(), ctx.scope))
        return ctx.get("auto.temporary", local=True), ctx.scope

    @node
    async def parent(
        ctx: Context,
        /,
        *,
        values: Auto[list[tuple[int, object]]],
    ) -> list[tuple[int, object]]:
        return values

    ctx = Context(schema=R)
    results = await parent(values=[child(value=1), child(value=2)])(ctx)

    assert [value for value, _ in results] == [1, 2]
    assert [entry[:2] for entry in observed] == [
        (1, owner_thread),
        (2, owner_thread),
    ]
    assert results[0][1] is not results[1][1]
    assert not ctx.exists("auto.temporary")


def test_sync_auto_preserves_node_failure_when_cleanup_also_fails() -> None:
    def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    @node
    def child(ctx: Context, /) -> int:
        ctx.effect(lambda: fail_cleanup)
        raise ValueError("node failed")

    @node
    def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    child_node = child()
    with pytest.raises(NodeExceptionRecord) as caught:
        parent(value=child_node)(Context(schema=R))

    assert caught.value.exception_node is child_node
    assert isinstance(caught.value.exception, ValueError)
    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_async_auto_preserves_node_failure_when_cleanup_also_fails() -> None:
    async def fail_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    @node
    async def child(ctx: Context, /) -> int:
        ctx.effect(lambda: fail_cleanup)
        raise ValueError("node failed")

    @node
    async def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    child_node = child()
    with pytest.raises(NodeExceptionRecord) as caught:
        await parent(value=child_node)(Context(schema=R))

    assert caught.value.exception_node is child_node
    assert isinstance(caught.value.exception, ValueError)
    assert isinstance(caught.value.__cause__, RuntimeError)


async def test_async_auto_failure_disposes_cancelled_siblings() -> None:
    started = asyncio.Event()
    cleaned = asyncio.Event()

    @node
    async def waiting(ctx: Context, /) -> int:
        async def cleanup() -> None:
            cleaned.set()

        ctx.effect(lambda: cleanup)
        started.set()
        await asyncio.Event().wait()
        return 1

    @node
    async def failing(ctx: Context, /) -> int:
        await started.wait()
        raise RuntimeError("child failed")

    @node
    async def parent(ctx: Context, /, *, values: Auto[list[int]]) -> int:
        return sum(values)

    with pytest.raises(NodeExceptionRecord) as caught:
        await parent(values=[waiting(), failing()])(Context(schema=R))
    assert isinstance(caught.value.exception, RuntimeError)
    assert str(caught.value.exception) == "child failed"
    assert cleaned.is_set()


async def test_async_auto_preserves_cancelled_sibling_cleanup_failure() -> None:
    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    @node
    async def waiting(ctx: Context, /) -> int:
        async def cleanup() -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            raise RuntimeError("sibling cleanup failed")

        ctx.effect(lambda: cleanup)
        started.set()
        await asyncio.Event().wait()
        return 1

    @node
    async def failing(ctx: Context, /) -> int:
        await started.wait()
        raise ValueError("primary child failure")

    @node
    async def parent(ctx: Context, /, *, values: Auto[list[int]]) -> int:
        return sum(values)

    ctx = Context(schema=R)
    task = asyncio.create_task(parent(values=[waiting(), failing()])(ctx))
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release_cleanup.set()
    with pytest.raises(NodeExceptionRecord) as caught:
        await task

    assert isinstance(caught.value.exception, ValueError)
    assert str(caught.value.exception) == "primary child failure"
    details = caught.value.__cause__
    assert details is not None
    errors = getattr(details, "errors", (details,))
    assert any(
        isinstance(error, RuntimeError) and str(error) == "sibling cleanup failed"
        for error in errors
    )
    assert not ctx._owned


async def test_repeated_auto_cancellation_finishes_child_cleanup() -> None:
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
        return 1

    @node
    async def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    ctx = Context(schema=R)
    task = asyncio.create_task(parent(value=child())(ctx))
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release_cleanup.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleanup_finished.is_set()
    assert not ctx._owned


async def test_repeated_auto_cancellation_preserves_cleanup_failure() -> None:
    cleanup_started = asyncio.Event()
    release_cleanup = asyncio.Event()

    @node
    async def child(ctx: Context, /) -> int:
        async def cleanup() -> None:
            cleanup_started.set()
            await release_cleanup.wait()
            raise RuntimeError("cleanup failed")

        ctx.effect(lambda: cleanup)
        return 1

    @node
    async def parent(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    ctx = Context(schema=R)
    task = asyncio.create_task(parent(value=child())(ctx))
    await cleanup_started.wait()
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()

    release_cleanup.set()
    with pytest.raises(NodeExceptionRecord) as caught:
        await task
    assert isinstance(caught.value.exception, RuntimeError)
    assert str(caught.value.exception) == "cleanup failed"
    assert caught.value.__cause__ is caught.value.exception
    assert isinstance(caught.value.exception.__cause__, asyncio.CancelledError)
    assert not ctx._owned


def test_sequential_nodes_share_context() -> None:
    @node
    def increment(ctx: Context, /, *, source: Ref[int], target: Ref[int]) -> None:
        ctx.set(target, ctx.get(source, 0) + 1)

    first = increment(source=R.resolve("value"), target=R.resolve("value"))
    second = increment(source=R.resolve("value"), target=R.resolve("value"))
    ctx = Context(schema=R)
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

    ctx = Context(schema=R)
    nodes = [sync_step(), async_step()]
    await resolve(sequential_exec(ctx, nodes))
    assert ctx.get(R.resolve("sync")) and ctx.get(R.resolve("async_value"))
    assert sync_thread == owner_thread
    await resolve(sequential(nodes=nodes)(ctx))


async def test_plain_functions_assemble_independent_node_graphs() -> None:
    @node
    def leaf(ctx: Context, /, *, value: int = 1) -> int:
        return value

    def build(*, value: int = 1) -> Node[int]:
        return leaf(value=value)

    first = build()
    second = build(value=2)
    first.set("value", 3)
    ctx = Context(schema=R)
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
    ctx = Context(schema=R)
    assert sync_instance(ctx) == 1
    assert await async_instance(ctx) == 2
    assert events == ["sync wrapper", "sync node", "async wrapper", "async node"]


def test_node_tree_round_trip() -> None:
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
    def parent(ctx: Context, /, *, nested: Auto[int]) -> int:
        return nested

    graph = parent(nested=child()).add_wrappers(trace())
    leaves, definition = NODE_ENGINE.flatten(graph)
    rebuilt = NODE_ENGINE.unflatten(definition, leaves)
    assert isinstance(rebuilt, Node)
    assert rebuilt(Context(schema=R)) == 1
