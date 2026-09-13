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

"""
Core node module, consolidating base definitions and functional APIs.
"""

import inspect
from collections.abc import Awaitable, Callable, Iterable, Mapping
from enum import Enum
from functools import partial, update_wrapper
from types import MappingProxyType
from typing import (
    Any,
    Concatenate,
    Generic,
    NoReturn,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
    overload,
)

from typing_extensions import Self

from slyme.context import Context
from slyme.utils.continuation import Continuation, await_result
from slyme.utils.exception import enrich_exception

from .exception import (
    NodeException,
    NodeExceptionRecord,
    NodeTerminate,
    WrapperExceptionRecord,
)
from .signature import (
    UNDEFINED,
    UNSET,
    Spec,
    analyze_signature,
)

__all__ = [
    "node",
    "wrapper",
    "NodeElement",
    "Node",
    "Wrapper",
    "NODE_ENGINE",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_E = TypeVar("_E", bound="NodeElement")

NodeFunc = Callable[Concatenate[Context, _P], _R | Awaitable[_R]]
WrapperFunc = Callable[
    Concatenate[Context, "Node[Any]", Callable[[Context], Any], _P], _R | Awaitable[_R]
]
AsyncNodeFunc = Callable[Concatenate[Context, _P], Awaitable[_R]]
AsyncWrapperFunc = Callable[
    Concatenate[
        Context,
        "Node[Any]",
        Callable[[Context], Any | Awaitable[Any]],
        _P,
    ],
    Awaitable[_R],
]
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


# Node Family
class NodeElement:
    """Base class for node-related graph elements."""

    __slots__ = ("_func", "_params", "_specs")

    def __init__(
        self,
        *,
        func: Callable,
        specs: Mapping[str, Spec],
        params: Mapping[str, Any],
    ) -> None:
        self._func = func
        self._specs = specs
        self._validate_inputs(specs, params)
        self._params: dict[str, Any] = {}
        for name in specs:
            self.set(name, params[name] if name in params else UNSET)

    @staticmethod
    def _validate_inputs(
        specs: Mapping[str, Spec],
        params: Mapping[str, Any],
    ) -> None:
        """Reject parameters that are not declared by the decorated function."""
        allowed_names = set(specs.keys())
        unknown_args = set(params.keys()) - allowed_names
        if unknown_args:
            raise TypeError(
                f"Got unexpected keyword argument(s) {list(unknown_args)}. "
                f"Allowed arguments: {list(allowed_names)}."
            )

    @staticmethod
    def _prepare_eval(
        specs: Mapping[str, Spec],
        params: Mapping[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Split parameters by their declared Auto evaluation flag."""
        raw_params = {}
        eval_params = {}
        for name, value in params.items():
            if specs[name].should_eval():
                eval_params[name] = value
            else:
                raw_params[name] = value
        return raw_params, eval_params

    @staticmethod
    def _validate_ready(params: Mapping[str, Any]) -> None:
        """Reject unresolved required parameters at the call boundary."""
        undefined = [name for name, value in params.items() if value is UNDEFINED]
        if undefined:
            raise ValueError(f"Missing required parameter(s): {undefined}.")

    @property
    def func(self) -> Callable:
        return self._func

    @property
    def specs(self) -> Mapping[str, Spec]:
        return self._specs

    @property
    def params(self) -> Mapping[str, Any]:
        """Return a live, read-only view of build parameters."""
        return MappingProxyType(self._params)

    def _require_param(self, name: str) -> Spec:
        try:
            return self._specs[name]
        except KeyError:
            raise KeyError(
                f"Unknown parameter {name!r} on {type(self).__name__}."
            ) from None

    def get(self, name: str) -> Any:
        """Return one build parameter."""
        self._require_param(name)
        return self._params[name]

    def set(self, name: str, value: Any) -> None:
        """Validate and replace one build parameter."""
        parameter = self._require_param(name)
        try:
            self._params[name] = parameter._build(value)
        except Exception as error:
            enrich_exception(error, f"for parameter '{name}'")
            raise

    def reset(self, name: str) -> None:
        """Restore one build parameter to its declared default."""
        self.set(name, UNSET)


class Node(NodeElement, Generic[_R]):
    """A mutable graph element returning a result or an awaitable completion."""

    __slots__ = ("wrappers",)

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Iterable["Wrapper[Any]"] | None = None,
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, specs=specs, params=params)
        self.wrappers: list[Wrapper[Any]] = list(wrappers or ())

    def add_wrappers(self, *wrappers: "Wrapper[Any]") -> Self:
        self.wrappers.extend(wrappers)
        return self

    def _raise_error(self, error: Exception) -> NoReturn:
        if isinstance(error, NodeTerminate):
            if error.source_node is None:
                error.source_node = self
            raise error
        if isinstance(error, NodeException):
            raise error
        raise NodeExceptionRecord(exception_node=self, exception=error) from error

    def __call__(self, ctx: Context) -> _R | Awaitable[_R]:
        return (
            Continuation.call(partial(self._call, ctx))
            .catch(self._raise_error)
            .unwrap()
        )

    def acall(self, ctx: Context) -> Awaitable[_R]:
        """Call with an always-awaitable result, preserving immediate execution.

        Synchronous work and errors occur during this call. Await the result
        to finish any asynchronous work; this method does not schedule it.
        """
        return await_result(self(ctx))

    def _call(self, ctx: Context) -> _R | Awaitable[_R]:
        wrappers = tuple(self.wrappers)
        kwargs = self._params.copy()
        try:
            self._validate_ready(kwargs)
        except Exception as error:
            enrich_exception(error, f"in call preparation for '{self._func.__name__}'")
            raise
        raw_kwargs, eval_kwargs = self._prepare_eval(self._specs, kwargs)
        if not eval_kwargs:
            chain: Callable[[Context], _R | Awaitable[_R]] = partial(
                self._func, **raw_kwargs
            )
        else:

            def chain(call_ctx: Context) -> _R | Awaitable[_R]:
                return (
                    Continuation.resolve(eval_tree(call_ctx, eval_kwargs))
                    .then(
                        lambda evaluated: self._func(
                            call_ctx, **raw_kwargs, **evaluated
                        )
                    )
                    .unwrap()
                )

        for wrapper_obj in reversed(wrappers):
            chain = partial(wrapper_obj, wrapped=self, call_next=chain)
        return chain(ctx)


class Wrapper(NodeElement, Generic[_R]):
    """Wrap a Node call; await its completion before result-dependent work."""

    __slots__ = ()

    def __init__(
        self,
        /,
        *,
        func: WrapperFunc,
        specs: Mapping[str, Spec],
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, specs=specs, params=params)

    def _raise_error(self, wrapped: Node[Any], error: Exception) -> NoReturn:
        if isinstance(error, NodeException):
            raise error
        raise WrapperExceptionRecord(
            exception_node=self, wrapped_node=wrapped, exception=error
        ) from error

    def __call__(
        self,
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any | Awaitable[Any]],
    ) -> _R | Awaitable[_R]:
        return (
            Continuation.call(partial(self._call, ctx, wrapped, call_next))
            .catch(partial(self._raise_error, wrapped))
            .unwrap()
        )

    def _call(
        self,
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any | Awaitable[Any]],
    ) -> _R | Awaitable[_R]:
        kwargs = self._params.copy()
        try:
            self._validate_ready(kwargs)
        except Exception as error:
            enrich_exception(error, f"in call preparation for '{self._func.__name__}'")
            raise
        raw_kwargs, eval_kwargs = self._prepare_eval(self._specs, kwargs)
        if not eval_kwargs:
            return self._func(ctx, wrapped, call_next, **raw_kwargs)
        return (
            Continuation.resolve(eval_tree(ctx, eval_kwargs))
            .then(
                lambda evaluated: self._func(
                    ctx, wrapped, call_next, **raw_kwargs, **evaluated
                )
            )
            .unwrap()
        )


# Functional Factory & Decorators
class _FactoryBase(Generic[_P, _E]):
    """Construct graph elements from a decorated function's signature."""

    _decorator_name: str
    _runtime_count: int
    _element_type: type[NodeElement]

    @classmethod
    def _decorate(
        cls,
        func: Callable[..., Any],
        /,
        *,
        resolve_type_hints: bool,
    ) -> Self:
        analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
        if len(analysis.runtime_params) != cls._runtime_count:
            raise TypeError(
                f"@{cls._decorator_name} '{func.__name__}' requires exactly "
                f"{cls._runtime_count} runtime "
                f"argument{'s' if cls._runtime_count != 1 else ''}, "
                f"but found {len(analysis.runtime_params)}. "
                "All build arguments must be keyword-only."
            )
        return cls(func, analysis.specs, analysis.public_signature)

    def __init__(
        self,
        func: Callable,
        specs: Mapping[str, Spec],
        signature: inspect.Signature,
    ):
        update_wrapper(self, func)
        self._func = func
        self._specs = specs
        self.__signature__ = signature

    @property
    def element_type(self) -> type[_E]:
        return cast(type[_E], self._element_type)

    @property
    def func(self) -> Callable:
        return self._func

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _E:
        if args:
            raise TypeError("Node and Wrapper factories accept keyword arguments only.")
        try:
            return self.element_type(func=self._func, specs=self._specs, params=kwargs)
        except Exception as error:
            enrich_exception(error, f"for '{self._func.__name__}'")
            raise


class NodeFactory(_FactoryBase[_P, _E]):
    """Build a Node without selecting a synchronous or asynchronous mode."""

    _decorator_name = "node"
    _runtime_count = 1
    _element_type = Node


class WrapperFactory(_FactoryBase[_P, _E]):
    """Build a Wrapper without selecting an execution mode."""

    _decorator_name = "wrapper"
    _runtime_count = 3
    _element_type = Wrapper


class _NodeDecorator(Protocol):
    @overload
    def __call__(self, func: AsyncNodeFunc[_P, _R], /) -> NodeFactory[_P, Node[_R]]: ...
    @overload
    def __call__(self, func: NodeFunc[_P, _R], /) -> NodeFactory[_P, Node[_R]]: ...


class _WrapperDecorator(Protocol):
    @overload
    def __call__(
        self, func: AsyncWrapperFunc[_P, _R], /
    ) -> WrapperFactory[_P, Wrapper[_R]]: ...
    @overload
    def __call__(
        self, func: WrapperFunc[_P, _R], /
    ) -> WrapperFactory[_P, Wrapper[_R]]: ...


@overload
def node(
    func: AsyncNodeFunc[_P, _R],
    /,
    *,
    resolve_type_hints: bool = True,
) -> NodeFactory[_P, Node[_R]]: ...
@overload
def node(
    func: NodeFunc[_P, _R],
    /,
    *,
    resolve_type_hints: bool = True,
) -> NodeFactory[_P, Node[_R]]: ...
@overload
def node(
    func: _Missing = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> _NodeDecorator: ...
def node(
    func: Callable[..., Any] | _Missing = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Any:
    """Create a Node factory; execution follows actual returned values."""
    if func is _MISSING:
        return partial(NodeFactory._decorate, resolve_type_hints=resolve_type_hints)
    return NodeFactory._decorate(func, resolve_type_hints=resolve_type_hints)


@overload
def wrapper(
    func: AsyncWrapperFunc[_P, _R],
    /,
    *,
    resolve_type_hints: bool = True,
) -> WrapperFactory[_P, Wrapper[_R]]: ...
@overload
def wrapper(
    func: WrapperFunc[_P, _R],
    /,
    *,
    resolve_type_hints: bool = True,
) -> WrapperFactory[_P, Wrapper[_R]]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> _WrapperDecorator: ...
def wrapper(
    func: Callable[..., Any] | _Missing = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Any:
    """Create a Wrapper factory for immediate or awaitable execution."""
    if func is _MISSING:
        return partial(WrapperFactory._decorate, resolve_type_hints=resolve_type_hints)
    return WrapperFactory._decorate(func, resolve_type_hints=resolve_type_hints)


from .eval import eval_tree
from .tree import NODE_ENGINE
