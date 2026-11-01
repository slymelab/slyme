from __future__ import annotations

import asyncio

import pytest

from slyme.context import Context, ContextPathError, Identity, Ref, Schema
from slyme.execution import await_result, continuation
from slyme.node import Node, auto, eval_tree, node, wrap


@pytest.fixture
def ctx():
    context = Context()
    try:
        context.declare(
            {name: Schema.leaf() for name in ("source", "result", "a", "b")}
        )
        context.set("source", 2)
        yield context
    finally:
        context.dispose()


def test_native_function_and_context_function_share_execution_interface(ctx):
    def plain(*, value: int) -> dict:
        return {"value": value}

    @node(inject_ctx=True)
    def contextual(ctx, *, field):
        return ctx.get(field)

    task = node(plain).partial(value=auto(Ref("source")))
    assert task.func is plain
    assert task(ctx) == {"value": 2}
    assert contextual(ctx, field=Ref("source")) == 2
    assert task.inject_ctx is False and contextual.inject_ctx is True


@pytest.mark.parametrize("ctx_mode", [False, True])
def test_creation_paths_preserve_result_and_context_mode(ctx, ctx_mode):
    value = {"text": "hello"}

    def plain(*, value):
        return value

    def contextual(context, /, *, value):
        assert context is ctx
        return value

    function = contextual if ctx_mode else plain
    config = {"inject_ctx": ctx_mode, "result_to": "result", "params": {"value": value}}
    for task in (
        Node(func=function, **config),
        node(function, **config),
        node(**config)(function),
    ):
        assert task(ctx) is value and ctx.get("result") is value
        assert task.partial().inject_ctx is ctx_mode
        assert task.replace().result_to == "result"
        assert task.to(None).result_to is None


def test_result_preserves_identity_and_normal_none_values(ctx):
    @node
    def identity(*, value):
        return value

    value = {"items": []}
    task = identity.to(Ref("result"))
    assert task(ctx, value=value) is value
    assert ctx.get("result") is value
    value["items"].append(1)
    assert ctx.get("result")["items"] == [1]
    assert task(ctx, value=None) is None
    assert ctx.get("result") is None
    assert task.to(None)(ctx, value=42) == 42
    assert ctx.get("result") is None


def test_result_projection_uses_selected_paths_not_flattened_leaf_positions(ctx):
    value = {"items": [10, {"payload": {"nested": 20}}], "ignored": 30}
    task = node(lambda: value).to({"items": [Ref("a"), {"payload": Ref("b")}]})
    assert task(ctx) is value
    assert ctx.get("a") == 10
    assert ctx.get("b") is value["items"][1]["payload"]


def test_incompatible_projection_checks_every_selection_before_writing(ctx):
    ctx.set_many({"a": 100, "b": 200})
    task = node(lambda: {"first": 1}).to({"first": Ref("a"), "missing": Ref("b")})
    with pytest.raises(KeyError) as error:
        task(ctx)
    assert isinstance(error.value, KeyError)
    assert ctx.get("a") == 100 and ctx.get("b") == 200


def test_result_does_not_declare_paths(ctx):
    ctx.set("a", 100)
    for destination in (Ref("undeclared"), Ref("")):
        task = node(lambda: [1, 2]).to([Ref("a"), destination])
        with pytest.raises(ContextPathError):
            task(ctx)
        assert ctx.get("a") == 100


def test_mixed_result_registers_with_execution_context_owner(ctx):
    ctx.declare({"service": Schema.leaf(mode="register")})
    closed = []

    class Service:
        def dispose(self):
            closed.append(True)

    service = Service()
    result = {"count": 3, "service": service}
    child = ctx.fork()
    task = node(lambda: result).to({"count": "a", "service": Ref("service")})
    assert task(child) is result
    assert ctx.get("a") == 3 and ctx.get("service") is service
    child.dispose()
    assert ctx.get("a") == 3
    assert not ctx.exists("service")
    assert closed == []  # Binding ownership does not dispose the returned object.


def test_registration_conflicts_and_scope_shadowing_follow_context(ctx):
    ctx.declare({"service": Schema.leaf(mode="register")})
    original, replacement = object(), object()
    remove = ctx.register("service", original)
    task = node(lambda: replacement).to("service")
    with pytest.raises(ContextPathError):
        task(ctx)
    assert ctx.get("service") is original
    child = ctx.derive()
    assert task(child) is replacement
    assert child.get("service") is replacement
    assert ctx.get("service") is original
    child.dispose()
    remove()
    assert task(ctx) is replacement


@pytest.mark.parametrize("duplicate", ["service", Ref("service")])
def test_duplicate_registration_results_fail_before_any_writes(ctx, duplicate):
    ctx.declare({"service": Schema.leaf(mode="register")})
    task = node(lambda: [1, 2, 3]).to(["a", "service", duplicate])
    with pytest.raises(ContextPathError) as caught:
        task(ctx)
    assert isinstance(caught.value, ContextPathError)
    assert "Duplicate registration" in str(caught.value)
    assert not ctx.exists("service") and not ctx.exists("a")


def test_mixed_result_preflights_shape_before_registration(ctx):
    ctx.declare({"service": Schema.leaf(mode="register")})
    task = node(lambda: {"service": object()}).to(
        {"service": "service", "missing": "a"}
    )
    with pytest.raises(KeyError):
        task(ctx)
    assert not ctx.exists("service") and not ctx.exists("a")


def test_registration_conflict_withdraws_batch_and_leaves_assignments_untouched(ctx):
    ctx.declare(
        {"fresh": Schema.leaf(mode="register"), "service": Schema.leaf(mode="register")}
    )
    ctx.register("service", "existing")
    ctx.set("a", "original")
    task = node(lambda: [1, 2, 3]).to(["a", "fresh", "service"])
    with pytest.raises(ContextPathError):
        task(ctx)
    assert ctx.get("service") == "existing"
    assert ctx.get("a") == "original" and not ctx.exists("fresh")


def test_registration_group_is_visible_when_assignments_start(ctx):
    ctx.declare({"service": Schema.leaf(mode="register")})
    observed = []

    class ObserveService:
        def __del__(self):
            observed.append(ctx.get("service"))

    ctx.set("a", ObserveService())
    task = node(lambda: [1, "installed"]).to(["a", "service"])
    assert task(ctx) == [1, "installed"]
    assert observed == ["installed"]


def test_assignment_failure_does_not_roll_back_successful_registration_group(
    ctx, monkeypatch
):
    ctx.declare({"service": Schema.leaf(mode="register")})
    owner = ctx.fork()
    failure = OSError("assignment failed")

    def fail_assignment(self, updates):
        raise failure

    monkeypatch.setattr(Context, "set_many", fail_assignment)
    task = node(lambda: [1, "installed"]).to(["a", "service"])
    with pytest.raises(OSError) as caught:
        task(owner)
    assert caught.value is failure
    assert ctx.get("service") == "installed" and not ctx.exists("a")
    owner.dispose()
    assert not ctx.exists("service")


def test_duplicate_assignment_destinations_keep_last_value(ctx):
    observed = []

    class ObserveAssignment:
        def __del__(self):
            observed.append(ctx.get("a"))

    ctx.set("a", ObserveAssignment())
    result = [1, 2]
    assert node(lambda: result).to(["a", Ref("a")])(ctx) is result
    assert ctx.get("a") == 2
    assert observed == [2]


def test_auto_partial_is_lazy_and_preserves_native_values_and_bindings(ctx):
    calls = []
    child = node(lambda: calls.append("called") or 7)

    @node
    def combine(value, label):
        return value, label

    base = combine.partial(label="original")
    bound = base.auto_partial(value={"source": Ref("source"), "child": child})
    assert calls == [] and dict(base.params) == {"label": "original"}
    assert bound(ctx) == ({"source": 2, "child": 7}, "original")
    ctx.set("source", 4)
    assert bound.auto_partial(label="source")(ctx) == (
        {"source": 4, "child": 7},
        "source",
    )  # Strings remain literal, not implicit Context paths.
    assert calls == ["called", "called"]
    assert bound.func is combine.func


def test_auto_partial_overridden_sources_are_never_evaluated(ctx):
    def fail():
        pytest.fail("overridden source evaluated")

    task = node(lambda value: value).auto_partial(value=node(fail))
    assert task(ctx, value=8) == 8
    assert task.partial(value=9)(ctx) == 9
    assert task.auto_partial(value=Ref("source"))(ctx) == 2


def test_result_overrides_do_not_accumulate_or_mutate_the_original(ctx):
    base = node(lambda: 9)
    first = base.to(Ref("a"))
    second = first.to(Ref("b"))
    assert base.result_to is None
    assert first.result_to == Ref("a")
    second(ctx)
    assert not ctx.exists("a") and ctx.get("b") == 9


def test_bindings_are_independent_and_node_arguments_do_not_auto_execute(ctx):
    calls = []
    child = node(lambda: calls.append("child"))
    parent = node(lambda *, child: child)
    bound = parent.partial(child=child)
    assert bound(ctx) is child and calls == []
    assert parent.params == {}
    assert parent.partial(child=auto(child))(ctx) is None
    assert calls == ["child"]


def test_wrap_is_nested_binding_with_outermost_first_order(ctx):
    events = []

    @node(inject_ctx=True)
    @continuation
    def trace(ctx, *, wrapped, label):
        events.append(label + ":start")
        try:
            return (yield wrapped(ctx))
        finally:
            events.append(label + ":end")

    leaf = node(lambda: 7)
    graph = wrap(trace.partial(label="outer"), trace.partial(label="inner"), leaf)
    assert graph.params["wrapped"].params["wrapped"] is leaf
    assert graph(ctx) == 7
    assert events == ["outer:start", "inner:start", "inner:end", "outer:end"]
    assert "wrapped" not in trace.params


def test_wrap_keeps_inner_and_outer_business_arguments_independent(ctx):
    inner = node(lambda *, value: value).partial(value=1)

    @node(inject_ctx=True)
    def outer(ctx, *, wrapped, value):
        return wrapped(ctx), value

    graph = wrap(outer.partial(value=2), inner)
    assert graph(ctx) == (1, 2)
    assert graph(ctx, value=3) == (1, 3)
    assert graph.partial(value=4)(ctx) == (1, 4)
    changed = graph.partial(wrapped=inner.partial(value=5))
    assert changed(ctx) == (5, 2)
    assert graph(ctx) == (1, 2)


def test_retry_reevaluates_auto_and_publishes_only_outer_success(ctx):
    events = []

    @node
    def attempt(*, value):
        events.append(value)
        if value == 2:
            raise ValueError("retry")
        return value

    @node(inject_ctx=True)
    @continuation
    def retry(ctx, *, wrapped, source):
        try:
            return (yield wrapped(ctx))
        except ValueError:
            assert not ctx.exists("result")
            ctx.set(source, 3)
            return (yield wrapped(ctx))

    graph = wrap(
        retry.partial(source=Ref("source")), attempt.partial(value=auto(Ref("source")))
    ).to(Ref("result"))
    assert graph(ctx) == 3
    assert events == [2, 3] and ctx.get("result") == 3


def test_cache_short_circuit_still_publishes_outer_result(ctx):
    calls = []
    leaf = node(lambda: calls.append("unexpected"))

    @node(inject_ctx=True)
    def cached(ctx, *, wrapped, value):
        return value

    value = {"cached": True}
    graph = wrap(cached.partial(value=value), leaf).to(Ref("result"))
    assert graph(ctx) is value and ctx.get("result") is value
    assert calls == []


def test_child_scope_changes_auto_context_and_cleanup_precedes_outer_result(ctx):
    events = []

    @node(inject_ctx=True)
    @continuation
    def scoped(ctx, *, wrapped, source):
        child = ctx.derive(bindings={source: Identity()})
        child.set(source, 8)
        child.effect(lambda: lambda: events.append("closed"))
        try:
            result = yield wrapped(child)
            assert not ctx.exists("result")
            return result
        finally:
            yield child.dispose()

    leaf = node(lambda *, value: value).partial(value=auto(Ref("source")))
    graph = wrap(scoped.partial(source=Ref("source")), leaf).to(Ref("result"))
    assert graph(ctx) == 8
    assert ctx.get("source") == 2 and ctx.get("result") == 8
    assert events == ["closed"]


def test_auto_node_evaluation_retains_temporary_lifecycle(ctx):
    events = []

    @node(inject_ctx=True)
    def child(ctx):
        ctx.effect(lambda: lambda: events.append("closed"))
        return {"value": 9}

    result = eval_tree(ctx, [child])
    assert result == [{"value": 9}] and events == ["closed"]


@pytest.mark.parametrize("mode", ["assign", "register"])
async def test_async_result_is_published_after_completion(ctx, mode):
    ctx.declare({"async_result": Schema.leaf(mode=mode)})
    started, release = asyncio.Event(), asyncio.Event()

    @node
    async def operation():
        started.set()
        await release.wait()
        return {"value": 3}

    pending = asyncio.create_task(await_result(operation.to(Ref("async_result"))(ctx)))
    try:
        await asyncio.wait_for(started.wait(), 1)
        assert not ctx.exists("async_result")
    finally:
        release.set()
    result = await pending
    assert ctx.get("async_result") is result


async def test_cancel_unwinds_layer_without_publishing(ctx):
    started = asyncio.Event()
    events = []

    @node
    async def work():
        started.set()
        await asyncio.Event().wait()
        return 1

    @node(inject_ctx=True)
    async def cleanup(ctx, *, wrapped):
        try:
            return await await_result(wrapped(ctx))
        finally:
            events.append("closed")

    graph = wrap(cleanup, work).to(Ref("result"))
    pending = asyncio.create_task(await_result(graph(ctx)))
    try:
        await asyncio.wait_for(started.wait(), 1)
    finally:
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
    assert events == ["closed"] and not ctx.exists("result")


async def test_shared_node_keeps_concurrent_bindings_and_results_local(ctx):
    contexts = [ctx.derive(bindings={Ref("result"): Identity()}) for _ in range(2)]
    ready = [asyncio.Event(), asyncio.Event()]

    @node
    async def work(*, index):
        ready[index].set()
        await ready[1 - index].wait()
        return index

    shared = work.to(Ref("result"))
    try:
        results = await asyncio.wait_for(
            asyncio.gather(
                *(
                    await_result(shared(child, index=i))
                    for i, child in enumerate(contexts)
                )
            ),
            1,
        )
        assert results == [0, 1]
        assert [child.get("result") for child in contexts] == [0, 1]
        assert not ctx.exists("result") and shared.params == {}
    finally:
        for child in contexts:
            await await_result(child.dispose())


def test_result_placement_exposes_inner_vs_outer_success(ctx):
    @node(inject_ctx=True)
    @continuation
    def reject(ctx, *, wrapped):
        yield wrapped(ctx)
        raise ValueError("rejected after execution")

    leaf = node(lambda: 3)
    with pytest.raises(ValueError):
        wrap(reject, leaf.to(Ref("a"))).to(Ref("b"))(ctx)
    assert ctx.get("a") == 3 and not ctx.exists("b")


def test_nested_failure_preserves_original_exception(ctx):
    failure = ValueError("business failure")

    @node
    def leaf():
        raise failure

    @node(inject_ctx=True)
    def forwarding(ctx, *, wrapped):
        return wrapped(ctx)

    with pytest.raises(ValueError) as caught:
        wrap(forwarding, leaf)(ctx)
    assert caught.value is failure
