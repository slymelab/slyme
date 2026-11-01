from __future__ import annotations

import asyncio

import pytest

from slyme.context import Context, Metadata, Ref, Schema, ref
from slyme.execution import await_result
from slyme.node import auto, node, wrap


def test_replace_distinguishes_omission_empty_and_none():
    marker = Metadata()
    shared = []
    original = node(
        lambda *args, value=10, **kwargs: (value, kwargs),
        params={"value": 1, "shared": shared},
        metadata={"tag": marker},
    )
    copied = original.replace()
    assert copied is not original and copied.func is original.func
    assert copied.params == original.params
    assert copied.metadata == original.metadata
    assert copied.get("shared") is shared
    shared.append(2)
    assert copied.get("shared") == [2]
    replacement = {"value": 3}
    changed = original.replace(params=replacement, metadata={})
    replacement.clear()
    assert changed.params == {"value": 3}
    assert changed.metadata == {}
    assert original.params == {"value": 1, "shared": shared}
    assert original.metadata == {"tag": marker}
    assert original.partial(value=4).get("shared") is shared
    for empty in ({}, None):
        cleared = original.replace(params=empty, metadata=empty)
        assert cleared.params == cleared.metadata == {}
        ctx = Context()
        try:
            runtime = (ctx,)
            assert cleared(*runtime) == (10, {})
        finally:
            ctx.dispose()


def test_layer_replacement_preserves_order_and_previous_graph():
    events = []

    @node(inject_ctx=True)
    def trace(ctx, *, wrapped, label):
        events.append(label)
        return wrapped(ctx)

    @node(inject_ctx=True)
    def task(ctx):
        return 7

    outer = trace.partial(label="outer")
    inner = trace.partial(label="inner")
    supplied = [outer]
    first = wrap(*iter(supplied), task)
    supplied.clear()
    second = first.partial(wrapped=wrap(inner, task))
    assert task.params == {}
    assert first.get("wrapped") is task
    assert second.get("wrapped").get("wrapped") is task
    ctx = Context()
    try:
        assert second(ctx) == first(ctx) == task(ctx) == 7
        assert events == ["outer", "inner", "outer"]
        assert wrap(second) is second
    finally:
        ctx.dispose()


def test_public_configuration_is_read_only():
    element = node(
        lambda *args, **kwargs: kwargs,
        params={"value": 1},
        metadata={"tag": Metadata()},
    )
    assert element.params is element.params
    assert element.metadata is element.metadata
    for field in ("func", "inject_ctx", "params", "result_to", "metadata"):
        with pytest.raises(AttributeError):
            setattr(element, field, getattr(element, field))
    for mapping in (element.params, element.metadata):
        with pytest.raises(TypeError):
            mapping["new"] = None


async def test_replace_and_factories_keep_auto_lazy_across_async_calls():
    value_ref: Ref[int] = ref("value")
    calls = []

    @node(inject_ctx=True)
    async def source(ctx):
        await asyncio.sleep(0)
        calls.append(ctx.get(value_ref))
        return calls[-1]

    @node(inject_ctx=True)
    def read(ctx, *, value):
        return value

    marked = auto(source)
    original = read.partial(value=marked)
    derived = original.replace(params={"value": auto(value_ref)})
    assert marked.value is source
    assert isinstance(value_ref, Ref) and value_ref.parts == ("value",)
    assert not calls
    ctx = Context()
    ctx.declare({"value": Schema.leaf()})
    ctx.set(value_ref, 4)
    try:
        assert derived(ctx) == 4
        assert not calls
        assert await await_result(original(ctx)) == 4
        assert calls == [4]
        assert original.get("value") is marked
    finally:
        await await_result(ctx.dispose())
