"""Static checks for the awaited result type of unified execution APIs."""

from collections.abc import Awaitable, Callable

from typing_extensions import assert_type

from slyme.context import Context
from slyme.node import Node, Wrapper, node, wrapper
from slyme.utils.awaitable import resolve


@node
def immediate(ctx: Context, /) -> int:
    return 1


@node
async def asynchronous(ctx: Context, /) -> int:
    return 1


@node
def mixed(ctx: Context, /) -> int | Awaitable[int]:
    return 1


@wrapper
def mixed_wrapper(
    ctx: Context, wrapped: Node, call_next: Callable, /
) -> int | Awaitable[int]:
    return call_next(ctx)


async def check_types(ctx: Context) -> None:
    assert_type(immediate(), Node[int])
    assert_type(asynchronous(), Node[int])
    assert_type(mixed(), Node[int])
    assert_type(mixed_wrapper(), Wrapper[int])
    assert_type(immediate()(ctx), int | Awaitable[int])
    assert_type(await resolve(immediate()(ctx)), int)
    assert_type(await resolve(asynchronous()(ctx)), int)
    assert_type(await resolve(mixed()(ctx)), int)

    def setup() -> Callable[[], None]:
        return lambda: None

    async def async_setup() -> Callable[[], None]:
        return lambda: None

    assert_type(ctx.effect(setup), Callable[[], None])
    assert_type(
        await resolve(ctx.effect(async_setup)),
        Callable[[], None | Awaitable[None]],
    )
    assert_type(ctx.dispose(), None | Awaitable[None])
