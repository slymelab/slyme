"""Native ParamSpec retention without changing keyword partial semantics."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator
from typing import Any, ParamSpec, TypeVar

from typing_extensions import assert_type

from slyme.context import Context, Ref, ref
from slyme.execution import FlatExec, await_result, continuation
from slyme.node import AnyNode, AnyWrapper, Node, Wrapper, auto, node, wrapper

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
    instance = render.partial(text="hello").partial(repeat=2)
    assert_type(instance.func(ctx, text="hello"), str | Awaitable[str])
    instance.func(ctx, text="hello", repeat=2)
    instance.func(ctx, text=1)  # type: ignore[arg-type]
    instance.func(ctx)  # type: ignore[call-arg]
    instance.func(ctx, text="hello", repeats=2)  # type: ignore[call-arg]
    instance.func(ctx, "hello")  # type: ignore[misc]
    # Direct execution requires a Context prefix.
    render("hello")  # type: ignore[arg-type]
    asynchronous(1)  # type: ignore[arg-type]
    # Python cannot transform P into optional keyword bindings or Auto unions.
    # partial() and call overrides intentionally remain dynamic.
    render.partial()
    render.partial("hello")  # type: ignore[call-arg]
    render.partial(text=auto(Ref[str]("text")))
    instance(ctx, text="override")
    instance = instance.partial(text=auto(Ref[str]("text")))
    assert_type(instance(ctx), str | Awaitable[str])
    assert_type(instance.__call__.flat_call(ctx), FlatExec[str])
    explicit: Node[[int], str] = asynchronous
    assert_type(explicit(ctx), str | Awaitable[str])
    assert_type(asynchronous.func(ctx, 1), str | Awaitable[str])
    asynchronous.func(ctx, "wrong")  # type: ignore[arg-type]
    assert_type(flat.func(ctx, count=2), int | Awaitable[int])
    flat.func(ctx, count="wrong")  # type: ignore[arg-type]

    middleware = repeat.partial(times=2).partial(times=3)
    repeat.partial(2)  # type: ignore[call-arg]
    instance = instance.replace(wrappers=(middleware,))
    assert_type(middleware.func(ctx, instance, instance, times=2), str | Awaitable[str])
    middleware.func(ctx, instance, instance, times="wrong")  # type: ignore[arg-type]
    repeat(2)  # type: ignore[call-arg, arg-type]
    instance.replace(wrappers=(middleware,)).func(ctx, text=1)  # type: ignore[arg-type]
    erased: AnyNode[str] = instance
    erased_wrapper: AnyWrapper[str] = middleware
    assert_type(erased(ctx, arbitrary=1), str | Awaitable[str])
    assert_type(erased_wrapper(ctx, erased, erased), str | Awaitable[str])
    collection: list[AnyNode] = [instance, flat]
    assert_type(collection[0](ctx), Any | Awaitable[Any])
    source = auto(Ref[str]("text"))
    assert_type(source.value, Ref[str])
    assert_type(auto(instance).value.func(ctx, text="hello"), str | Awaitable[str])


def preserve(value: Node[P, R]) -> Node[P, R]:
    return value


def check_generic(ctx: Context) -> None:
    preserve(render).func(ctx, text=1)  # type: ignore[arg-type]
    assert_type(preserve(render).func(ctx, text="ok"), str | Awaitable[str])


def invalid_callback() -> int:
    return 1


node(params={})(invalid_callback)  # type: ignore[arg-type]
node(invalid_callback)  # type: ignore[arg-type]
wrapper(params={})(invalid_callback)  # type: ignore[arg-type]
wrapper(invalid_callback)  # type: ignore[arg-type]


async def completed(ctx: Context) -> None:
    assert_type(await await_result(render.partial(text="hello")(ctx)), str)
    assert_type(await await_result(asynchronous.partial(value=1)(ctx)), str)


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
    n = Node(func=async_callback)
    w = Wrapper(func=async_middleware, params=None)
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
wrapper(wrong_wrapper_result)  # type: ignore[arg-type]
wrapper()(wrong_wrapper_result)  # type: ignore[arg-type]
Wrapper(func=wrong_async_wrapper_result, params={})  # type: ignore[misc]
wrapper(wrong_async_wrapper_result)  # type: ignore[misc]
wrapper()(wrong_async_wrapper_result)  # type: ignore[misc]
wrapper(wrong_wrapper_next)  # type: ignore[arg-type, misc]


def forwarding_wrapper(
    ctx: Context,
    wrapped: AnyNode[R],
    call_next: Callable[[Context], R | Awaitable[R]],
    /,
) -> R | Awaitable[R]:
    return call_next(ctx)


# A reusable wrapper can express result preservation with a TypeVar.
def check_forwarding(ctx: Context) -> None:
    text: AnyWrapper[str] = wrapper(forwarding_wrapper)
    integer: AnyWrapper[int] = wrapper(forwarding_wrapper)
    assert_type(text(ctx, render, render), str | Awaitable[str])
    assert_type(integer(ctx, flat, flat), int | Awaitable[int])


def check_factories_and_replacements(ctx: Context) -> None:
    reference: Ref[str] = ref("text")
    assert_type(reference, Ref[str])
    assert_type(ref("untyped"), Ref[Any])
    assert_type(auto(reference).value, Ref[str])
    derived = render.replace(params={"text": auto(reference)}, metadata=None)
    assert_type(derived(ctx), str | Awaitable[str])
    derived.func(ctx, text=1)  # type: ignore[arg-type]
    assert_type(
        repeat.replace(params={"times": 2})(ctx, render, render), str | Awaitable[str]
    )
    derived.params = {}  # type: ignore[misc]
    derived.metadata = {}  # type: ignore[misc]
    derived.func = render.func  # type: ignore[misc]
