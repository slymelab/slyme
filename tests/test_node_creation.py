from __future__ import annotations

import asyncio
from types import MappingProxyType

import pytest

from slyme.context import Context, Metadata, Schema
from slyme.execution import await_result, continuation
from slyme.node import Node, auto, node, wrap


@pytest.mark.parametrize("parentheses", [False, True])
def test_decorators_return_objects_and_partial_creates_independent_bindings(
    parentheses: bool,
) -> None:

    def function(*runtime, **kwargs):
        """Collect explicit bindings."""
        return kwargs

    decorator = node
    base = (decorator() if parentheses else decorator)(function)
    assert isinstance(base, Node)
    assert base.func is function
    assert base.func.__name__ == function.__name__
    assert base.func.__doc__ == function.__doc__
    shared = []
    first = base.partial(value=shared, func=1, params=2, layers=3)
    second = base.partial(value=shared)
    updated = first.partial(extra=4).partial(func=5)
    assert first.params == {"value": shared, "func": 1, "params": 2, "layers": 3}
    assert updated.params == {**first.params, "extra": 4, "func": 5}
    assert second.params == {"value": shared}
    assert updated.get("value") is second.get("value") is shared
    assert first.func is second.func is function
    assert base.params == {}
    assert base.partial() is not base
    with pytest.raises(TypeError):
        first.params["value"] = 1


def test_direct_creation_copies_bindings_without_evaluating_them() -> None:
    calls = []

    def source(ctx):
        calls.append("source")
        return 7

    def function(*runtime, **kwargs):
        return kwargs

    create = node
    shared = []
    params = {"value": auto(node(source, inject_ctx=True)), "shared": shared}
    instance = create(function, params=MappingProxyType(params))
    params["value"] = 99
    params["extra"] = 1
    assert calls == []
    assert instance.get("shared") is shared
    assert "extra" not in instance.params
    ctx = Context()
    runtime = (ctx,)
    assert instance(*runtime) == {"value": 7, "shared": shared}
    assert calls == ["source"]
    assert create(function)(*runtime) == {}
    instance = instance.partial(local=True)
    assert "local" not in params
    ctx.dispose()


def test_assembly_layers_are_separate_from_business_bindings() -> None:
    events = []

    @node(inject_ctx=True)
    def collect(ctx, /, **kwargs):
        return kwargs

    def around(ctx, /, *, wrapped, label):
        events.append(label)
        return wrapped(ctx)

    outer = node(around, params={"label": "outer"}, inject_ctx=True)
    inner = node(around, params={"label": "inner"}, inject_ctx=True)
    layers = [outer]
    graph = node(collect.func, params={"params": 1, "layers": 2}, inject_ctx=True)
    graph = wrap(*layers, graph)
    copied = graph.partial()
    layers.clear()
    graph = graph.partial(wrapped=wrap(inner, graph.get("wrapped")))
    assert copied.get("wrapped").func is collect.func
    assert copied.get("wrapped") is not graph.get("wrapped")
    other = collect.partial(layers=3)
    assert other.params == {"layers": 3}

    ctx = Context()
    assert graph(ctx) == {"params": 1, "layers": 2}
    assert events == ["outer", "inner"]
    assert other(ctx) == {"layers": 3}
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_decorated_generators_compose_as_nodes_and_layers(
    asynchronous: bool,
) -> None:
    events = []

    async def pending(value):
        await asyncio.sleep(0)
        return value

    source = node(
        lambda ctx, *, value: pending(value) if asynchronous else value, inject_ctx=True
    )

    @node(inject_ctx=True)
    @continuation()
    def add(ctx, /, *, child):
        value = yield child(ctx)
        return value + 1

    @node(inject_ctx=True)
    @continuation
    def around(ctx, /, *, wrapped):
        events.append("before")
        try:
            return (yield wrapped(ctx))
        finally:
            events.append("after")

    source = source.partial(value=4)
    graph = wrap(around.partial(), add.partial(child=source))
    ctx = Context()
    assert await await_result(graph(ctx)) == 5
    assert await await_result(graph(ctx)) == 5
    assert events == ["before", "after"] * 2
    await await_result(ctx.dispose())


def test_decorating_snapshots_params_and_partial_defers_auto():
    calls = []

    @node(inject_ctx=True)
    def source(ctx):
        calls.append(ctx.get("value"))
        return calls[-1]

    payload = []
    supplied = {"value": auto(source), "payload": payload}
    configured = node(params=supplied)

    @configured
    def target(*runtime, value, payload):
        return (value, payload)

    supplied.clear()
    first = target.partial()
    second = first.partial(value=10).partial(value=auto(source))
    assert isinstance(target, Node)
    assert first is not target and second is not first
    assert first.params == target.params == second.params
    assert first.get("payload") is payload
    assert calls == []
    for value in (1, 2):
        ctx = Context()
        try:
            ctx.declare({"value": Schema.leaf()})
            ctx.set("value", value)
            runtime = (ctx,)
            assert second(*runtime) == (value, payload)
            assert target(*runtime, value=9) == (9, payload)
        finally:
            ctx.dispose()
    assert calls == [1, 2]


def test_reused_decorator_snapshots_configuration_at_each_node_creation():
    payload = []
    params = {"value": 1, "payload": payload}
    old_marker, new_marker = Metadata(), Metadata()
    metadata = {"tag": old_marker}
    result_to = {"value": "initial"}
    configured = node(
        params=MappingProxyType(params),
        metadata=MappingProxyType(metadata),
        result_to=result_to,
    )
    params["value"] = 2
    metadata["tag"] = new_marker

    def function(value, payload):
        return value, payload

    first = configured(function)
    params["value"] = 3
    metadata["tag"] = old_marker
    second = configured(function)
    params.clear()
    metadata.clear()
    payload.append("shared")
    result_to["value"] = "changed"

    assert first.get("value") == 2 and second.get("value") == 3
    assert first.metadata["tag"] is new_marker
    assert second.metadata["tag"] is old_marker
    assert first.get("payload") is second.get("payload") is payload
    assert first.get("payload") == ["shared"]
    assert first.result_to is second.result_to is result_to
    assert first.result_to["value"] == "changed"


def test_partial_copies_metadata_and_supports_attribute_names_as_bindings():
    marker = Metadata()
    original = node(lambda *args, **kwargs: kwargs, metadata={"tag": marker})
    derived = original.partial(self=1, params=2, metadata=3, func=4)
    assert original.params == {}
    assert derived.params == {"self": 1, "params": 2, "metadata": 3, "func": 4}
    assert derived.metadata is not original.metadata
    assert derived.metadata["tag"] is marker
    cleared = derived.replace(metadata=None)
    assert cleared.metadata == {}
    assert derived.metadata == {"tag": marker}
    assert original.metadata == {"tag": marker}
