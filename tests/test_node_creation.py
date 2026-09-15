from __future__ import annotations

import asyncio
from inspect import isfunction
from types import MappingProxyType

import pytest

from slyme.context import Context
from slyme.node import Auto, create_node, create_wrapper, node, wrapper
from slyme.utils.continuation import continuation


@pytest.mark.parametrize("kind", ["node", "wrapper"])
@pytest.mark.parametrize("parentheses", [False, True])
def test_factories_preserve_function_metadata_and_create_independent_bindings(
    kind: str, parentheses: bool
) -> None:
    def function(*runtime, **kwargs):
        """Collect explicit bindings."""
        return kwargs

    decorator = node if kind == "node" else wrapper
    factory = (decorator() if parentheses else decorator)(function)
    assert isfunction(factory)
    assert factory.__wrapped__ is function
    assert factory.__name__ == function.__name__
    assert factory.__doc__ == function.__doc__

    shared = []
    first = factory(value=shared, func=1, params=2, wrappers=3)
    second = factory(value=shared)
    first.set("extra", 4)
    first.delete("value")
    assert first.params == {"func": 1, "params": 2, "wrappers": 3, "extra": 4}
    assert second.params == {"value": shared}
    assert second.get("value") is shared
    assert first.func is second.func is function


@pytest.mark.parametrize("kind", ["node", "wrapper"])
def test_direct_creation_copies_bindings_without_evaluating_them(kind: str) -> None:
    calls = []

    def source(ctx):
        calls.append("source")
        return 7

    def function(*runtime, **kwargs):
        return kwargs

    create = create_node if kind == "node" else create_wrapper
    shared = []
    params = {"value": Auto(create_node(source)), "shared": shared}
    instance = create(function, MappingProxyType(params))
    params["value"] = 99
    params["extra"] = 1
    assert calls == []
    assert instance.get("shared") is shared
    assert "extra" not in instance.params

    ctx = Context()
    runtime = (ctx,) if kind == "node" else (ctx, create_node(source), source)
    assert instance(*runtime) == {"value": 7, "shared": shared}
    assert calls == ["source"]
    assert create(function)(*runtime) == {}
    instance.set("local", True)
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

    outer = create_wrapper(around, {"label": "outer"})
    inner = create_wrapper(around, {"label": "inner"})
    wrappers = [outer]
    graph = create_node(
        collect.__wrapped__, {"params": 1, "wrappers": 2}, wrappers=iter(wrappers)
    )
    wrappers.clear()
    graph.add_wrappers(inner)
    inner.set("label", "updated")
    other = collect(wrappers=3)
    assert other.wrappers == []

    ctx = Context()
    assert graph(ctx) == {"params": 1, "wrappers": 2}
    assert events == ["outer", "updated"]
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

    source = create_node(
        lambda ctx, *, value: pending(value) if asynchronous else value
    )

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

    source.set("value", 4)
    graph = add(child=source).add_wrappers(around())
    ctx = Context()
    assert await graph.acall(ctx) == 5
    assert await graph.acall(ctx) == 5
    assert events == ["before", "after"] * 2
    await ctx.adispose()
