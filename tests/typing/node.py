"""Native ParamSpec retention without changing mutable keyword partial semantics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator
from typing import Any, ParamSpec, TypeVar

from typing_extensions import assert_type

from slyme.context import Context, Ref
from slyme.execution import FlatExec, await_result, continuation
from slyme.node import (
    AnyNode,
    AnyWrapper,
    Auto,
    Node,
    Wrapper,
    create_node,
    create_wrapper,
    node,
    wrapper,
)

P = ParamSpec("P")
R = TypeVar("R")


@node
def render(ctx: Context, /, *, text: str, repeat: int = 1) -> str:
    return text * repeat


@node()
async def asynchronous(ctx: Context, /, value: int) -> str:
    return str(value)


@node
@continuation
def flat(ctx: Context, /, *, count: int) -> Generator[Any, Any, int]:
    yield count
    return count


@wrapper
def repeat(
    ctx: Context,
    wrapped: AnyNode[str],
    call_next: Callable[[Context], str | Awaitable[str]],
    /,
    *,
    times: int = 1,
) -> str | Awaitable[str]:
    return call_next(ctx)


def check(ctx: Context) -> None:
    instance = render(text="hello")
    assert_type(instance.func(ctx, text="hello"), str | Awaitable[str])
    instance.func(ctx, text="hello", repeat=2)
    instance.func(ctx, text=1)  # type: ignore[arg-type]
    instance.func(ctx)  # type: ignore[call-arg]
    instance.func(ctx, text="hello", repeats=2)  # type: ignore[call-arg]
    instance.func(ctx, "hello")  # type: ignore[misc]
    # Construction remains keyword-only, including the decorated overload.
    render("hello")  # type: ignore[call-arg]
    asynchronous(1)  # type: ignore[call-arg]
    # Python cannot transform P into optional keyword bindings or Auto unions.
    # These existing dynamic entry points are intentionally still permissive.
    render()
    render(text=Auto(Ref[str]("text")))
    instance(ctx, text="override")
    instance.delete("text")
    instance.set("text", Auto(Ref[str]("text")))
    assert_type(instance(ctx), str | Awaitable[str])
    assert_type(instance.__call__.flat_call(ctx), FlatExec[str])
    explicit: Node[[int], str] = asynchronous()
    assert_type(explicit(ctx), str | Awaitable[str])
    assert_type(asynchronous().func(ctx, 1), str | Awaitable[str])
    asynchronous().func(ctx, "wrong")  # type: ignore[arg-type]
    assert_type(flat().func(ctx, count=2), int | Awaitable[int])
    flat().func(ctx, count="wrong")  # type: ignore[arg-type]

    middleware = repeat(times=2)
    instance.add_wrappers(middleware)
    assert_type(middleware.func(ctx, instance, instance, times=2), str | Awaitable[str])
    middleware.func(ctx, instance, instance, times="wrong")  # type: ignore[arg-type]
    repeat(2)  # type: ignore[call-arg]
    instance.add_wrappers(middleware).func(ctx, text=1)  # type: ignore[arg-type]
    erased: AnyNode[str] = instance
    erased_wrapper: AnyWrapper[str] = middleware
    assert_type(erased(ctx, arbitrary=1), str | Awaitable[str])
    assert_type(erased_wrapper(ctx, erased, erased), str | Awaitable[str])
    collection: list[AnyNode] = [instance, flat()]
    assert_type(collection[0](ctx), Any | Awaitable[Any])
    source = Auto(Ref[str]("text"))
    assert_type(source, Auto[Ref[str]])
    assert_type(source.value, Ref[str])
    assert_type(Auto(instance).value.func(ctx, text="hello"), str | Awaitable[str])


def preserve(value: Node[P, R]) -> Node[P, R]:
    return value


def check_generic(ctx: Context) -> None:
    preserve(render()).func(ctx, text=1)  # type: ignore[arg-type]
    assert_type(preserve(render()).func(ctx, text="ok"), str | Awaitable[str])


def invalid_callback() -> int:
    return 1


create_node(invalid_callback)  # type: ignore[arg-type]
node(invalid_callback)  # type: ignore[arg-type]
create_wrapper(invalid_callback)  # type: ignore[arg-type]
wrapper(invalid_callback)  # type: ignore[arg-type]


async def completed(ctx: Context) -> None:
    assert_type(await await_result(render(text="hello")(ctx)), str)
    assert_type(await await_result(asynchronous(value=1)(ctx)), str)


async def async_callback(ctx: Context, /, *, value: int) -> str:
    return str(value)


async def async_middleware(
    ctx: Context,
    wrapped: AnyNode[str],
    call_next: Callable[[Context], str | Awaitable[str]],
    /,
    *,
    value: int,
) -> str:
    return str(value)


def check_constructors(ctx: Context) -> None:
    n = Node(func=async_callback, params={})
    w = Wrapper(func=async_middleware, params={})
    assert_type(n(ctx), str | Awaitable[str])
    assert_type(w(ctx, n, n), str | Awaitable[str])
    assert_type(n.func(ctx, value=1), str | Awaitable[str])
    assert_type(w.func(ctx, n, n, value=1), str | Awaitable[str])
    n.func(ctx, value="wrong")  # type: ignore[arg-type]
    w.func(ctx, n, n, value="wrong")  # type: ignore[arg-type]


def wrong_wrapper_result(
    ctx: Context,
    wrapped: AnyNode[str],
    call_next: Callable[[Context], str | Awaitable[str]],
    /,
) -> int:
    return 1


async def wrong_async_wrapper_result(
    ctx: Context,
    wrapped: AnyNode[str],
    call_next: Callable[[Context], str | Awaitable[str]],
    /,
) -> int:
    return 1


def wrong_wrapper_next(
    ctx: Context,
    wrapped: AnyNode[str],
    call_next: Callable[[Context], int | Awaitable[int]],
    /,
) -> str:
    return "text"


# Each public construction path must check the shared completion contract.
Wrapper(func=wrong_wrapper_result, params={})  # type: ignore[arg-type]
create_wrapper(wrong_wrapper_result)  # type: ignore[arg-type]
wrapper(wrong_wrapper_result)  # type: ignore[arg-type]
wrapper()(wrong_wrapper_result)  # type: ignore[arg-type]
Wrapper(func=wrong_async_wrapper_result, params={})  # type: ignore[misc]
create_wrapper(wrong_async_wrapper_result)  # type: ignore[misc]
wrapper(wrong_async_wrapper_result)  # type: ignore[misc]
wrapper()(wrong_async_wrapper_result)  # type: ignore[misc]
create_wrapper(wrong_wrapper_next)  # type: ignore[arg-type, misc]


def forwarding_wrapper(
    ctx: Context,
    wrapped: AnyNode[R],
    call_next: Callable[[Context], R | Awaitable[R]],
    /,
) -> R | Awaitable[R]:
    return call_next(ctx)


# A reusable wrapper can express result preservation with a TypeVar.
def check_forwarding(ctx: Context) -> None:
    text: AnyWrapper[str] = create_wrapper(forwarding_wrapper)
    integer: AnyWrapper[int] = create_wrapper(forwarding_wrapper)
    assert_type(text(ctx, render(), render()), str | Awaitable[str])
    assert_type(integer(ctx, flat(), flat()), int | Awaitable[int])
