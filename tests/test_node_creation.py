from __future__ import annotations

import asyncio
from types import MappingProxyType

import pytest

from slyme.context import Context, Metadata, Schema
from slyme.execution import await_result, continuation
from slyme.node import Node, Wrapper, auto, node, wrapper


@pytest.mark.parametrize("kind", ["node", "wrapper"])
@pytest.mark.parametrize("parentheses", [False, True])
def test_decorators_return_objects_and_partial_creates_independent_bindings(
    kind: str, parentheses: bool
) -> None:
    def function(*runtime, **kwargs):
        """Collect explicit bindings."""
        return kwargs

    decorator = node if kind == "node" else wrapper
    base = (decorator() if parentheses else decorator)(function)
    assert isinstance(base, Node if kind == "node" else Wrapper)
    assert base.func is function
    assert base.func.__name__ == function.__name__
    assert base.func.__doc__ == function.__doc__

    shared = []
    first = base.partial(value=shared, func=1, params=2, wrappers=3)
    second = base.partial(value=shared)
    updated = first.partial(extra=4).partial(func=5)
    assert first.params == {"value": shared, "func": 1, "params": 2, "wrappers": 3}
    assert updated.params == {**first.params, "extra": 4, "func": 5}
    assert second.params == {"value": shared}
    assert updated.get("value") is second.get("value") is shared
    assert first.func is second.func is function
    assert base.params == {}
    assert base.partial() is not base
    with pytest.raises(TypeError):
        first.params["value"] = 1


@pytest.mark.parametrize("kind", ["node", "wrapper"])
def test_direct_creation_copies_bindings_without_evaluating_them(kind: str) -> None:
    calls = []

    def source(ctx):
        calls.append("source")
        return 7

    def function(*runtime, **kwargs):
        return kwargs

    create = node if kind == "node" else wrapper
    shared = []
    params = {"value": auto(node(source)), "shared": shared}
    instance = create(function, params=MappingProxyType(params))
    params["value"] = 99
    params["extra"] = 1
    assert calls == []
    assert instance.get("shared") is shared
    assert "extra" not in instance.params

    ctx = Context()
    runtime = (ctx,) if kind == "node" else (ctx, node(source), source)
    assert instance(*runtime) == {"value": 7, "shared": shared}
    assert calls == ["source"]
    assert create(function)(*runtime) == {}
    instance = instance.partial(local=True)
    assert "local" not in params
    ctx.dispose()


def test_assembly_wrappers_are_separate_from_business_bindings() -> None:
    events = []

    @node
    def collect(ctx, /, **kwargs):
        return kwargs

    def around(ctx, wrapped, call_next, /, *, label):
        events.append(label)
        return call_next(ctx)

    outer = wrapper(around, params={"label": "outer"})
    inner = wrapper(around, params={"label": "inner"})
    wrappers = [outer]
    graph = node(collect.func, params={"params": 1, "wrappers": 2})
    graph = graph.replace(wrappers=(*wrappers,))
    copied = graph.partial()
    wrappers.clear()
    graph = graph.replace(wrappers=(*graph.wrappers, inner))
    assert copied.wrappers == (outer,)
    assert copied.wrappers is not graph.wrappers
    other = collect.partial(wrappers=3)
    assert other.wrappers == ()

    ctx = Context()
    assert graph(ctx) == {"params": 1, "wrappers": 2}
    assert events == ["outer", "inner"]
    assert other(ctx) == {"wrappers": 3}
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_decorated_generators_compose_as_nodes_and_wrappers(
    asynchronous: bool,
) -> None:
    events = []

    async def pending(value):
        await asyncio.sleep(0)
        return value

    source = node(lambda ctx, *, value: pending(value) if asynchronous else value)

    @node
    @continuation()
    def add(ctx, /, *, child):
        value = yield child(ctx)
        return value + 1

    @wrapper()
    @continuation
    def around(ctx, wrapped, call_next, /):
        events.append("before")
        try:
            return (yield call_next(ctx))
        finally:
            events.append("after")

    source = source.partial(value=4)
    graph = add.partial(child=source).replace(wrappers=(around.partial(),))
    ctx = Context()
    assert await await_result(graph(ctx)) == 5
    assert await await_result(graph(ctx)) == 5
    assert events == ["before", "after"] * 2
    await await_result(ctx.dispose())


@pytest.mark.parametrize("decorate, cls", [(node, Node), (wrapper, Wrapper)])
def test_configured_decorator_snapshots_params_and_partial_defers_auto(decorate, cls):
    calls = []

    @node
    def source(ctx):
        calls.append(ctx.get("value"))
        return calls[-1]

    payload = []
    supplied = {"value": auto(source), "payload": payload}
    configured = decorate(params=supplied)
    supplied.clear()

    @configured
    def target(*runtime, value, payload):
        return value, payload

    first = target.partial()
    second = first.partial(value=10).partial(value=auto(source))
    assert isinstance(target, cls)
    assert first is not target and second is not first
    assert first.params == target.params == second.params
    assert first.get("payload") is payload
    assert calls == []
    for value in (1, 2):
        ctx = Context()
        try:
            ctx.declare({"value": Schema.leaf()})
            ctx.set("value", value)
            runtime = (ctx,) if cls is Node else (ctx, source, source)
            assert second(*runtime) == (value, payload)
            assert target(*runtime, value=9) == (9, payload)
        finally:
            ctx.dispose()
    assert calls == [1, 2]


@pytest.mark.parametrize("decorate", [node, wrapper])
def test_partial_copies_metadata_and_supports_attribute_names_as_bindings(decorate):
    marker = Metadata()
    original = decorate(lambda *args, **kwargs: kwargs, metadata={"tag": marker})
    derived = original.partial(self=1, params=2, metadata=3, func=4)
    assert original.params == {}
    assert derived.params == {"self": 1, "params": 2, "metadata": 3, "func": 4}
    assert derived.metadata is not original.metadata
    assert derived.metadata["tag"] is marker
    cleared = derived.replace(metadata=None)
    assert cleared.metadata == {}
    assert derived.metadata == {"tag": marker}
    assert original.metadata == {"tag": marker}
