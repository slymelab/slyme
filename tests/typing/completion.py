"""Static checks for the awaited result type of unified execution APIs."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Mapping, Sequence
from typing import Any, Literal

from typing_extensions import assert_type

from slyme.context import Context, Metadata, Ref
from slyme.execution import FlatExec, await_result, continuation
from slyme.node import AnyNode, AnyWrapper, Node, Wrapper, auto, node, wrapper
from slyme.node.eval import node_evaluator, ref_evaluator
from slyme.node.exception import NodeExceptionRecord, WrapperExceptionRecord


def direct(*runtime: Any, **kwargs: Any) -> int:
    return 1


async def direct_async(*runtime: Any, **kwargs: Any) -> int:
    return 1


def direct_mixed(*runtime: Any, **kwargs: Any) -> int | Awaitable[int]:
    return 1


def direct_wrapper(
    ctx: Context,
    wrapped: AnyNode[int],
    call_next: Callable[[Context], int | Awaitable[int]],
    /,
    *args: Any,
    **kwargs: Any,
) -> int:
    return 1


async def direct_async_wrapper(
    ctx: Context,
    wrapped: AnyNode[int],
    call_next: Callable[[Context], int | Awaitable[int]],
    /,
    *args: Any,
    **kwargs: Any,
) -> int:
    return 1


def direct_mixed_wrapper(
    ctx: Context,
    wrapped: AnyNode[int],
    call_next: Callable[[Context], int | Awaitable[int]],
    /,
    *args: Any,
    **kwargs: Any,
) -> int | Awaitable[int]:
    return call_next(ctx)


@node
@continuation
def composed(ctx: Context, /) -> Generator[Any, Any, int]:
    yield direct_async(ctx)
    return 1


@wrapper()
@continuation()
def composed_wrapper(
    ctx: Context,
    wrapped: AnyNode[int],
    call_next: Callable[[Context], int | Awaitable[int]],
    /,
) -> Generator[Any, Any, int]:
    yield call_next(ctx)
    return 1


@node
def immediate(ctx: Context, /) -> int:
    return 1


@node
async def asynchronous(ctx: Context, /) -> int:
    return 1


@node
def mixed(ctx: Context, /) -> int | Awaitable[int]:
    return 1


@node()
def parameterized(ctx: Context, /, *, value: int, scale: int = 2) -> int:
    return value * scale


@node()
async def decorated_async(ctx: Context, /, **kwargs: int) -> int:
    return sum(kwargs.values())


@wrapper
def mixed_wrapper(
    ctx: Context,
    wrapped: AnyNode[int],
    call_next: Callable[[Context], int | Awaitable[int]],
    /,
) -> int | Awaitable[int]:
    return call_next(ctx)


async def check_types(ctx: Context) -> None:
    assert_type(ctx.dispose_mode, Literal["sequential", "batch"])
    assert_type(Context(parent=ctx, dispose_mode="batch"), Context)
    assert_type(ctx.fork(dispose_mode="batch"), Context)
    assert_type(ctx.derive(dispose_mode="batch"), Context)
    assert_type(node(direct), AnyNode[int])
    assert_type(node(direct_async), AnyNode[int])
    assert_type(node(direct_mixed, params={"value": 1}), AnyNode[int])
    assert_type(node(direct).replace(wrappers=(composed_wrapper,)), AnyNode[int])
    assert_type(node(direct_async).replace(wrappers=(composed_wrapper,)), AnyNode[int])
    assert_type(node(direct_mixed).replace(wrappers=(composed_wrapper,)), AnyNode[int])
    assert_type(wrapper(direct_wrapper), AnyWrapper[int])
    assert_type(wrapper(direct_async_wrapper), AnyWrapper[int])
    assert_type(wrapper(direct_mixed_wrapper), AnyWrapper[int])
    assert_type(composed, Node[[], int])
    assert_type(composed_wrapper, Wrapper[[], int])
    assert_type(immediate, Node[[], int])
    assert_type(asynchronous, Node[[], int])
    assert_type(mixed, Node[[], int])
    assert_type(mixed_wrapper, Wrapper[[], int])
    assert_type(immediate.func, Callable[[Context], int | Awaitable[int]])
    assert_type(asynchronous.func, Callable[[Context], int | Awaitable[int]])
    assert_type(
        mixed_wrapper.func,
        Callable[
            [Context, AnyNode[int], Callable[[Context], int | Awaitable[int]]],
            int | Awaitable[int],
        ],
    )
    assert_type(
        ref_evaluator(ctx, [Ref[int]("value")]),
        Sequence[Any] | Awaitable[Sequence[Any]],
    )
    assert_type(
        node_evaluator(ctx, [immediate, asynchronous]),
        Sequence[Any] | Awaitable[Sequence[Any]],
    )
    assert_type(NodeExceptionRecord(immediate).exception_node, AnyNode[Any])
    error = WrapperExceptionRecord(mixed_wrapper, immediate)
    assert_type(error.exception_node, AnyWrapper[Any])
    assert_type(error.wrapped_node, AnyNode[Any])
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
    assert_type(
        Wrapper.compose(
            [mixed_wrapper],
            wrapped=immediate,
            call_next=immediate,
        ),
        Callable[[Context], int | Awaitable[int]],
    )
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


def check_wrapper_result_types(
    ctx: Context,
    integer: AnyNode[int],
    text: AnyNode[str],
    mapping: AnyNode[dict[str, int]],
    integer_wrapper: AnyWrapper[int],
    text_wrapper: AnyWrapper[str],
) -> None:
    assert_type(
        node_evaluator(ctx, (integer, text, mapping)),
        Sequence[Any] | Awaitable[Sequence[Any]],
    )
    assert_type(
        node_evaluator(ctx, [integer, text, mapping]),
        Sequence[Any] | Awaitable[Sequence[Any]],
    )
    assert_type(integer.wrappers, tuple[AnyWrapper[int], ...])
    assert_type(integer.replace(wrappers=(integer_wrapper,)), AnyNode[int])
    assert_type(integer_wrapper(ctx, integer, integer), int | Awaitable[int])
    assert_type(
        Wrapper.compose([integer_wrapper], wrapped=integer, call_next=integer),
        Callable[[Context], int | Awaitable[int]],
    )
    integer = integer.replace(wrappers=(text_wrapper,))  # type: ignore[arg-type]
    integer.wrappers.append(text_wrapper)  # type: ignore[attr-defined]
    integer.wrappers = ()  # type: ignore[misc]
    node(direct).replace(wrappers=(text_wrapper,))  # type: ignore[arg-type]
    Node(func=direct, params={}, wrappers=[text_wrapper])  # type: ignore[arg-type]
    Wrapper.compose([text_wrapper], wrapped=integer, call_next=integer)  # type: ignore[misc]
    integer_wrapper(ctx, text, text)  # type: ignore[arg-type]


def metadata_types() -> None:
    markers = {"description": Metadata()}
    for element in (node(direct), wrapper(direct_wrapper)):
        assert_type(element.metadata, Mapping[str, Metadata])
        element.metadata["description"] = markers["description"]  # type: ignore[index]
        assert_type(element.metadata.get("description"), Metadata | None)
        del element.metadata["description"]  # type: ignore[attr-defined]
    assert_type(node(direct, metadata=markers), AnyNode[int])
    assert_type(node(direct_async, metadata=markers), AnyNode[int])
    assert_type(node(params={"value": 1}, metadata=markers)(direct), AnyNode[int])
    assert_type(node(metadata=markers)(direct_async), AnyNode[int])
    assert_type(wrapper(direct_wrapper, metadata=markers), AnyWrapper[int])
    assert_type(wrapper(direct_async_wrapper, metadata=markers), AnyWrapper[int])
    assert_type(
        wrapper(params={"value": 1}, metadata=markers)(direct_wrapper), AnyWrapper[int]
    )
    assert_type(wrapper(metadata=markers)(direct_async_wrapper), AnyWrapper[int])
    assert_type(node(direct_async, metadata=markers), AnyNode[int])
    assert_type(node(direct, metadata=markers), AnyNode[int])
    assert_type(wrapper(direct_async_wrapper, metadata=markers), AnyWrapper[int])
    assert_type(wrapper(direct_wrapper, metadata=markers), AnyWrapper[int])
    assert_type(Node(func=direct, params={}, metadata=markers), AnyNode[int])
    assert_type(
        Wrapper(func=direct_wrapper, params={}, metadata=markers), AnyWrapper[int]
    )
