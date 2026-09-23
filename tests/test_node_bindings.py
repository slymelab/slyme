from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from slyme.context import Context, Schema
from slyme.context.default import NODE_TREE_REF
from slyme.node import Auto, Node, node, wrapper
from slyme.node.exception import NodeExceptionRecord, WrapperExceptionRecord
from slyme.utils.execution import await_result
from slyme.utils.tree import TreeEngine


@pytest.mark.parametrize("kind", ["node", "wrapper"])
def test_bindings_are_explicit_and_defaults_belong_to_the_function(kind: str) -> None:
    default = []

    def function(*runtime, value=default, **kwargs):
        return value, kwargs

    factory = node(function) if kind == "node" else wrapper(function)
    instance = factory()
    ctx = Context()
    runtime = (
        (ctx,) if kind == "node" else (ctx, node(lambda ctx: None)(), lambda ctx: None)
    )
    params = instance.params
    assert params == {}
    assert instance(*runtime) == (default, {})
    assert instance(*runtime)[0] is default
    assert instance.func is function
    with pytest.raises(KeyError):
        instance.get("value")
    assert instance.get("value", None) is None
    assert instance.params == {}

    instance.set("value", None)
    instance.set("extra", 3)
    assert params == {"value": None, "extra": 3}
    assert instance(*runtime) == (None, {"extra": 3})
    assert instance.delete("value") is None
    assert instance(*runtime)[0] is default
    assert params == {"extra": 3}
    assert instance.delete("value") is None
    assert params == {"extra": 3}
    assert instance(*runtime)[0] is default
    instance.delete("extra")
    assert params == {}
    assert instance.delete("never_bound") is None
    assert params == {}
    ctx.dispose()


@pytest.mark.parametrize("kind", ["node", "wrapper"])
def test_get_uses_explicit_defaults_without_changing_bindings(kind: str) -> None:
    def function(*runtime, **kwargs):
        return kwargs

    factory = node(function) if kind == "node" else wrapper(function)
    instance = factory(value=None, count=0, enabled=False)
    fallback = []

    assert instance.get("missing", fallback) is fallback
    assert instance.get("missing", default=None) is None
    assert instance.get("missing", 0) == 0
    assert instance.get("missing", False) is False
    assert instance.get("value", fallback) is None
    assert instance.get("count", fallback) == 0
    assert instance.get("enabled", fallback) is False
    assert instance.params == {"value": None, "count": 0, "enabled": False}
    with pytest.raises(KeyError) as missing:
        instance.get("missing")
    assert missing.value.args == ("missing",)


@pytest.mark.parametrize("kind", ["node", "wrapper"])
def test_call_overrides_bindings_before_auto_evaluation(kind: str) -> None:
    calls = []

    @node
    def child(ctx, /):
        calls.append("child")
        return 3

    def function(*runtime, value=1, **kwargs):
        return value, kwargs

    factory = node(function) if kind == "node" else wrapper(function)
    binding = Auto(child())
    original = {"left": 1}
    instance = factory(value=binding, options=original)
    ctx = Context()
    ctx.declare(Schema({"value": Schema.leaf()}))
    initial_effects = tuple(ctx._lifecycle._effects)
    ctx.update({"value": 4})
    runtime = (ctx,) if kind == "node" else (ctx, child(), lambda ctx: None)
    replacement = {"right": 2}
    assert instance(*runtime, value=9, options=replacement) == (
        9,
        {"options": replacement},
    )
    assert calls == []
    assert instance(*runtime, value=Auto(ctx.resolve("value")))[0] == 4
    assert calls == []
    assert instance(*runtime, value=None)[0] is None
    assert instance(*runtime)[0] == 3
    assert calls == ["child"]
    assert instance.params == {"value": binding, "options": original}
    assert instance.get("options") is original
    assert tuple(ctx._lifecycle._effects) == initial_effects
    ctx.dispose()


def test_native_parameter_errors_are_reported_at_invocation() -> None:
    calls = []

    @node
    def child(ctx, /):
        calls.append("child")
        return 1

    @node
    def required(ctx, /, *, value):
        return value

    instance = required()
    ctx = Context()
    initial_effects = tuple(ctx._lifecycle._effects)
    with pytest.raises(NodeExceptionRecord) as missing:
        instance(ctx)
    assert isinstance(missing.value.__cause__, TypeError)
    assert "value" in str(missing.value.__cause__)

    instance.set("unknown", 1)
    with pytest.raises(NodeExceptionRecord) as unexpected:
        instance(ctx, value=Auto(child()))
    assert isinstance(unexpected.value.__cause__, TypeError)
    assert "unknown" in str(unexpected.value.__cause__)
    assert calls == ["child"]
    assert tuple(ctx._lifecycle._effects) == initial_effects
    instance.delete("unknown")
    assert instance(ctx, value=2) == 2

    @wrapper
    def middleware(ctx, wrapped, call_next, /, *, required):
        return call_next(ctx)

    wrapped = required(value=1).add_wrappers(middleware())
    with pytest.raises(WrapperExceptionRecord) as missing_wrapper:
        wrapped(ctx)
    assert isinstance(missing_wrapper.value.__cause__, TypeError)
    ctx.dispose()


def test_variadic_functions_receive_positional_framework_arguments() -> None:
    ctx = Context()
    keywords = {"self": 1, "ctx": 2, "wrapped": 3, "call_next": 4}

    @node()
    def collect(*args, **kwargs):
        return args, kwargs

    instance = collect(**keywords)
    assert instance(ctx) == ((ctx,), keywords)
    assert instance(ctx, ctx=5) == ((ctx,), {**keywords, "ctx": 5})

    @node
    def collect_tail(ctx, /, *args, **kwargs):
        return args, kwargs

    assert collect_tail(**keywords)(ctx) == ((), keywords)

    @wrapper()
    def middleware(*args, **kwargs):
        return args, kwargs

    wrapped = collect()
    next_call = node(lambda ctx: None)()
    effect = middleware(**keywords)
    assert effect(ctx, wrapped, next_call, wrapped=5) == (
        (ctx, wrapped, next_call),
        {**keywords, "wrapped": 5},
    )
    runtime, actual = wrapped.add_wrappers(effect)(ctx)
    assert runtime[0] is ctx and runtime[1] is wrapped
    assert actual == keywords
    assert runtime[2](ctx) == ((ctx,), {})
    ctx.dispose()


def test_auto_is_a_binding_not_a_function_default_or_a_nested_implicit_marker() -> None:
    calls = []

    @node
    def child(ctx, /):
        calls.append("child")
        return 1

    default = Auto(child())

    @node
    def identity(ctx, /, value=default):
        return value

    ctx = Context()
    assert identity()(ctx) is default
    nested = [default]
    assert identity(value=nested)(ctx) is nested
    assert not calls
    assert identity(value=default)(ctx) == 1
    assert calls == ["child"]
    ctx.dispose()


def test_auto_can_produce_a_node_without_executing_its_result() -> None:
    @node
    def handler(ctx, /):
        raise AssertionError("Handler is data here")

    callback = handler()

    @node
    def select(ctx, /):
        return callback

    @node
    def register(ctx, /, *, handler: Node):
        return handler

    ctx = Context()
    assert register(handler=callback)(ctx) is callback
    assert register(handler=Auto(select()))(ctx) is callback
    ctx.dispose()


async def test_async_calls_snapshot_overrides_without_changing_saved_bindings() -> None:
    @node
    async def value(ctx, /, value=1):
        await asyncio.sleep(0)
        return value

    @wrapper
    async def pause(ctx: Context, wrapped: Node, call_next: Callable, /) -> Any:
        await asyncio.sleep(0)
        return await call_next(ctx)

    instance = value(value=2).add_wrappers(pause())
    ctx = Context()
    first = await_result(instance(ctx))
    second = await_result(instance(ctx, value=3))
    instance.set("value", 4)
    assert await asyncio.gather(first, second) == [2, 3]
    assert await await_result(instance(ctx)) == 4
    await await_result(ctx.dispose())


def test_inspection_follows_explicit_bindings_and_auto_trees() -> None:
    @node
    def value(ctx, /, default=42, **kwargs):
        return kwargs

    schema = Schema({"input": Schema.leaf()})
    ref = schema.resolve("input")
    instance = value(dynamic=Auto({"value": ref}), extra="raw")
    ctx = Context()
    rules = ctx.get(NODE_TREE_REF).resolve(ctx.scope)
    paths_and_leaves = list(TreeEngine.iter_with_key_path(instance, rules=rules))
    assert [leaf for _, leaf in paths_and_leaves] == [ref, "raw"]
    assert [TreeEngine.get_element(instance, path) for path, _ in paths_and_leaves] == [
        ref,
        "raw",
    ]
    instance.delete("extra")
    assert list(TreeEngine.iter(instance, rules=rules)) == [ref]
    ctx.dispose()
