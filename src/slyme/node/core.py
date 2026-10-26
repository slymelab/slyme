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

"""Keyword partials with explicit Auto evaluation and Wrapper composition."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import (
    Any,
    Concatenate,
    Generic,
    ParamSpec,
    Protocol,
    TypeAlias,
    TypeVar,
    overload,
)

from slyme.context import Context, Metadata
from slyme.execution import Continuation, continuation

from .exception import (
    NodeException,
    NodeExceptionRecord,
    WrapperExceptionRecord,
)

__all__ = [
    "auto",
    "node",
    "wrapper",
    "NodeElement",
    "Node",
    "AnyNode",
    "Wrapper",
    "AnyWrapper",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_S = TypeVar("_S", covariant=True)
_V = TypeVar("_V")

_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(frozen=True, slots=True)
class Auto(Generic[_S]):
    """Evaluate a bound parameter tree before invoking its Node or Wrapper.

    Ordinary parameters pass through unchanged. Containers inside value are
    reconstructed; evaluator results are not recursively evaluated.
    """

    value: _S


def auto(value: _V) -> Auto[_V]:
    """Mark a value tree for evaluation when its Node or Wrapper executes."""
    return Auto(value)


class NodeElement(Generic[_P, _R]):
    """A function with read-only keyword bindings and auxiliary metadata.

    Mappings are shallow snapshots; referenced values retain their identities.
    """

    __slots__ = ("_func", "_params", "_metadata")

    @overload
    def __init__(
        self,
        *,
        func: Callable[_P, Awaitable[_R]],
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        *,
        func: Callable[_P, _R | Awaitable[_R]],
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    def __init__(
        self,
        *,
        func: Callable[_P, _R | Awaitable[_R]],
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None:
        self._func: Callable[..., _R | Awaitable[_R]] = func
        self._params: Mapping[str, Any] = MappingProxyType(
            {} if params is None else dict(params)
        )
        self._metadata: Mapping[str, Metadata] = MappingProxyType(
            {} if metadata is None else dict(metadata)
        )

    @property
    def func(self) -> Callable[_P, _R | Awaitable[_R]]:
        return self._func

    @property
    def params(self) -> Mapping[str, Any]:
        """Return a read-only view of explicitly bound parameters."""
        return self._params

    @property
    def metadata(self) -> Mapping[str, Metadata]:
        """Return the read-only snapshot of auxiliary metadata."""
        return self._metadata

    def get(self, name: str, default: Any = _MISSING) -> Any:
        """Return a binding or default; absent bindings otherwise raise KeyError."""
        try:
            return self._params[name]
        except KeyError:
            if default is _MISSING:
                raise
            return default

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


class Node(NodeElement[Concatenate[Context, _P], _R], Generic[_P, _R]):
    """A keyword partial with Auto evaluation and Wrapper composition.

    Calls shallowly override saved bindings without modifying them. Python
    applies function defaults and argument validation at invocation time.
    """

    __slots__ = ("_wrappers",)

    @overload
    def __init__(
        self,
        /,
        *,
        func: Callable[Concatenate[Context, _P], Awaitable[_R]],
        wrappers: Iterable[AnyWrapper[_R]] | None = None,
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        /,
        *,
        func: Callable[Concatenate[Context, _P], _R | Awaitable[_R]],
        wrappers: Iterable[AnyWrapper[_R]] | None = None,
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    def __init__(
        self,
        /,
        *,
        func: Callable[Concatenate[Context, _P], _R | Awaitable[_R]],
        wrappers: Iterable[AnyWrapper[_R]] | None = None,
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None:
        super().__init__(func=func, params=params, metadata=metadata)
        self._wrappers: tuple[AnyWrapper[_R], ...] = (
            () if wrappers is None else tuple(wrappers)
        )

    @property
    def wrappers(self) -> tuple[AnyWrapper[_R], ...]:
        """Return the read-only, outermost-first wrapper sequence."""
        return self._wrappers

    def partial(self, /, **params: Any) -> Node[_P, _R]:
        """Return a new Node with shallowly merged bindings, without evaluation."""
        return self.replace(params={**self._params, **params})

    def replace(
        self,
        *,
        params: Mapping[str, Any] | None | _Missing = _MISSING,
        wrappers: Iterable[AnyWrapper[_R]] | None | _Missing = _MISSING,
        metadata: Mapping[str, Metadata] | None | _Missing = _MISSING,
    ) -> Node[_P, _R]:
        """Return a new Node, replacing supplied configuration fields in full.

        Omitted fields are retained; None clears a field. Values and Wrapper
        objects keep their identities. The callback is unchanged.
        """
        return Node(
            func=self.func,
            params=self.params if params is _MISSING else params,
            wrappers=self.wrappers if wrappers is _MISSING else wrappers,
            metadata=self.metadata if metadata is _MISSING else metadata,
        )

    @continuation
    def __call__(self, ctx: Context, /, **kwargs: Any) -> Generator[Any, Any, _R]:
        """Snapshot bindings and wrappers; evaluate Auto when call_next delegates."""
        try:
            func = self._func
            wrappers = self.wrappers
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


class Wrapper(
    NodeElement[
        Concatenate[
            Context, Node[..., _R], Callable[[Context], _R | Awaitable[_R]], _P
        ],
        _R,
    ],
    Generic[_P, _R],
):
    """Preserve a Node's result type; await completion before result-dependent work."""

    __slots__ = ()

    @overload
    def __init__(
        self,
        /,
        *,
        func: Callable[
            Concatenate[
                Context, AnyNode[_R], Callable[[Context], _R | Awaitable[_R]], _P
            ],
            Awaitable[_R],
        ],
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        /,
        *,
        func: Callable[
            Concatenate[
                Context, AnyNode[_R], Callable[[Context], _R | Awaitable[_R]], _P
            ],
            _R | Awaitable[_R],
        ],
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    def __init__(
        self,
        /,
        *,
        func: Callable[
            Concatenate[
                Context, AnyNode[_R], Callable[[Context], _R | Awaitable[_R]], _P
            ],
            _R | Awaitable[_R],
        ],
        params: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None:
        super().__init__(func=func, params=params, metadata=metadata)

    def partial(self, /, **params: Any) -> Wrapper[_P, _R]:
        """Return a new Wrapper with shallowly merged bindings, without evaluation."""
        return self.replace(params={**self._params, **params})

    def replace(
        self,
        *,
        params: Mapping[str, Any] | None | _Missing = _MISSING,
        metadata: Mapping[str, Metadata] | None | _Missing = _MISSING,
    ) -> Wrapper[_P, _R]:
        """Return a new Wrapper; omitted fields stay, None clears a field."""
        return Wrapper(
            func=self.func,
            params=self.params if params is _MISSING else params,
            metadata=self.metadata if metadata is _MISSING else metadata,
        )

    @staticmethod
    def compose(
        wrappers: Iterable[AnyWrapper[_R]],
        *,
        wrapped: AnyNode[_R],
        call_next: Callable[[Context], _R | Awaitable[_R]],
    ) -> Callable[[Context], _R | Awaitable[_R]]:
        """Assemble outermost-first wrappers without invoking them.

        Snapshot wrapper order; each invocation evaluates the wrappers' Auto
        bindings. Wrappers control whether and how often to call the next
        layer and its Context. Results preserve the Node's type and may be awaitable.
        """
        for wrapper_obj in reversed(tuple(wrappers)):
            invoke = lambda ctx, current=wrapper_obj, next_call=call_next: current(  # noqa: E731
                ctx, wrapped, next_call
            )
            call_next = invoke
        return call_next

    @continuation
    def __call__(
        self,
        ctx: Context,
        wrapped: AnyNode[_R],
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


# Erase the business signature while retaining the completion type.
AnyNode: TypeAlias = Node[..., _R]
AnyWrapper: TypeAlias = Wrapper[..., _R]


class _NodeDecorator(Protocol):
    @overload
    def __call__(
        self, func: Callable[Concatenate[Context, _P], Awaitable[_R]], /
    ) -> Node[_P, _R]: ...
    @overload
    def __call__(
        self, func: Callable[Concatenate[Context, _P], _R | Awaitable[_R]], /
    ) -> Node[_P, _R]: ...


class _WrapperDecorator(Protocol):
    @overload
    def __call__(
        self,
        func: Callable[
            Concatenate[
                Context, AnyNode[_R], Callable[[Context], _R | Awaitable[_R]], _P
            ],
            Awaitable[_R],
        ],
        /,
    ) -> Wrapper[_P, _R]: ...
    @overload
    def __call__(
        self,
        func: Callable[
            Concatenate[
                Context, AnyNode[_R], Callable[[Context], _R | Awaitable[_R]], _P
            ],
            _R | Awaitable[_R],
        ],
        /,
    ) -> Wrapper[_P, _R]: ...


@overload
def node(
    func: Callable[Concatenate[Context, _P], Awaitable[_R]],
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Node[_P, _R]: ...
@overload
def node(
    func: Callable[Concatenate[Context, _P], _R | Awaitable[_R]],
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Node[_P, _R]: ...
@overload
def node(
    func: None = None,
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> _NodeDecorator: ...
def node(
    func: Callable[..., Any] | None = None,
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Any:
    """Decorate a function as a directly callable Node.

    Snapshot params and metadata when configuring the decorator. Use partial()
    to derive independent bindings; Auto evaluation happens only on invocation.
    """
    saved_params = None if params is None else dict(params)
    saved_metadata = None if metadata is None else dict(metadata)

    def decorate(target: Callable[..., Any]) -> AnyNode[Any]:
        return Node(func=target, params=saved_params, metadata=saved_metadata)

    return decorate if func is None else decorate(func)


@overload
def wrapper(
    func: Callable[
        Concatenate[Context, AnyNode[_R], Callable[[Context], _R | Awaitable[_R]], _P],
        Awaitable[_R],
    ],
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Wrapper[_P, _R]: ...
@overload
def wrapper(
    func: Callable[
        Concatenate[Context, AnyNode[_R], Callable[[Context], _R | Awaitable[_R]], _P],
        _R | Awaitable[_R],
    ],
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Wrapper[_P, _R]: ...
@overload
def wrapper(
    func: None = None,
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> _WrapperDecorator: ...
def wrapper(
    func: Callable[..., Any] | None = None,
    /,
    *,
    params: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Any:
    """Decorate a function as a directly callable Wrapper with saved bindings."""
    saved_params = None if params is None else dict(params)
    saved_metadata = None if metadata is None else dict(metadata)

    def decorate(target: Callable[..., Any]) -> AnyWrapper[Any]:
        return Wrapper(func=target, params=saved_params, metadata=saved_metadata)

    return decorate if func is None else decorate(func)


from .eval import eval_tree
