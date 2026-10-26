from __future__ import annotations

import asyncio

import pytest

from slyme.context import Context, Metadata, Ref, Schema, ref
from slyme.execution import await_result
from slyme.node import Node, auto, node, wrapper


@pytest.mark.parametrize("decorate", [node, wrapper])
def test_replace_distinguishes_omission_empty_and_none(decorate):
    marker = Metadata()
    shared = []
    original = decorate(
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
            runtime = (
                (ctx,)
                if isinstance(cleared, Node)
                else (ctx, node(lambda ctx: None), lambda ctx: None)
            )
            assert cleared(*runtime) == (10, {})
        finally:
            ctx.dispose()


def test_wrapper_replacement_preserves_order_and_previous_graph():
    events = []

    @wrapper
    def trace(ctx, wrapped, call_next, *, label):
        events.append(label)
        return call_next(ctx)

    @node
    def task(ctx):
        return 7

    outer = trace.partial(label="outer")
    inner = trace.partial(label="inner")
    supplied = [outer]
    first = task.replace(wrappers=iter(supplied))
    supplied.clear()
    second = first.replace(wrappers=(*first.wrappers, inner))
    assert task.wrappers == ()
    assert first.wrappers == (outer,)
    assert second.partial().wrappers == (outer, inner)
    ctx = Context()
    try:
        assert second(ctx) == first(ctx) == task(ctx) == 7
        assert events == ["outer", "inner", "outer"]
        for empty in (None, ()):
            assert second.replace(wrappers=empty).wrappers == ()
    finally:
        ctx.dispose()


@pytest.mark.parametrize("decorate", [node, wrapper])
def test_public_configuration_is_read_only(decorate):
    element = decorate(
        lambda *args, **kwargs: kwargs,
        params={"value": 1},
        metadata={"tag": Metadata()},
    )
    assert element.params is element.params
    assert element.metadata is element.metadata
    for field in ("func", "params", "metadata") + (
        ("wrappers",) if isinstance(element, Node) else ()
    ):
        with pytest.raises(AttributeError):
            setattr(element, field, getattr(element, field))
    for mapping in (element.params, element.metadata):
        with pytest.raises(TypeError):
            mapping["new"] = None


async def test_replace_and_factories_keep_auto_lazy_across_async_calls():
    value_ref: Ref[int] = ref("value")
    calls = []

    @node
    async def source(ctx):
        await asyncio.sleep(0)
        calls.append(ctx.get(value_ref))
        return calls[-1]

    @node
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
