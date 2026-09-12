from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from slyme.context import Context, Schema
from slyme.node import Auto, Node, eval_tree, node, wrapper
from slyme.utils.awaitable import resolve
from slyme.utils.pytree import PyTreeAux, PyTreeEngine


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

    first = await resolve(call(ctx))
    second = await resolve(call(ctx))
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
    engine = PyTreeEngine("test_auto", register_defaults=True)

    def flatten(box):
        nonlocal calls
        calls += 1
        element.set("other", 2)
        return iter((box.value,)), PyTreeAux()

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

    engine = PyTreeEngine("test_auto", register_defaults=True)
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
