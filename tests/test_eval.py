from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

import pytest

from slyme.context import Context, Ref, Schema
from slyme.context.core import ContextPathError
from slyme.context.default import DATA_TREE_REF, EVALUATORS_REF
from slyme.node import Auto, Node, eval_tree, node, wrapper
from slyme.node.eval import node_evaluator
from slyme.node.exception import NodeExceptionRecord, WrapperExceptionRecord
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result
from slyme.utils.tree import TreeAux, TreeHandler, TreeRules


def test_same_evaluator_batches_leaves_registered_under_multiple_types(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = []

    def evaluate(ctx, values):
        calls.append(list(values))
        return [str(value) for value in values]

    ctx = Context()
    ctx.effect(
        lambda: ctx.get(EVALUATORS_REF).register(
            ctx.scope, {int: evaluate, str: evaluate}
        )
    )
    assert eval_tree(ctx, [1, "a", 2]) == ["1", "a", "2"]
    assert calls == [[1, "a", 2]]
    ctx.dispose()


@pytest.mark.parametrize("acall", [False, True])
def test_auto_async_result_can_be_created_before_starting_event_loop(
    acall: bool,
) -> None:
    events = []

    @node
    async def child(ctx: Context, /) -> int:
        async def cleanup() -> None:
            await asyncio.sleep(0)
            events.append("cleanup")

        ctx.effect(lambda: cleanup)
        events.append("child")
        return 7

    @node
    def parent(ctx: Context, /, *, value: int) -> int:
        events.append("parent")
        return value

    ctx = Context()
    initial_owned = tuple(ctx._lifecycle._owned)
    graph = parent(value=Auto(child()))
    pending = graph.acall(ctx) if acall else graph(ctx)
    assert not events
    assert asyncio.run(await_result(pending)) == 7
    assert events == ["child", "cleanup", "parent"]
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


async def test_concurrent_evaluations_keep_results_and_children_separate() -> None:
    root = Context()
    root.declare({"value": Schema.leaf()})
    contexts = [root.fork(scope=root.scope.fork()) for _ in range(2)]
    started = [asyncio.Event(), asyncio.Event()]

    @node
    async def read(ctx: Context, /, *, index: int) -> int:
        started[index].set()
        await started[1 - index].wait()
        return ctx.get("value") + 1

    for index, ctx in enumerate(contexts):
        ctx.set("value", index * 10)
    owned = [tuple(ctx._lifecycle._owned) for ctx in contexts]
    pending = [
        await_result(eval_tree(ctx, [ctx.resolve("value"), read(index=index)]))
        for index, ctx in enumerate(contexts)
    ]
    assert await asyncio.wait_for(asyncio.gather(*pending), timeout=1) == [
        [0, 1],
        [10, 11],
    ]
    assert [tuple(ctx._lifecycle._owned) for ctx in contexts] == owned
    root.dispose()


@pytest.mark.parametrize("fail", [False, True])
async def test_evaluator_groups_run_concurrently_and_settle_before_reporting(
    monkeypatch: pytest.MonkeyPatch, fail: bool
) -> None:
    started = [asyncio.Event(), asyncio.Event()]
    failures = [ValueError("integer evaluator"), LookupError("string evaluator")]
    finished = []

    async def integers(ctx, values):
        started[0].set()
        await started[1].wait()
        finished.append(0)
        if fail:
            raise failures[0]
        return [value + 1 for value in values]

    async def strings(ctx, values):
        started[1].set()
        await started[0].wait()
        finished.append(1)
        if fail:
            raise failures[1]
        return [value.upper() for value in values]

    ctx = Context()
    ctx.effect(
        lambda: ctx.get(EVALUATORS_REF).register(
            ctx.scope, {int: integers, str: strings}
        )
    )
    pending = await_result(eval_tree(ctx, [1, "a", 2, "b"]))
    if fail:
        with pytest.raises(BaseExceptionGroup) as caught:
            await asyncio.wait_for(pending, timeout=1)
        assert caught.value.exceptions == tuple(failures)
    else:
        assert await asyncio.wait_for(pending, timeout=1) == [2, "A", 3, "B"]
    assert sorted(finished) == [0, 1]
    ctx.dispose()


@pytest.mark.parametrize("target", ["node", "wrapper", "eval_tree"])
async def test_auto_collects_ref_and_node_errors_across_groups(target: str) -> None:
    schema = Schema({"first": Schema.leaf(), "second": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    initial_owned = tuple(ctx._lifecycle._owned)
    visited = []
    failure = ValueError("child failed")

    @node
    async def child(ctx: Context, /) -> int:
        ctx.effect(lambda: lambda: visited.append("cleanup"))
        visited.append("child")
        raise failure

    @node
    def parent(ctx: Context, /, *, values: Any) -> Any:
        visited.append("parent")
        return values

    @wrapper
    def middleware(
        ctx: Context, wrapped: Node, call_next: Callable, /, *, values: Any
    ) -> Any:
        visited.append("wrapper")
        return values

    values = [schema.resolve("first"), schema.resolve("second"), child()]
    if target == "node":
        call = parent(values=Auto(values))
    elif target == "wrapper":
        call = parent(values=Auto(None)).add_wrappers(middleware(values=Auto(values)))
    else:

        def call(ctx):
            return eval_tree(ctx, values)

    expected = {
        "node": NodeExceptionRecord,
        "wrapper": WrapperExceptionRecord,
        "eval_tree": BaseExceptionGroup,
    }[target]
    with pytest.raises(expected) as caught:
        await await_result(call(ctx))
    error = caught.value if target == "eval_tree" else caught.value.__cause__
    assert isinstance(error, BaseExceptionGroup)
    assert len(error.exceptions) == 2
    ref_errors, node_errors = error.exceptions
    assert isinstance(ref_errors, BaseExceptionGroup)
    assert len(ref_errors.exceptions) == 2
    assert all(isinstance(error, ContextPathError) for error in ref_errors.exceptions)
    assert isinstance(node_errors, BaseExceptionGroup)
    assert len(node_errors.exceptions) == 1
    child_error = node_errors.exceptions[0]
    assert isinstance(child_error, NodeExceptionRecord)
    assert child_error.__cause__ is failure
    assert visited == ["child", "cleanup"]
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_auto_reports_cleanup_failures_with_node_exception_context(
    asynchronous: bool,
) -> None:
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
    initial_owned = tuple(ctx._lifecycle._owned)
    with pytest.raises(BaseExceptionGroup) as caught:
        await await_result(
            node_evaluator(ctx, [failing(index=0), failing(index=1), successful()])
        )
    assert len(caught.value.exceptions) == 2
    for index, failure in enumerate(failures):
        cleanup_error = caught.value.exceptions[index]
        assert isinstance(cleanup_error, BaseExceptionGroup)
        assert cleanup_error.exceptions == (cleanup_failures[index],)
        node_error = cleanup_error.__context__
        assert isinstance(node_error, NodeExceptionRecord)
        assert node_error.__cause__ is failure
    assert caught.value.__cause__ is None
    for index in range(3):
        assert events.count(("run", index)) == 1
        assert events.count(("cleanup", index)) == 1
    if not asynchronous:
        assert events == [
            (event, index) for index in range(3) for event in ("run", "cleanup")
        ]
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


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
    initial_owned = tuple(ctx._lifecycle._owned)
    with pytest.raises(BaseExceptionGroup) as caught:
        node_evaluator(ctx, [first(), second()])
    cleanup_error = caught.value.exceptions[0]
    assert isinstance(cleanup_error, BaseExceptionGroup)
    assert cleanup_error.exceptions == (failure,)
    assert len(caught.value.exceptions) == 1
    assert caught.value.__cause__ is None
    assert events == ["cleanup", "second"]
    assert tuple(ctx._lifecycle._owned) == initial_owned


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
    initial_owned = tuple(ctx._lifecycle._owned)
    pending = node_evaluator(ctx, [failing(), asynchronous()])
    assert inspect.isawaitable(pending)
    assert visited == ["sync"]
    with pytest.raises(BaseExceptionGroup):
        await pending
    assert visited == ["sync", "async"]
    assert tuple(ctx._lifecycle._owned) == initial_owned


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
    initial_owned = tuple(ctx._lifecycle._owned)
    result = await await_result(node_evaluator(ctx, [asynchronous(), synchronous()]))
    assert result[0] is first and result[1] is second
    assert tuple(ctx._lifecycle._owned) == initial_owned


@pytest.mark.parametrize("fails", [False, True])
async def test_auto_disposes_child_before_siblings_finish(fails: bool) -> None:
    cleaned = asyncio.Event()
    release = asyncio.Event()
    failure = ValueError("child failed")

    @node
    async def child(ctx: Context, /) -> int:
        ctx.effect(lambda: cleaned.set)
        if fails:
            raise failure
        return 1

    @node
    async def waiting(ctx: Context, /) -> int:
        await release.wait()
        return 2

    ctx = Context()
    initial_owned = tuple(ctx._lifecycle._owned)
    task = asyncio.create_task(await_result(node_evaluator(ctx, [child(), waiting()])))
    await cleaned.wait()
    assert not task.done()
    release.set()
    if fails:
        with pytest.raises(BaseExceptionGroup) as caught:
            await task
        node_error = caught.value.exceptions[0]
        assert isinstance(node_error, NodeExceptionRecord)
        assert node_error.__cause__ is failure
        assert len(caught.value.exceptions) == 1
    else:
        assert await task == [1, 2]
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


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
    initial_owned = tuple(ctx._lifecycle._owned)
    task = asyncio.create_task(
        await_result(node_evaluator(ctx, [cancelled(), sibling()]))
    )
    await started.wait()
    operation.cancel()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not task.done()
    release.set()
    with pytest.raises(BaseExceptionGroup) as caught:
        await task
    assert isinstance(caught.value.exceptions[0], asyncio.CancelledError)
    assert "sibling finished" in events
    assert events.count("cleanup:cancelled") == 1
    assert events.count("cleanup:sibling") == 1
    assert tuple(ctx._lifecycle._owned) == initial_owned


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
    initial_owned = tuple(ctx._lifecycle._owned)
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
    assert tuple(ctx._lifecycle._owned) == initial_owned


@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancelled_auto_leaves_failure_cleanup_owned_by_context(
    cleanup_fails: bool,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    failure = ValueError("node failed")
    cleanup_failure = OSError("cleanup failed")
    events = []
    children: list[Context] = []

    @node
    def child(ctx: Context, /) -> int:
        children.append(ctx)

        async def cleanup() -> None:
            events.append("cleanup started")
            started.set()
            await release.wait()
            events.append("cleanup finished")
            if cleanup_fails:
                raise cleanup_failure

        ctx.effect(lambda: cleanup)
        raise failure

    ctx = Context()
    initial_owned = tuple(ctx._lifecycle._owned)
    task = asyncio.create_task(await_result(node_evaluator(ctx, [child()])))
    await started.wait()
    try:
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert events == ["cleanup started"]
        assert children[0]._lifecycle in ctx._lifecycle._owned
    finally:
        release.set()
        if cleanup_fails:
            with pytest.raises(BaseExceptionGroup) as caught:
                await children[0].adispose()
            assert caught.value.exceptions == (cleanup_failure,)
        else:
            await children[0].adispose()
    assert events == ["cleanup started", "cleanup finished"]
    assert tuple(ctx._lifecycle._owned) == initial_owned
    ctx.dispose()


def test_auto_subclasses_require_explicit_evaluator_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CustomRef(Ref[int]):
        pass

    class CustomNode(Node[int]):
        pass

    ctx = Context()
    evaluators = ctx.get(EVALUATORS_REF)
    defaults = evaluators.resolve(ctx.scope)
    ctx.declare(Schema({"value": Schema.leaf()}))
    ctx.update({"value": 7})
    custom_ref = CustomRef("value")
    custom_node = CustomNode(func=lambda ctx: 3, params={})

    @node
    def collect(ctx: Context, /, *, values: list[Any]) -> list[Any]:
        return values

    graph = collect(values=Auto([Ref("value"), custom_ref, custom_node]))
    result = graph(ctx)
    assert result[0] == 7
    assert result[1] is custom_ref and result[2] is custom_node
    remove = ctx.effect(
        lambda: evaluators.register(
            ctx.scope, {CustomRef: defaults[Ref], CustomNode: defaults[Node]}
        )
    )
    assert graph(ctx) == [7, 7, 3]
    remove()
    result = graph(ctx)
    assert result[1] is custom_ref and result[2] is custom_node
    ctx.dispose()


def test_integer_evaluator_does_not_evaluate_booleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ctx = Context()
    ctx.effect(
        lambda: ctx.get(EVALUATORS_REF).register(
            ctx.scope, {int: lambda _ctx, values: [value + 1 for value in values]}
        )
    )
    result = eval_tree(ctx, [1, True])
    assert result[0] == 2 and result[1] is True
    ctx.dispose()


@pytest.mark.parametrize("target", ["node", "wrapper", "eval_tree"])
@pytest.mark.parametrize("dynamic", [False, True])
async def test_auto_reconstructs_containers_and_preserves_ordinary_leaves(
    target: str, dynamic: bool
) -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"value": 7})
    marker = object()
    payload = {
        "plain": [{"marker": marker}, [], {}],
        "tuple": ([1, 2],),
        "value": schema.resolve("value") if dynamic else 7,
    }

    @node
    def identity(ctx: Context, /, *, value: Any) -> Any:
        return value

    @wrapper
    def capture(
        ctx: Context, wrapped: Node, call_next: Callable, /, *, value: Any
    ) -> Any:
        return value

    if target == "node":
        call = identity(value=Auto(payload))
    elif target == "wrapper":
        call = identity(value=Auto(None)).add_wrappers(capture(value=Auto(payload)))
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
async def test_auto_and_non_auto_parameters_have_binding_driven_identity(
    asynchronous: bool,
) -> None:
    @node
    def identity(ctx: Context, /, *, evaluated: Any, raw: Any) -> Any:
        return evaluated, raw

    @node
    async def async_identity(ctx: Context, /, *, evaluated: Any, raw: Any) -> Any:
        return evaluated, raw

    ctx = Context()
    marker = object()
    payload = [marker]
    factory = async_identity if asynchronous else identity
    evaluated, raw = await factory(evaluated=Auto(payload), raw=payload).acall(ctx)
    assert evaluated is not payload
    assert evaluated[0] is marker
    assert raw is payload
    evaluated, raw = await factory(evaluated=Auto(marker), raw=marker).acall(ctx)
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

    def flatten(box):
        nonlocal calls
        calls += 1
        element.set("other", 2)
        return iter((box.value,)), TreeAux()

    rules = TreeRules(
        {Box: TreeHandler(flatten, lambda children, aux: Box(next(iter(children))))}
    )

    @node
    def identity(ctx: Context, /, *, value: Any, other: int) -> Any:
        return value, other

    @wrapper
    def capture(
        ctx: Context,
        wrapped: Node,
        call_next: Callable,
        /,
        *,
        value: Any,
        other: int,
    ) -> Any:
        return value, other

    marker = object()
    box = Box(marker)
    if target == "node":
        element = graph = identity(value=Auto(box), other=1)
    else:
        element = capture(value=Auto(box), other=1)
        graph = identity(value=Auto(None), other=0).add_wrappers(element)
    ctx = Context()
    ctx.effect(lambda: ctx.get(DATA_TREE_REF).register(ctx.scope, rules))
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
    def read(ctx: Context, /, *, values: list[int]) -> list[int]:
        return values

    @wrapper
    def repeat(ctx: Context, wrapped: Node, call_next: Callable, /) -> Any:
        wrapped.set("values", Auto([42]))
        first = call_next(ctx)
        payload.append(schema.resolve("value"))
        ctx.set("value", 3)
        return first, call_next(ctx)

    ctx = Context()
    ctx.declare(schema)
    ctx.update({"value": 2})
    graph = read(values=Auto(payload)).add_wrappers(repeat())
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

    rules = TreeRules({Box: TreeHandler(flatten, lambda children, aux: Box())})

    @node
    def identity(ctx: Context, /, *, value: Any) -> Any:
        return value

    @wrapper
    def stop(ctx: Context, wrapped: Node, call_next: Callable, /) -> int:
        return 12

    ctx = Context()
    ctx.effect(lambda: ctx.get(DATA_TREE_REF).register(ctx.scope, rules))
    assert identity(value=Auto(Box())).add_wrappers(stop())(ctx) == 12
    ctx.dispose()
