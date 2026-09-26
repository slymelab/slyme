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

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from functools import wraps
from types import MappingProxyType
from typing import Any, Generic, Protocol, TypeVar, overload

from typing_extensions import Self

from slyme.context import Context
from slyme.utils.execution import Continuation, continuation
from slyme.utils.tree import (
    AttributeKey,
    TreeAux,
    TreeHandler,
    TreeKey,
    TreeRules,
)

from .exception import (
    NodeException,
    NodeExceptionRecord,
    WrapperExceptionRecord,
)

__all__ = [
    "Auto",
    "node",
    "wrapper",
    "create_node",
    "create_wrapper",
    "NodeElement",
    "Node",
    "Wrapper",
]

_R = TypeVar("_R")
_MISSING = object()


@dataclass(frozen=True, slots=True)
class Auto:
    """Evaluate a bound parameter tree before invoking its Node or Wrapper.

    Ordinary parameters pass through unchanged. Containers inside value are
    reconstructed; evaluator results are not recursively evaluated.
    """

    value: Any


class NodeElement(Generic[_R]):
    """A function with mutable, explicitly bound keyword parameters."""

    __slots__ = ("_func", "_params")

    def __init__(
        self, *, func: Callable[..., _R | Awaitable[_R]], params: Mapping[str, Any]
    ) -> None:
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
    def func(self) -> Callable[..., _R | Awaitable[_R]]:
        return self._func

    @property
    def params(self) -> Mapping[str, Any]:
        """Return a live, read-only view of explicitly bound parameters."""
        return MappingProxyType(self._params)

    def get(self, name: str, default: Any = _MISSING) -> Any:
        """Return a binding or default; absent bindings otherwise raise KeyError."""
        try:
            return self._params[name]
        except KeyError:
            if default is _MISSING:
                raise
            return default

    def set(self, name: str, value: Any) -> None:
        """Store a parameter without inspecting the function signature."""
        self._params[name] = value

    def delete(self, name: str) -> None:
        """Remove a binding; an absent binding is a no-op."""
        self._params.pop(name, None)


class Node(NodeElement[_R]):
    """A mutable keyword partial with Auto evaluation and Wrapper composition.

    Calls shallowly override saved bindings without modifying them. Python
    applies function defaults and argument validation at invocation time.
    """

    __slots__ = ("wrappers",)

    @staticmethod
    def _flatten(obj: Node[Any]) -> tuple[Iterable[Any], TreeAux]:
        children = [obj.wrappers]
        keys: list[TreeKey] = [AttributeKey("wrappers")]
        for name, value in obj._params.items():
            children.append(value)
            keys.append(_NodeParameterKey(name))
        return tuple(children), TreeAux(children_keys=tuple(keys), cls=Node)

    def __init__(
        self,
        /,
        *,
        func: Callable[..., _R | Awaitable[_R]],
        wrappers: Iterable[Wrapper[_R]] | None = None,
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, params=params)
        self.wrappers: list[Wrapper[_R]] = list(wrappers or ())

    def add_wrappers(self, *wrappers: Wrapper[_R]) -> Self:
        self.wrappers.extend(wrappers)
        return self

    @continuation
    def __call__(self, ctx: Context, /, **kwargs: Any) -> Generator[Any, Any, _R]:
        """Snapshot bindings and wrappers; evaluate Auto when call_next delegates."""
        try:
            func = self._func
            wrappers = tuple(self.wrappers)
            kwargs = {**self._params, **kwargs}
            raw_kwargs, eval_kwargs = self._prepare_eval(kwargs)

            @continuation
            def chain(call_ctx: Context) -> Generator[Any, Any, _R]:
                if eval_kwargs:
                    evaluated = yield eval_tree.flat_call(call_ctx, eval_kwargs)
                else:
                    evaluated = {}
                return (
                    yield func.flat_call(call_ctx, **raw_kwargs, **evaluated)
                    if isinstance(func, Continuation)
                    else func(call_ctx, **raw_kwargs, **evaluated)
                )

            composed = Wrapper.compose(wrappers, wrapped=self, call_next=chain)
            return (
                yield composed.flat_call(ctx)
                if isinstance(composed, Continuation)
                else composed(ctx)
            )
        except NodeException:
            raise
        except Exception as error:
            raise NodeExceptionRecord(exception_node=self) from error


class Wrapper(NodeElement[_R]):
    """Preserve a Node's result type; await completion before result-dependent work."""

    __slots__ = ()

    @staticmethod
    def _flatten(obj: Wrapper[Any]) -> tuple[Iterable[Any], TreeAux]:
        keys = tuple(_NodeParameterKey(name) for name in obj._params)
        return tuple(obj._params.values()), TreeAux(children_keys=keys, cls=Wrapper)

    @staticmethod
    def compose(
        wrappers: Iterable[Wrapper[_R]],
        *,
        wrapped: Node[_R],
        call_next: Callable[[Context], _R | Awaitable[_R]],
    ) -> Callable[[Context], _R | Awaitable[_R]]:
        """Assemble outermost-first wrappers without invoking them.

        Snapshot wrapper order; each invocation reads the wrappers' live
        parameters. Wrappers control whether and how often to call the next
        layer and its Context. Results preserve the Node's type and may be awaitable.
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

    @continuation
    def __call__(
        self,
        ctx: Context,
        wrapped: Node[_R],
        call_next: Callable[[Context], _R | Awaitable[_R]],
        /,
        **kwargs: Any,
    ) -> Generator[Any, Any, _R]:
        """Evaluate wrapper Auto bindings and invoke its function on this Context."""
        try:
            kwargs = {**self._params, **kwargs}
            raw_kwargs, eval_kwargs = self._prepare_eval(kwargs)
            if eval_kwargs:
                raw_kwargs.update((yield eval_tree.flat_call(ctx, eval_kwargs)))
            func = self._func
            return (
                yield func.flat_call(ctx, wrapped, call_next, **raw_kwargs)
                if isinstance(func, Continuation)
                else func(ctx, wrapped, call_next, **raw_kwargs)
            )
        except NodeException:
            raise
        except Exception as error:
            raise WrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped
            ) from error


@overload
def create_node(
    func: Callable[..., Awaitable[_R]],
    /,
    params: Mapping[str, Any] | None = None,
    *,
    wrappers: Iterable[Wrapper[_R]] | None = None,
) -> Node[_R]: ...
@overload
def create_node(
    func: Callable[..., _R | Awaitable[_R]],
    /,
    params: Mapping[str, Any] | None = None,
    *,
    wrappers: Iterable[Wrapper[_R]] | None = None,
) -> Node[_R]: ...
def create_node(
    func: Callable[..., _R | Awaitable[_R]],
    /,
    params: Mapping[str, Any] | None = None,
    *,
    wrappers: Iterable[Wrapper[_R]] | None = None,
) -> Node[_R]:
    """Create a Node with shallow-copied bindings and assembly-time wrappers.

    Parameter values and Wrapper objects retain their identities. Construction
    does not invoke the function or evaluate Auto bindings.
    """
    return Node(func=func, params={} if params is None else params, wrappers=wrappers)


@overload
def create_wrapper(
    func: Callable[..., Awaitable[_R]],
    /,
    params: Mapping[str, Any] | None = None,
) -> Wrapper[_R]: ...
@overload
def create_wrapper(
    func: Callable[..., _R | Awaitable[_R]],
    /,
    params: Mapping[str, Any] | None = None,
) -> Wrapper[_R]: ...
def create_wrapper(
    func: Callable[..., _R | Awaitable[_R]],
    /,
    params: Mapping[str, Any] | None = None,
) -> Wrapper[_R]:
    """Create a Wrapper with shallow-copied bindings without invoking it."""
    return Wrapper(func=func, params={} if params is None else params)


class _NodeDecorator(Protocol):
    @overload
    def __call__(
        self, func: Callable[..., Awaitable[_R]], /
    ) -> Callable[..., Node[_R]]: ...
    @overload
    def __call__(
        self, func: Callable[..., _R | Awaitable[_R]], /
    ) -> Callable[..., Node[_R]]: ...


class _WrapperDecorator(Protocol):
    @overload
    def __call__(
        self, func: Callable[..., Awaitable[_R]], /
    ) -> Callable[..., Wrapper[_R]]: ...
    @overload
    def __call__(
        self, func: Callable[..., _R | Awaitable[_R]], /
    ) -> Callable[..., Wrapper[_R]]: ...


@overload
def node(func: Callable[..., Awaitable[_R]], /) -> Callable[..., Node[_R]]: ...
@overload
def node(func: Callable[..., _R | Awaitable[_R]], /) -> Callable[..., Node[_R]]: ...
@overload
def node(func: None = None, /) -> _NodeDecorator: ...
def node(func: Callable[..., Any] | None = None, /) -> Any:
    """Create a keyword-only Node factory without inspecting its function."""
    if func is None:
        return node

    @wraps(func)
    def factory(**kwargs: Any) -> Node[Any]:
        return create_node(func, kwargs)

    return factory


@overload
def wrapper(func: Callable[..., Awaitable[_R]], /) -> Callable[..., Wrapper[_R]]: ...
@overload
def wrapper(
    func: Callable[..., _R | Awaitable[_R]], /
) -> Callable[..., Wrapper[_R]]: ...
@overload
def wrapper(func: None = None, /) -> _WrapperDecorator: ...
def wrapper(func: Callable[..., Any] | None = None, /) -> Any:
    """Create a keyword-only Wrapper factory without inspecting its function."""
    if func is None:
        return wrapper

    @wraps(func)
    def factory(**kwargs: Any) -> Wrapper[Any]:
        return create_wrapper(func, kwargs)

    return factory


@dataclass(frozen=True)
class _NodeParameterKey(TreeKey):
    """Address one NodeElement build parameter without attribute projection."""

    name: str

    def resolve(self, element: Any) -> Any:
        return element.get(self.name)

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.get({self.name!r})"


# Nodes, Wrappers, and Auto support inspection, not reconstruction.
NODE_RULES = TreeRules(
    handlers={
        Node: TreeHandler(Node._flatten, None),
        Wrapper: TreeHandler(Wrapper._flatten, None),
        Auto: TreeHandler(
            lambda obj: ((obj.value,), TreeAux(children_keys=(AttributeKey("value"),))),
            None,
        ),
    }
)

from .eval import eval_tree
