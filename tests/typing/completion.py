"""Static checks for the awaited result type of unified execution APIs."""

from collections.abc import Awaitable, Callable, Generator
from typing import Any, Literal

from typing_extensions import assert_type

from slyme.context import Context
from slyme.node import Auto, Node, Wrapper, create_node, create_wrapper, node, wrapper
from slyme.utils.execution import await_result, continuation


def direct(*runtime: Any, **kwargs: Any) -> int:
    return 1


async def direct_async(*runtime: Any, **kwargs: Any) -> int:
    return 1


def direct_mixed(*runtime: Any, **kwargs: Any) -> int | Awaitable[int]:
    return 1


@node
@continuation
def composed(ctx: Context, /) -> Generator[Any, Any, int]:
    yield direct_async(ctx)
    return 1


@wrapper()
@continuation()
def composed_wrapper(
    ctx: Context, wrapped: Node, call_next: Callable, /
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
    ctx: Context, wrapped: Node, call_next: Callable, /
) -> int | Awaitable[int]:
    return call_next(ctx)


async def check_types(ctx: Context) -> None:
    assert_type(ctx.dispose_mode, Literal["sequential", "batch"])
    assert_type(Context(parent=ctx, dispose_mode="batch"), Context)
    assert_type(ctx.fork(dispose_mode="batch"), Context)
    assert_type(ctx.derive(dispose_mode="batch"), Context)
    assert_type(create_node(direct), Node[int])
    assert_type(create_node(direct_async), Node[int])
    assert_type(create_node(direct_mixed, {"value": 1}), Node[int])
    assert_type(create_node(direct, wrappers=[composed_wrapper()]), Node[int])
    assert_type(create_wrapper(direct), Wrapper[int])
    assert_type(create_wrapper(direct_async), Wrapper[int])
    assert_type(create_wrapper(direct_mixed), Wrapper[int])
    assert_type(composed(), Node[int])
    assert_type(composed_wrapper(), Wrapper[int])
    assert_type(immediate(), Node[int])
    assert_type(asynchronous(), Node[int])
    assert_type(mixed(), Node[int])
    assert_type(mixed_wrapper(), Wrapper[int])
    assert_type(parameterized(), Node[int])
    assert_type(parameterized(value=Auto(mixed())), Node[int])
    assert_type(parameterized()(ctx, value=1), int | Awaitable[int])
    assert_type(await await_result(parameterized()(ctx, value=1)), int)
    assert_type(decorated_async(value=1), Node[int])
    assert_type(
        Wrapper.compose([mixed_wrapper()], wrapped=immediate(), call_next=immediate()),
        Callable[[Context], Any],
    )
    assert_type(immediate()(ctx), int | Awaitable[int])
    assert_type(await await_result(immediate()(ctx)), int)
    assert_type(await await_result(asynchronous()(ctx)), int)
    assert_type(await await_result(mixed()(ctx)), int)

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
    assert_type(await await_result(ctx.dispose()), None)
