"""Static checks for the awaited result type of unified execution APIs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from typing import Any, Literal

from typing_extensions import assert_type

from slyme.context import Context, Metadata, Ref
from slyme.execution import FlatExec, await_result, continuation
from slyme.node import AnyNode, Node, auto, node, wrap
from slyme.node.eval import node_evaluator, ref_evaluator


def direct(*runtime: Any, **kwargs: Any) -> int:
    return 1


async def direct_async(*runtime: Any, **kwargs: Any) -> int:
    return 1


def direct_mixed(*runtime: Any, **kwargs: Any) -> int | Awaitable[int]:
    return 1


def direct_node(
    ctx: Context,
    /,
    *args: Any,
    wrapped: AnyNode[int],
    **kwargs: Any,
) -> int:
    return 1


async def direct_async_node(
    ctx: Context,
    /,
    *args: Any,
    wrapped: AnyNode[int],
    **kwargs: Any,
) -> int:
    return 1


def direct_mixed_node(
    ctx: Context,
    /,
    *args: Any,
    wrapped: AnyNode[int],
    **kwargs: Any,
) -> int | Awaitable[int]:
    return wrapped(ctx)


@node(inject_ctx=True)
@continuation
def composed(ctx: Context, /) -> Generator[Any, Any, int]:
    yield direct_async(ctx)
    return 1


@node(inject_ctx=True)
@continuation()
def composed_node(
    ctx: Context,
    /,
    *,
    wrapped: AnyNode[int],
) -> Generator[Any, Any, int]:
    yield wrapped(ctx)
    return 1


@node(inject_ctx=True)
def immediate(ctx: Context, /) -> int:
    return 1


@node(inject_ctx=True)
async def asynchronous(ctx: Context, /) -> int:
    return 1


@node(inject_ctx=True)
def mixed(ctx: Context, /) -> int | Awaitable[int]:
    return 1


@node(inject_ctx=True)
def parameterized(ctx: Context, /, *, value: int, scale: int = 2) -> int:
    return value * scale


@node(inject_ctx=True)
async def decorated_async(ctx: Context, /, **kwargs: int) -> int:
    return sum(kwargs.values())


@node(inject_ctx=True)
def mixed_node(
    ctx: Context,
    /,
    *,
    wrapped: AnyNode[int],
) -> int | Awaitable[int]:
    return wrapped(ctx)


async def check_types(ctx: Context) -> None:
    assert_type(ctx.dispose_mode, Literal["sequential", "batch"])
    assert_type(Context(parent=ctx, dispose_mode="batch"), Context)
    assert_type(ctx.fork(dispose_mode="batch"), Context)
    assert_type(ctx.derive(dispose_mode="batch"), Context)
    assert_type(node(direct), AnyNode[int])
    assert_type(node(direct_async), AnyNode[int])
    assert_type(node(direct_mixed, params={"value": 1}), AnyNode[int])
    assert_type(wrap(composed_node, node(direct))(ctx), int | Awaitable[int])
    assert_type(wrap(composed_node, node(direct_async))(ctx), int | Awaitable[int])
    assert_type(wrap(composed_node, node(direct_mixed))(ctx), int | Awaitable[int])
    assert_type(
        node(direct_node, inject_ctx=True)(ctx, wrapped=immediate), int | Awaitable[int]
    )
    assert_type(
        node(direct_async_node, inject_ctx=True)(ctx, wrapped=immediate),
        int | Awaitable[int],
    )
    assert_type(
        node(direct_mixed_node, inject_ctx=True)(ctx, wrapped=immediate),
        int | Awaitable[int],
    )
    assert_type(composed, Node[[Context], int])
    assert_type(composed_node(ctx, wrapped=immediate), int | Awaitable[int])
    assert_type(immediate, Node[[Context], int])
    assert_type(asynchronous, Node[[Context], int])
    assert_type(mixed, Node[[Context], int])
    assert_type(mixed_node(ctx, wrapped=immediate), int | Awaitable[int])
    assert_type(immediate.func, Callable[[Context], int | Awaitable[int]])
    assert_type(asynchronous.func, Callable[[Context], int | Awaitable[int]])
    assert_type(
        ref_evaluator(ctx, [Ref[int]("value")]),
        Sequence[Any] | Awaitable[Sequence[Any]],
    )
    assert_type(
        node_evaluator(ctx, [immediate, asynchronous]),
        Sequence[Any] | Awaitable[Sequence[Any]],
    )
    assert_type(parameterized.func(Context(), value=1), int | Awaitable[int])
    assert_type(
        parameterized.partial(value=auto(mixed))(Context()),
        int | Awaitable[int],
    )
    assert_type(parameterized(ctx, value=1), int | Awaitable[int])
    assert_type(await await_result(parameterized(ctx, value=1)), int)
    assert_type(
        decorated_async.partial(value=1).func(Context(), value=1), int | Awaitable[int]
    )
    assert_type(wrap(mixed_node, immediate)(ctx), int | Awaitable[int])
    assert_type(immediate(ctx), int | Awaitable[int])
    assert_type(await await_result(immediate(ctx)), int)
    assert_type(await await_result(asynchronous(ctx)), int)
    assert_type(await await_result(mixed(ctx)), int)

    def setup() -> Callable[[], None]:
        return lambda: None

    async def async_setup() -> Callable[[], None]:
        return lambda: None

    assert_type(ctx.effect(setup), Callable[[], None])
    assert_type(
        await await_result(ctx.effect(async_setup)),
        Callable[[], None | Awaitable[None]],
    )
    assert_type(ctx.dispose(), None | Awaitable[None])
    assert_type(ctx.dispose.flat_call(), FlatExec[None])
    assert_type(ctx.dispose.flat_start(), FlatExec[None])
    assert_type(Context[object].dispose.flat_call(ctx), FlatExec[None])
    assert_type(immediate.__call__.flat_call(ctx), FlatExec[int])
    assert_type(ref_evaluator.flat_call(ctx, []), FlatExec[Sequence[Any]])
    assert_type(node_evaluator.flat_call(ctx, []), FlatExec[Sequence[Any]])

    assert_type(await await_result(ctx.dispose()), None)


def check_heterogeneous_nodes(
    ctx: Context,
    integer: AnyNode[int],
    text: AnyNode[str],
    mapping: AnyNode[dict[str, int]],
) -> None:
    assert_type(
        node_evaluator(ctx, (integer, text, mapping)),
        Sequence[Any] | Awaitable[Sequence[Any]],
    )
    # The outer Node determines the completion type; layers may transform it.
    assert_type(wrap(text, integer), AnyNode[str])
    assert_type(wrap(integer, text), AnyNode[int])
    assert_type(wrap(integer), AnyNode[int])


def metadata_types() -> None:
    markers = {"description": Metadata()}
    element = node(direct)
    assert_type(element.metadata, Mapping[str, Metadata])
    element.metadata["description"] = markers["description"]  # type: ignore[index]
    assert_type(element.metadata.get("description"), Metadata | None)
    del element.metadata["description"]  # type: ignore[attr-defined]
    assert_type(node(direct, metadata=markers), AnyNode[int])
    assert_type(node(direct_async, metadata=markers), AnyNode[int])
    assert_type(node(params={"value": 1}, metadata=markers)(direct), AnyNode[int])
    assert_type(node(metadata=markers)(direct_async), AnyNode[int])
    assert_type(Node(func=direct, params={}, metadata=markers), AnyNode[int])
