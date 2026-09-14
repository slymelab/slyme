# Copyright 2026 The SlymeLab Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Mutable keyword partials with explicit Auto evaluation and Wrapper composition."""

from collections.abc import Awaitable, Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from functools import partial, update_wrapper
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeVar, cast, overload

from typing_extensions import Self

from slyme.context import Context
from slyme.utils.continuation import await_result, run
from slyme.utils.exception import BatchError

from .exception import (
    NodeException,
    NodeExceptionRecord,
    NodeTerminate,
    WrapperExceptionRecord,
)

__all__ = ["Auto", "node", "wrapper", "NodeElement", "Node", "Wrapper", "NODE_ENGINE"]

_R = TypeVar("_R")
_E = TypeVar("_E", bound="NodeElement")


@dataclass(frozen=True, slots=True)
class Auto:
    """Evaluate a bound parameter tree before invoking its Node or Wrapper.

    Ordinary parameters pass through unchanged. Containers inside value are
    reconstructed; evaluator results are not recursively evaluated.
    """

    value: Any


class NodeElement:
    """A function with mutable, explicitly bound keyword parameters."""

    __slots__ = ("_func", "_params")

    def __init__(self, *, func: Callable, params: Mapping[str, Any]) -> None:
        self._func = func
        self._params: dict[str, Any] = dict(params)

    @staticmethod
    def _prepare_eval(
        params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Separate explicitly wrapped Auto trees from ordinary parameters."""
        raw_params = {}
        eval_params = {}
        for name, value in params.items():
            if isinstance(value, Auto):
                eval_params[name] = value.value
            else:
                raw_params[name] = value
        return raw_params, eval_params

    @property
    def func(self) -> Callable:
        return self._func

    @property
    def params(self) -> Mapping[str, Any]:
        """Return a live, read-only view of explicitly bound parameters."""
        return MappingProxyType(self._params)

    def get(self, name: str) -> Any:
        """Return a bound parameter, raising KeyError when it is absent."""
        return self._params[name]

    def set(self, name: str, value: Any) -> None:
        """Store a parameter without inspecting the function signature."""
        self._params[name] = value

    def delete(self, name: str) -> None:
        """Remove a binding, raising KeyError when it is absent."""
        del self._params[name]


class Node(NodeElement, Generic[_R]):
    """A mutable keyword partial with Auto evaluation and Wrapper composition.

    Calls shallowly override saved bindings without modifying them. Python
    applies function defaults and argument validation at invocation time.
    """

    __slots__ = ("wrappers",)

    def __init__(
        self,
        /,
        *,
        func: Callable[..., _R | Awaitable[_R]],
        wrappers: Iterable["Wrapper[Any]"] | None = None,
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, params=params)
        self.wrappers: list[Wrapper[Any]] = list(wrappers or ())

    def add_wrappers(self, *wrappers: "Wrapper[Any]") -> Self:
        self.wrappers.extend(wrappers)
        return self

    def __call__(self, ctx: Context, /, **kwargs: Any) -> _R | Awaitable[_R]:
        def execute() -> Generator[Any, Any, _R]:
            try:
                return (yield self._call(ctx, kwargs))
            except BaseException as error:
                if not isinstance(error, Exception):
                    raise
                if isinstance(error, NodeTerminate):
                    if error.source_node is None:
                        error.source_node = self
                    raise
                if isinstance(error, (NodeException, BatchError)):
                    raise
                raise NodeExceptionRecord(
                    exception_node=self, exception=error
                ) from error

        return run(execute())

    def acall(self, ctx: Context, /, **kwargs: Any) -> Awaitable[_R]:
        """Call with an always-awaitable result, preserving immediate execution.

        Synchronous work and errors occur during this call. Await the result
        to finish any asynchronous work; this method does not schedule it.
        """
        return await_result(self(ctx, **kwargs))

    def _call(
        self, ctx: Context, overrides: Mapping[str, Any], /
    ) -> _R | Awaitable[_R]:
        wrappers = tuple(self.wrappers)
        kwargs = {**self._params, **overrides}
        raw_kwargs, eval_kwargs = self._prepare_eval(kwargs)
        if not eval_kwargs:
            chain: Callable[[Context], _R | Awaitable[_R]] = partial(
                self._func, **raw_kwargs
            )
        else:

            def chain(call_ctx: Context) -> _R | Awaitable[_R]:
                def execute() -> Generator[Any, Any, _R]:
                    evaluated = yield eval_tree(call_ctx, eval_kwargs)
                    return (yield self._func(call_ctx, **raw_kwargs, **evaluated))

                return run(execute())

        return Wrapper.compose(wrappers, wrapped=self, call_next=chain)(ctx)


class Wrapper(NodeElement, Generic[_R]):
    """Wrap a Node call; await its completion before result-dependent work."""

    __slots__ = ()

    @staticmethod
    def compose(
        wrappers: Iterable["Wrapper[Any]"],
        *,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
    ) -> Callable[[Context], Any]:
        """Assemble outermost-first wrappers without invoking them.

        Snapshot wrapper order; each invocation reads the wrappers' live
        parameters. Wrappers control whether and how often to call the next
        layer, its Context, and its result type, including awaitable results.
        """
        for wrapper_obj in reversed(tuple(wrappers)):
            invoke = lambda ctx, current=wrapper_obj, next_call=call_next: current(  # noqa: E731
                ctx, wrapped, next_call
            )
            call_next = invoke
        return call_next

    def __init__(
        self,
        /,
        *,
        func: Callable[..., _R | Awaitable[_R]],
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, params=params)

    def __call__(
        self,
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any | Awaitable[Any]],
        /,
        **kwargs: Any,
    ) -> _R | Awaitable[_R]:
        def execute() -> Generator[Any, Any, _R]:
            try:
                return (yield self._call(ctx, wrapped, call_next, kwargs))
            except BaseException as error:
                if not isinstance(error, Exception):
                    raise
                if isinstance(error, (NodeException, BatchError)):
                    raise
                raise WrapperExceptionRecord(
                    exception_node=self, wrapped_node=wrapped, exception=error
                ) from error

        return run(execute())

    def _call(
        self,
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any | Awaitable[Any]],
        overrides: Mapping[str, Any],
        /,
    ) -> _R | Awaitable[_R]:
        kwargs = {**self._params, **overrides}
        raw_kwargs, eval_kwargs = self._prepare_eval(kwargs)
        if not eval_kwargs:
            return self._func(ctx, wrapped, call_next, **raw_kwargs)

        def execute() -> Generator[Any, Any, _R]:
            evaluated = yield eval_tree(ctx, eval_kwargs)
            return (
                yield self._func(ctx, wrapped, call_next, **raw_kwargs, **evaluated)
            )

        return run(execute())


class _FactoryBase(Generic[_R, _E]):
    """Construct graph elements from explicit keyword bindings.

    Binding names and values are dynamic: calls may be partial and may supply
    Auto trees instead of the values accepted by the underlying function.
    """

    _element_type: type[NodeElement]

    def __init__(self, func: Callable[..., _R | Awaitable[_R]]):
        update_wrapper(self, func)
        self._func = func

    @property
    def element_type(self) -> type[_E]:
        return cast(type[_E], self._element_type)

    @property
    def func(self) -> Callable[..., _R | Awaitable[_R]]:
        return self._func

    def __call__(self, /, **kwargs: Any) -> _E:
        return self.element_type(func=self._func, params=kwargs)


class NodeFactory(_FactoryBase[_R, Node[_R]]):
    """Build a Node without selecting a synchronous or asynchronous mode."""

    _element_type = Node


class WrapperFactory(_FactoryBase[_R, Wrapper[_R]]):
    """Build a Wrapper without selecting an execution mode."""

    _element_type = Wrapper


class _NodeDecorator(Protocol):
    @overload
    def __call__(self, func: Callable[..., Awaitable[_R]], /) -> NodeFactory[_R]: ...
    @overload
    def __call__(
        self, func: Callable[..., _R | Awaitable[_R]], /
    ) -> NodeFactory[_R]: ...


class _WrapperDecorator(Protocol):
    @overload
    def __call__(self, func: Callable[..., Awaitable[_R]], /) -> WrapperFactory[_R]: ...
    @overload
    def __call__(
        self, func: Callable[..., _R | Awaitable[_R]], /
    ) -> WrapperFactory[_R]: ...


@overload
def node(func: Callable[..., Awaitable[_R]], /) -> NodeFactory[_R]: ...
@overload
def node(func: Callable[..., _R | Awaitable[_R]], /) -> NodeFactory[_R]: ...
@overload
def node(func: None = None, /) -> _NodeDecorator: ...
def node(func: Callable[..., Any] | None = None, /) -> Any:
    """Create a keyword-only Node factory without inspecting its function."""
    return NodeFactory if func is None else NodeFactory(func)


@overload
def wrapper(func: Callable[..., Awaitable[_R]], /) -> WrapperFactory[_R]: ...
@overload
def wrapper(func: Callable[..., _R | Awaitable[_R]], /) -> WrapperFactory[_R]: ...
@overload
def wrapper(func: None = None, /) -> _WrapperDecorator: ...
def wrapper(func: Callable[..., Any] | None = None, /) -> Any:
    """Create a keyword-only Wrapper factory without inspecting its function."""
    return WrapperFactory if func is None else WrapperFactory(func)


from .eval import eval_tree
from .tree import NODE_ENGINE
