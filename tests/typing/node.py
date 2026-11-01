"""Preserve original callable signatures in both Context modes."""

from __future__ import annotations

from collections.abc import Awaitable, Generator
from typing import Any, ParamSpec, TypeVar

from typing_extensions import assert_type

from slyme.context import Context, Ref, ref
from slyme.execution import FlatExec, await_result, continuation
from slyme.node import AnyNode, Node, auto, node, wrap

P = ParamSpec("P")
R = TypeVar("R")


@node
def render(*, text: str, repeat: int = 1) -> str:
    return text * repeat


@node(inject_ctx=True)
async def contextual(ctx: Context, /, value: int) -> str:
    return str(value)


@node()
async def asynchronous(value: int) -> str:
    return str(value)


@node(inject_ctx=True)
@continuation
def flat(ctx: Context, /, *, count: int) -> Generator[Any, Any, int]:
    yield count
    return count


@node(inject_ctx=True)
def repeat(
    ctx: Context, /, *, wrapped: AnyNode[str], times: int = 1
) -> str | Awaitable[str]:
    return wrapped(ctx)


def check(ctx: Context) -> None:
    wrap()  # type: ignore[call-arg]
    instance = render.partial(text="hello").partial(repeat=2)
    assert_type(instance.func(text="hello"), str | Awaitable[str])
    instance.func(text=1)  # type: ignore[arg-type]
    instance.func()  # type: ignore[call-arg]
    instance.func(text="hello", repeats=2)  # type: ignore[call-arg]
    instance.func("hello")  # type: ignore[misc]
    render("hello")  # type: ignore[arg-type]
    asynchronous(1)  # type: ignore[arg-type]
    # Binding kwargs and execution overrides remain dynamic; .func is precise.
    render.partial()
    render.partial("hello")  # type: ignore[call-arg]
    instance = instance.partial(text=auto(Ref[str]("text")))
    inferred = instance.auto_partial(text=ref("text"))
    assert_type(inferred.func(text="hello"), str | Awaitable[str])
    assert_type(inferred(ctx), str | Awaitable[str])
    inferred.func(text=1)  # type: ignore[arg-type]
    assert_type(instance(ctx, text="override"), str | Awaitable[str])
    assert_type(instance.__call__.flat_call(ctx), FlatExec[str])
    explicit: Node[[int], str] = asynchronous
    with_context: Node[[Context, int], str] = contextual
    assert_type(explicit(ctx), str | Awaitable[str])
    assert_type(with_context.func(ctx, 1), str | Awaitable[str])
    contextual.func(ctx, "wrong")  # type: ignore[arg-type]
    assert_type(flat.func(ctx, count=2), int | Awaitable[int])
    flat.func(ctx, count="wrong")  # type: ignore[arg-type]

    middleware = repeat.partial(times=2)
    assert_type(middleware.func(ctx, wrapped=instance, times=2), str | Awaitable[str])
    middleware.func(ctx, wrapped=instance, times="wrong")  # type: ignore[arg-type]
    middleware.func(ctx, wrapped=flat)  # type: ignore[arg-type]
    repeat(2)  # type: ignore[arg-type]
    graph = wrap(middleware, instance)
    graph.func(ctx, wrapped=instance, times="wrong")  # type: ignore[arg-type]
    assert_type(graph(ctx), str | Awaitable[str])
    assert_type(wrap(instance).func(text="hello"), str | Awaitable[str])
    erased: AnyNode[str] = instance
    assert_type(erased(ctx, arbitrary=1), str | Awaitable[str])
    collection: list[AnyNode] = [instance, flat]
    assert_type(collection[0](ctx), Any | Awaitable[Any])
    assert_type(auto(Ref[str]("text")).value, Ref[str])
    assert_type(auto(instance).value.func(text="hello"), str | Awaitable[str])


def preserve(value: Node[P, R]) -> Node[P, R]:
    return value


def check_generic(ctx: Context) -> None:
    preserve(render).func(text=1)  # type: ignore[arg-type]
    assert_type(preserve(render).func(text="ok"), str | Awaitable[str])


async def completed(ctx: Context) -> None:
    assert_type(await await_result(render.partial(text="hello")(ctx)), str)
    assert_type(await await_result(asynchronous.partial(value=1)(ctx)), str)


def callback(*, value: int) -> str:
    return str(value)


async def async_callback(ctx: Context, /, *, value: int) -> str:
    return str(value)


def check_constructors(ctx: Context) -> None:
    native = Node(func=callback)
    n = Node(func=async_callback, inject_ctx=True)
    configured = node(inject_ctx=True, params={"value": 1})(async_callback)
    assert_type(n(ctx), str | Awaitable[str])
    assert_type(n.func(ctx, value=1), str | Awaitable[str])
    assert_type(configured.func(ctx, value=1), str | Awaitable[str])
    assert_type(native.func(value=1), str | Awaitable[str])
    n.func(ctx, value="wrong")  # type: ignore[arg-type]
    native.func(value="wrong")  # type: ignore[arg-type]
    configured.func(ctx, value="wrong")  # type: ignore[arg-type]
    node(async_callback, inject_ctx=True).func(ctx, value="wrong")  # type: ignore[arg-type]
    node(params={})(callback).func(value="wrong")  # type: ignore[arg-type]


def forwarding(ctx: Context, /, *, wrapped: AnyNode[R]) -> R | Awaitable[R]:
    return wrapped(ctx)


def identity(*, value: R) -> R:
    return value


# Regression: a higher-order generic callback must remain usable in both modes.
def check_forwarding(ctx: Context) -> None:
    text: AnyNode[str] = node(forwarding, inject_ctx=True)
    integer: AnyNode[int] = node(inject_ctx=True)(forwarding)
    constructed: AnyNode[int] = Node(func=forwarding, inject_ctx=True)
    plain: AnyNode[str] = node(identity)
    assert_type(text.partial(wrapped=render)(ctx), str | Awaitable[str])
    assert_type(integer.partial(wrapped=flat)(ctx), int | Awaitable[int])
    assert_type(constructed(ctx, wrapped=flat), int | Awaitable[int])
    assert_type(plain(ctx, value="hello"), str | Awaitable[str])


def check_factories_and_replacements(ctx: Context) -> None:
    reference: Ref[str] = ref("text")
    assert_type(reference, Ref[str])
    assert_type(ref("untyped"), Ref[Any])
    assert_type(auto(reference).value, Ref[str])
    derived = render.replace(params={"text": auto(reference)}, metadata=None).to(
        reference
    )
    assert_type(derived(ctx), str | Awaitable[str])
    derived.func(text=1)  # type: ignore[arg-type]
    derived.to(None).func(text=1)  # type: ignore[arg-type]
    assert_type(derived.inject_ctx, bool)
    derived.params = {}  # type: ignore[misc]
    derived.metadata = {}  # type: ignore[misc]
    derived.func = render.func  # type: ignore[misc]
    derived.inject_ctx = False  # type: ignore[misc]
    derived.result_to = None  # type: ignore[misc]
