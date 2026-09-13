from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from slyme.context import Context, Ref, Schema
from slyme.node import Auto, Node, eval_tree, node, wrapper
from slyme.node.eval import EVALUATOR_REGISTRY, EvaluatorDef, node_evaluator
from slyme.node.exception import NodeExceptionRecord
from slyme.utils.continuation import await_result
from slyme.utils.registry import GeneralRegistry
from slyme.utils.tree import TreeAux, TreeEngine


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_auto_reports_all_child_and_cleanup_failures(asynchronous: bool) -> None:
    events = []
    failures = [ValueError("first"), RuntimeError("second")]
    cleanup_failures = [OSError("first cleanup"), OSError("second cleanup")]

    @node
    def failing(ctx: Context, /, *, index: int) -> Any:
        def cleanup() -> None:
            events.append(("cleanup", index))
            raise cleanup_failures[index]

        def run() -> None:
            ctx.effect(lambda: cleanup)
            events.append(("run", index))
            raise failures[index]

        async def arun() -> None:
            await asyncio.sleep(0)
            run()

        return arun() if asynchronous else run()

    @node
    def successful(ctx: Context, /) -> int:
        ctx.effect(lambda: lambda: events.append(("cleanup", 2)))
        events.append(("run", 2))
        return 2

    ctx = Context()
    with pytest.raises(NodeExceptionRecord) as caught:
        await await_result(
            node_evaluator(ctx, [failing(index=0), failing(index=1), successful()])
        )
    assert caught.value.exception is failures[0]
    cause = caught.value.__cause__
    assert cause is not None
    additional = getattr(cause, "errors", (cause,))
    assert isinstance(additional[0], NodeExceptionRecord)
    assert additional[0].exception is failures[1]
    assert additional[1:] == tuple(cleanup_failures)
    for index in range(3):
        assert events.count(("run", index)) == 1
        assert events.count(("cleanup", index)) == 1
    assert not ctx._owned


def test_auto_reports_retained_cleanup_failure_once() -> None:
    failure = RuntimeError("cleanup failed")
    events = []

    @node
    def first(ctx: Context, /) -> int:
        def cleanup() -> None:
            events.append("cleanup")
            raise failure

        ctx.effect(lambda: cleanup)
        return 1

    @node
    def second(ctx: Context, /) -> int:
        events.append("second")
        return 2

    ctx = Context()
    with pytest.raises(RuntimeError) as caught:
        node_evaluator(ctx, [first(), second()])
    assert caught.value is failure
    assert caught.value.__cause__ is None
    assert events == ["cleanup", "second"]
    assert not ctx._owned


async def test_sync_auto_failure_still_starts_async_siblings() -> None:
    visited = []

    @node
    def failing(ctx: Context, /) -> int:
        visited.append("sync")
        raise ValueError("failed")

    @node
    async def asynchronous(ctx: Context, /) -> int:
        visited.append("async")
        return 1

    ctx = Context()
    pending = node_evaluator(ctx, [failing(), asynchronous()])
    assert inspect.isawaitable(pending)
    assert visited == ["sync"]
    with pytest.raises(NodeExceptionRecord):
        await pending
    assert visited == ["sync", "async"]
    assert not ctx._owned


async def test_auto_keeps_exception_objects_returned_as_data() -> None:
    first = ValueError("data")
    second = asyncio.CancelledError("also data")

    @node
    async def asynchronous(ctx: Context, /) -> BaseException:
        return first

    @node
    def synchronous(ctx: Context, /) -> BaseException:
        return second

    ctx = Context()
    result = await await_result(node_evaluator(ctx, [asynchronous(), synchronous()]))
    assert result[0] is first and result[1] is second
    assert not ctx._owned


async def test_auto_disposes_successful_child_before_siblings_finish() -> None:
    cleaned = asyncio.Event()
    release = asyncio.Event()

    @node
    async def successful(ctx: Context, /) -> int:
        ctx.effect(lambda: cleaned.set)
        return 1

    @node
    async def waiting(ctx: Context, /) -> int:
        await release.wait()
        return 2

    ctx = Context()
    task = asyncio.create_task(
        await_result(node_evaluator(ctx, [successful(), waiting()]))
    )
    await cleaned.wait()
    assert not task.done()
    release.set()
    assert await task == [1, 2]
    assert not ctx._owned


async def test_business_cancellation_does_not_cancel_auto_siblings() -> None:
    operation = asyncio.create_task(asyncio.Event().wait())
    started = asyncio.Event()
    release = asyncio.Event()
    events = []

    @node
    async def cancelled(ctx: Context, /) -> int:
        ctx.effect(lambda: lambda: events.append("cleanup:cancelled"))
        await operation
        return 1

    @node
    async def sibling(ctx: Context, /) -> int:
        ctx.effect(lambda: lambda: events.append("cleanup:sibling"))
        started.set()
        await release.wait()
        events.append("sibling finished")
        return 2

    ctx = Context()
    task = asyncio.create_task(
        await_result(node_evaluator(ctx, [cancelled(), sibling()]))
    )
    await started.wait()
    operation.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "sibling finished" in events
    assert events.count("cleanup:cancelled") == 1
    assert events.count("cleanup:sibling") == 1
    assert not ctx._owned


async def test_caller_cancellation_waits_for_node_exit_before_disposal() -> None:
    started = asyncio.Event()
    exiting = asyncio.Event()
    release = asyncio.Event()
    cleaned = asyncio.Event()

    @node
    async def child(ctx: Context, /) -> int:
        ctx.effect(lambda: cleaned.set)
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            exiting.set()
            await release.wait()
            assert not cleaned.is_set()
        return 1

    ctx = Context()
    task = asyncio.create_task(await_result(node_evaluator(ctx, [child()])))
    await started.wait()
    task.cancel()
    await exiting.wait()
    assert not task.done()
    assert not cleaned.is_set()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set()
    assert not ctx._owned


def test_auto_subclasses_require_explicit_evaluator_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CustomRef(Ref[int]):
        pass

    class CustomNode(Node[int]):
        pass

    registry = GeneralRegistry[type, EvaluatorDef]("test_evaluator")
    for key, evaluator in EVALUATOR_REGISTRY.items():
        registry.register(evaluator, key=key)
    monkeypatch.setattr("slyme.node.eval.EVALUATOR_REGISTRY", registry)
    ctx = Context({"value": 7}, schema=Schema({"value": Schema.leaf()}))
    custom_ref = CustomRef("value")
    custom_node = CustomNode(func=lambda ctx: 3, specs={}, params={})

    @node
    def collect(ctx: Context, /, *, values: Auto[list[Any]]) -> list[Any]:
        return values

    graph = collect(values=[Ref("value"), custom_ref, custom_node])
    result = graph(ctx)
    assert result[0] == 7
    assert result[1] is custom_ref and result[2] is custom_node
    registry.register(registry.get(Ref), key=CustomRef)
    registry.register(registry.get(Node), key=CustomNode)
    assert graph(ctx) == [7, 7, 3]
    registry.unregister(CustomRef)
    registry.unregister(CustomNode)
    result = graph(ctx)
    assert result[1] is custom_ref and result[2] is custom_node
    ctx.dispose()


def test_integer_evaluator_does_not_evaluate_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = GeneralRegistry[type, EvaluatorDef]("test_evaluator")
    registry.register(
        EvaluatorDef(lambda _ctx, values: [value + 1 for value in values]), key=int
    )
    monkeypatch.setattr("slyme.node.eval.EVALUATOR_REGISTRY", registry)
    ctx = Context()
    result = eval_tree(ctx, [1, True])
    assert result[0] == 2 and result[1] is True
    ctx.dispose()


@pytest.mark.parametrize("target", ["node", "wrapper", "eval_tree"])
@pytest.mark.parametrize("dynamic", [False, True])
async def test_auto_reconstructs_containers_and_preserves_ordinary_leaves(
    target: str, dynamic: bool
) -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context({"value": 7}, schema=schema)
    marker = object()
    payload = {
        "plain": [{"marker": marker}, [], {}],
        "tuple": ([1, 2],),
        "value": schema.resolve("value") if dynamic else 7,
    }

    @node
    def identity(ctx: Context, /, *, value: Auto[Any]) -> Any:
        return value

    @wrapper
    def capture(
        ctx: Context, wrapped: Node, call_next: Callable, /, *, value: Auto[Any]
    ) -> Any:
        return value

    if target == "node":
        call = identity(value=payload)
    elif target == "wrapper":
        call = identity(value=None).add_wrappers(capture(value=payload))
    else:

        def call(ctx):
            return eval_tree(ctx, payload)

    first = await await_result(call(ctx))
    second = await await_result(call(ctx))
    assert first is not second
    for result in (first, second):
        assert result is not payload
        assert result["plain"] is not payload["plain"]
        for original, rebuilt in zip(payload["plain"], result["plain"], strict=True):
            assert rebuilt is not original
        assert result["plain"][0]["marker"] is marker
        assert result["tuple"] is not payload["tuple"]
        assert result["tuple"][0] is not payload["tuple"][0]
        assert result["value"] == 7
    first["plain"].append(3)
    assert len(payload["plain"]) == len(second["plain"]) == 3
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_auto_and_non_auto_parameters_have_declaration_driven_identity(
    asynchronous: bool,
) -> None:
    @node
    def identity(ctx: Context, /, *, evaluated: Auto[Any], raw: Any) -> Any:
        return evaluated, raw

    @node
    async def async_identity(ctx: Context, /, *, evaluated: Auto[Any], raw: Any) -> Any:
        return evaluated, raw

    ctx = Context()
    marker = object()
    payload = [marker]
    factory = async_identity if asynchronous else identity
    evaluated, raw = await factory(evaluated=payload, raw=payload).acall(ctx)
    assert evaluated is not payload
    assert evaluated[0] is marker
    assert raw is payload
    evaluated, raw = await factory(evaluated=marker, raw=marker).acall(ctx)
    assert evaluated is raw is marker
    ctx.dispose()


@pytest.mark.parametrize("target", ["node", "wrapper"])
def test_auto_flattens_custom_container_once_and_keeps_parameter_bindings(
    monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    class Box:
        def __init__(self, value: Any):
            self.value = value

    calls = 0
    engine = TreeEngine("test_auto", register_defaults=True)

    def flatten(box):
        nonlocal calls
        calls += 1
        element.set("other", 2)
        return iter((box.value,)), TreeAux()

    engine.register(Box, flatten, lambda children, aux: Box(next(iter(children))))
    monkeypatch.setattr("slyme.node.eval.CTX_EVAL_ENGINE", engine)

    @node
    def identity(ctx: Context, /, *, value: Auto[Any], other: int) -> Any:
        return value, other

    @wrapper
    def capture(
        ctx: Context,
        wrapped: Node,
        call_next: Callable,
        /,
        *,
        value: Auto[Any],
        other: int,
    ) -> Any:
        return value, other

    marker = object()
    box = Box(marker)
    if target == "node":
        element = graph = identity(value=box, other=1)
    else:
        element = capture(value=box, other=1)
        graph = identity(value=None, other=0).add_wrappers(element)
    ctx = Context()
    result, other = graph(ctx)
    assert calls == 1
    assert result is not box
    assert result.value is marker
    assert other == 1
    assert element.get("other") == 2
    ctx.dispose()


def test_wrapper_reentry_evaluates_current_auto_container_each_time() -> None:
    schema = Schema({"value": Schema.leaf()})
    payload = [1]

    @node
    def read(ctx: Context, /, *, values: Auto[list[int]]) -> list[int]:
        return values

    @wrapper
    def repeat(ctx: Context, wrapped: Node, call_next: Callable, /) -> Any:
        wrapped.set("values", [42])
        first = call_next(ctx)
        payload.append(schema.resolve("value"))
        ctx.set("value", 3)
        return first, call_next(ctx)

    ctx = Context({"value": 2}, schema=schema)
    graph = read(values=payload).add_wrappers(repeat())
    assert graph(ctx) == ([1], [1, 3])
    assert graph(ctx) == ([42], [42])
    ctx.dispose()


def test_short_circuiting_wrapper_does_not_traverse_auto_parameters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Box:
        pass

    def flatten(box):
        raise AssertionError("Auto traversal was short-circuited")

    engine = TreeEngine("test_auto", register_defaults=True)
    engine.register(Box, flatten, lambda children, aux: Box())
    monkeypatch.setattr("slyme.node.eval.CTX_EVAL_ENGINE", engine)

    @node
    def identity(ctx: Context, /, *, value: Auto[Any]) -> Any:
        return value

    @wrapper
    def stop(ctx: Context, wrapped: Node, call_next: Callable, /) -> int:
        return 12

    ctx = Context()
    assert identity(value=Box()).add_wrappers(stop())(ctx) == 12
    ctx.dispose()
