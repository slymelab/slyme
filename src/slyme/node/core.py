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
    Literal,
    ParamSpec,
    Protocol,
    TypeVar,
    cast,
    overload,
)

from typing_extensions import Self

from slyme.context import Context
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
    "ExecutionMode",
    "node",
    "wrapper",
    "NodeElement",
    "Node",
    "Wrapper",
    "AsyncNode",
    "AsyncWrapper",
    "NODE_ENGINE",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_E = TypeVar("_E", bound="NodeElement")
ExecutionMode = Literal["sync", "async"]

NodeFunc = Callable[Concatenate[Context, _P], _R]
WrapperFunc = Callable[
    Concatenate[Context, "Node[Any]", Callable[[Context], Any], _P], _R
]
AsyncNodeFunc = Callable[Concatenate[Context, _P], Awaitable[_R]]
AsyncWrapperFunc = Callable[
    Concatenate[
        Context,
        "AsyncNode[Any]",
        Callable[[Context], Awaitable[Any]],
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
        """Split parameters into static values and dynamic evaluators."""
        raw_params = {}
        eval_params = {}
        for name, value in params.items():
            if specs[name].should_eval(value):
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

    def _collect_params(self) -> dict[str, Any]:
        """Return a mutable call-time snapshot of this element's parameters."""
        return dict(self.params)

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
        with enrich_exception(f"for parameter '{name}'"):
            self._params[name] = parameter._build(value)

    def reset(self, name: str) -> None:
        """Restore one build parameter to its declared default."""
        self.set(name, UNSET)


class Node(NodeElement, Generic[_R]):
    """Mutable synchronous Node."""

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
        self.wrappers: list[Wrapper[Any]] = []
        if wrappers:
            self.add_wrappers(*wrappers)

    def add_wrappers(self, *wrappers: "Wrapper[Any]") -> Self:
        self.wrappers.extend(wrappers)
        return self

    def __call__(self, ctx: Context) -> _R:
        wrappers = tuple(self.wrappers)
        try:
            kwargs = self._collect_params()
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                self._validate_ready(kwargs)
            raw_kwargs, eval_kwargs = self._prepare_eval(self._specs, kwargs)
            if not eval_kwargs:
                chain: Callable[[Context], _R] = partial(self._func, **raw_kwargs)
            else:
                eval_plan = prepare_eval_plan(eval_kwargs)

                def chain(call_ctx: Context) -> _R:
                    return self._func(
                        call_ctx,
                        **raw_kwargs,
                        **execute_eval_plan(call_ctx, eval_plan),
                    )

            for wrapper_obj in reversed(wrappers):
                chain = partial(wrapper_obj, wrapped=self, call_next=chain)
            return chain(ctx)
        except NodeTerminate as e:
            if e.source_node is None:
                e.source_node = self
            raise
        except NodeException:
            raise
        except Exception as e:
            raise NodeExceptionRecord(exception_node=self, exception=e) from e


class AsyncNode(NodeElement, Generic[_R]):
    """Mutable asynchronous Node."""

    __slots__ = ("wrappers",)

    def __init__(
        self,
        /,
        *,
        func: AsyncNodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Iterable["AsyncWrapper[Any]"] | None = None,
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, specs=specs, params=params)
        self.wrappers: list[AsyncWrapper[Any]] = []
        if wrappers:
            self.add_wrappers(*wrappers)

    def add_wrappers(self, *wrappers: "AsyncWrapper[Any]") -> Self:
        self.wrappers.extend(wrappers)
        return self

    async def __call__(self, ctx: Context) -> _R:
        wrappers = tuple(self.wrappers)
        try:
            kwargs = self._collect_params()
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                self._validate_ready(kwargs)
            raw_kwargs, eval_kwargs = self._prepare_eval(self._specs, kwargs)
            if not eval_kwargs:
                chain: Callable[[Context], Awaitable[_R]] = partial(
                    self._func, **raw_kwargs
                )
            else:
                eval_plan = prepare_eval_plan(eval_kwargs)

                async def chain(call_ctx: Context) -> _R:
                    return await self._func(
                        call_ctx,
                        **raw_kwargs,
                        **await async_execute_eval_plan(call_ctx, eval_plan),
                    )

            for wrapper_obj in reversed(wrappers):
                chain = partial(wrapper_obj, wrapped=self, call_next=chain)
            return await chain(ctx)
        except NodeTerminate as e:
            if e.source_node is None:
                e.source_node = self
            raise
        except NodeException:
            raise
        except Exception as e:
            raise NodeExceptionRecord(exception_node=self, exception=e) from e


class Wrapper(NodeElement, Generic[_R]):
    """Mutable synchronous Wrapper."""

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

    def __call__(
        self,
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
    ) -> _R:
        try:
            kwargs = self._collect_params()
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                self._validate_ready(kwargs)
            raw_kwargs, eval_kwargs = self._prepare_eval(self._specs, kwargs)
            if eval_kwargs:
                raw_kwargs.update(
                    execute_eval_plan(ctx, prepare_eval_plan(eval_kwargs))
                )
            return self._func(ctx, wrapped, call_next, **raw_kwargs)
        except NodeException:
            raise
        except Exception as e:
            raise WrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            ) from e


class AsyncWrapper(NodeElement, Generic[_R]):
    """Mutable asynchronous Wrapper."""

    __slots__ = ()

    def __init__(
        self,
        /,
        *,
        func: AsyncWrapperFunc,
        specs: Mapping[str, Spec],
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, specs=specs, params=params)

    async def __call__(
        self,
        ctx: Context,
        wrapped: AsyncNode[Any],
        call_next: Callable[[Context], Awaitable[Any]],
    ) -> _R:
        try:
            kwargs = self._collect_params()
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                self._validate_ready(kwargs)
            raw_kwargs, eval_kwargs = self._prepare_eval(self._specs, kwargs)
            if eval_kwargs:
                raw_kwargs.update(
                    await async_execute_eval_plan(ctx, prepare_eval_plan(eval_kwargs))
                )
            return await self._func(ctx, wrapped, call_next, **raw_kwargs)
        except NodeException:
            raise
        except Exception as e:
            raise WrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            ) from e


# Functional Factory & Decorators
class _FactoryBase(Generic[_P, _E]):
    """Shared implementation for mode-aware graph element factories."""

    _decorator_name: str
    _runtime_count: int
    _element_types: Mapping[ExecutionMode, type[NodeElement]]

    @staticmethod
    def _is_async_callable(func: Callable[..., Any]) -> bool:
        """Return whether *func* is declared with ``async def``.

        Return annotations are intentionally ignored. A synchronous function that
        returns an Awaitable must opt in with ``mode="async"``.
        """
        unwrapped = inspect.unwrap(func)
        if inspect.iscoroutinefunction(unwrapped):
            return True
        return inspect.iscoroutinefunction(unwrapped.__call__)

    @classmethod
    def _resolve_execution_mode(
        cls,
        func: Callable[..., Any],
        mode: ExecutionMode | None,
    ) -> ExecutionMode:
        detected_async = cls._is_async_callable(func)
        if mode is None:
            return "async" if detected_async else "sync"
        if mode == "sync" and detected_async:
            raise TypeError(
                f"@{cls._decorator_name}(mode='sync') cannot decorate an async "
                "function."
            )
        return mode

    @classmethod
    def _decorate(
        cls,
        func: Callable[..., Any],
        /,
        *,
        mode: ExecutionMode | None,
        resolve_type_hints: bool,
    ) -> Self:
        resolved_mode = cls._resolve_execution_mode(func, mode)
        analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
        if len(analysis.runtime_params) != cls._runtime_count:
            raise TypeError(
                f"@{cls._decorator_name} '{func.__name__}' requires exactly "
                f"{cls._runtime_count} runtime "
                f"argument{'s' if cls._runtime_count != 1 else ''}, "
                f"but found {len(analysis.runtime_params)}. "
                "All build arguments must be keyword-only."
            )
        return cls(
            func,
            analysis.specs,
            analysis.public_signature,
            mode=resolved_mode,
        )

    def __init__(
        self,
        func: Callable,
        specs: Mapping[str, Spec],
        signature: inspect.Signature,
        *,
        mode: ExecutionMode,
    ):
        update_wrapper(self, func)
        self._func = func
        self._specs = specs
        self.__signature__ = signature
        self.mode = mode

    @property
    def element_type(self) -> type[_E]:
        return cast(type[_E], self._element_types[self.mode])

    @property
    def func(self) -> Callable:
        return self._func

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _E:
        if args:
            raise TypeError("Node and Wrapper factories accept keyword arguments only.")
        with enrich_exception(f"for '{self._func.__name__}'"):
            return self.element_type(func=self._func, specs=self._specs, params=kwargs)


class NodeFactory(_FactoryBase[_P, _E]):
    """Build a Node or AsyncNode according to ``mode``."""

    _decorator_name = "node"
    _runtime_count = 1
    _element_types: Mapping[ExecutionMode, type[NodeElement]] = {
        "sync": Node,
        "async": AsyncNode,
    }


class WrapperFactory(_FactoryBase[_P, _E]):
    """Build a Wrapper or AsyncWrapper according to ``mode``."""

    _decorator_name = "wrapper"
    _runtime_count = 3
    _element_types: Mapping[ExecutionMode, type[NodeElement]] = {
        "sync": Wrapper,
        "async": AsyncWrapper,
    }


class _AutoNodeDecorator(Protocol):
    @overload
    def __call__(self, func: NodeFunc[_P, _R], /) -> NodeFactory[_P, Node[_R]]: ...
    @overload
    def __call__(
        self, func: AsyncNodeFunc[_P, _R], /
    ) -> NodeFactory[_P, AsyncNode[_R]]: ...


class _AutoWrapperDecorator(Protocol):
    @overload
    def __call__(
        self, func: WrapperFunc[_P, _R], /
    ) -> WrapperFactory[_P, Wrapper[_R]]: ...
    @overload
    def __call__(
        self, func: AsyncWrapperFunc[_P, _R], /
    ) -> WrapperFactory[_P, AsyncWrapper[_R]]: ...


@overload
def node(
    func: NodeFunc[_P, _R],
    /,
    *,
    mode: Literal["sync"] | None = None,
    resolve_type_hints: bool = True,
) -> NodeFactory[_P, Node[_R]]: ...
@overload
def node(
    func: AsyncNodeFunc[_P, _R],
    /,
    *,
    mode: Literal["async"] | None = None,
    resolve_type_hints: bool = True,
) -> NodeFactory[_P, AsyncNode[_R]]: ...
@overload
def node(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["sync"],
    resolve_type_hints: bool = True,
) -> Callable[[NodeFunc[_P, _R]], NodeFactory[_P, Node[_R]]]: ...
@overload
def node(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["async"],
    resolve_type_hints: bool = True,
) -> Callable[[AsyncNodeFunc[_P, _R]], NodeFactory[_P, AsyncNode[_R]]]: ...
@overload
def node(
    func: _Missing = _MISSING,
    /,
    *,
    mode: None = None,
    resolve_type_hints: bool = True,
) -> _AutoNodeDecorator: ...
def node(
    func: Callable[..., Any] | _Missing = _MISSING,
    /,
    *,
    mode: ExecutionMode | None = None,
    resolve_type_hints: bool = True,
) -> Any:
    """Create a synchronous or asynchronous Node factory.

    With ``mode=None``, ``async def`` callables are detected by inspection; all
    other callables are synchronous. Use ``mode="async"`` for a regular
    function that returns an Awaitable.
    """
    if func is _MISSING:
        return partial(
            NodeFactory._decorate,
            mode=mode,
            resolve_type_hints=resolve_type_hints,
        )
    return NodeFactory._decorate(
        func,
        mode=mode,
        resolve_type_hints=resolve_type_hints,
    )


@overload
def wrapper(
    func: WrapperFunc[_P, _R],
    /,
    *,
    mode: Literal["sync"] | None = None,
    resolve_type_hints: bool = True,
) -> WrapperFactory[_P, Wrapper[_R]]: ...
@overload
def wrapper(
    func: AsyncWrapperFunc[_P, _R],
    /,
    *,
    mode: Literal["async"] | None = None,
    resolve_type_hints: bool = True,
) -> WrapperFactory[_P, AsyncWrapper[_R]]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["sync"],
    resolve_type_hints: bool = True,
) -> Callable[[WrapperFunc[_P, _R]], WrapperFactory[_P, Wrapper[_R]]]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["async"],
    resolve_type_hints: bool = True,
) -> Callable[[AsyncWrapperFunc[_P, _R]], WrapperFactory[_P, AsyncWrapper[_R]]]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: None = None,
    resolve_type_hints: bool = True,
) -> _AutoWrapperDecorator: ...
def wrapper(
    func: Callable[..., Any] | _Missing = _MISSING,
    /,
    *,
    mode: ExecutionMode | None = None,
    resolve_type_hints: bool = True,
) -> Any:
    """Create a synchronous or asynchronous Wrapper factory."""
    if func is _MISSING:
        return partial(
            WrapperFactory._decorate,
            mode=mode,
            resolve_type_hints=resolve_type_hints,
        )
    return WrapperFactory._decorate(
        func,
        mode=mode,
        resolve_type_hints=resolve_type_hints,
    )


from .eval import async_execute_eval_plan, execute_eval_plan, prepare_eval_plan
from .tree import NODE_ENGINE
