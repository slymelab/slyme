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
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from enum import Enum
from functools import partial, update_wrapper
from typing import (
    Any,
    ClassVar,
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

from slyme.context import Context, RefLike
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
    Concatenate[Context, "Node[Any]", Callable[[Context], Any], _P], Any
]
AsyncNodeFunc = Callable[Concatenate[Context, _P], Awaitable[_R]]
AsyncWrapperFunc = Callable[
    Concatenate[
        Context,
        "AsyncNode[Any]",
        Callable[[Context], Awaitable[Any]],
        _P,
    ],
    Awaitable[Any],
]
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


def _collect_params(element: "NodeElement") -> dict[str, Any]:
    """Collect build parameters from their real instance attributes."""
    return {name: getattr(element, name) for name in element._specs}


def _prepare_eval(
    specs: Mapping[str, Spec], kwargs: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Split kwargs into static values and dynamic evaluators based on specs.
    """
    raw_kwargs = {}
    eval_kwargs = {}
    for key, value in kwargs.items():
        if specs[key].should_eval(value):
            eval_kwargs[key] = value
        else:
            raw_kwargs[key] = value
    return raw_kwargs, eval_kwargs


def _validate_inputs(specs: Mapping[str, Spec], params: Mapping[str, Any]) -> None:
    """Reject parameters that are not declared by the decorated function."""
    allowed_names = set(specs.keys())
    unknown_args = set(params.keys()) - allowed_names
    if unknown_args:
        raise TypeError(
            f"Got unexpected keyword argument(s) {list(unknown_args)}. "
            f"Allowed arguments: {list(allowed_names)}."
        )


def _validate_ready(params: Mapping[str, Any]) -> None:
    """Reject unresolved required parameters at the call boundary."""
    undefined_args = [name for name, value in params.items() if value is UNDEFINED]
    if undefined_args:
        raise ValueError(f"Missing required parameter(s): {undefined_args}.")


# Node Family
class NodeElement:
    """Base class for node-related graph elements."""

    _internal_attrs: ClassVar[frozenset[str]] = frozenset({"_func", "_specs"})

    def __init__(
        self,
        *,
        func: Callable,
        specs: Mapping[str, Spec],
        params: Mapping[str, Any],
    ) -> None:
        self._func = func
        self._specs = specs
        _validate_inputs(specs, params)
        for name in specs:
            setattr(self, name, params[name] if name in params else UNSET)

    @property
    def func(self) -> Callable:
        return self._func

    @property
    def specs(self) -> Mapping[str, Spec]:
        return self._specs

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._internal_attrs:
            super().__setattr__(name, value)
            return
        if name not in self._specs:
            raise AttributeError(
                f"Cannot set unknown attribute '{name}' on {type(self).__name__}."
            )
        with enrich_exception(f"for parameter '{name}'"):
            value = self._specs[name]._build(value)
        super().__setattr__(name, value)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"Cannot delete attribute '{name}' on {type(self).__name__}."
        )

    def __repr__(self) -> str:
        return get_render_string(self)

    def extra_repr(self) -> str:
        return ""

    def type_repr(self) -> str:
        return f"{self._func.__name__}<{self.__class__.__name__}>"

    def clone(self) -> Self:
        """Structurally clone this element and its parameter PyTree."""
        return cast(Self, NODE_ENGINE.map(lambda leaf: leaf, self))


class Node(NodeElement, Generic[_R]):
    """Mutable synchronous Node."""

    _internal_attrs = NodeElement._internal_attrs | {"wrappers"}

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Iterable["Wrapper"] | None = None,
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, specs=specs, params=params)
        self.wrappers = list(wrappers) if wrappers else []

    def add_wrappers(self, *wrappers: "Wrapper") -> Self:
        self.wrappers.extend(wrappers)
        return self

    def __call__(self, ctx: Context) -> _R:
        try:
            kwargs = _collect_params(self)
            wrappers = tuple(self.wrappers)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_ready(kwargs)
            raw_kwargs, eval_kwargs = _prepare_eval(self._specs, kwargs)
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

    def run(
        self,
        context: Context | None = None,
        /,
        *,
        inputs: Mapping[RefLike, Any] | None = None,
        outputs: Any = None,
        return_context: bool = False,
        use_argparse: bool = False,
        cli_args: Sequence[str] | None = None,
    ) -> Any:
        """Execute this Node and optionally extract outputs."""
        from .runner import run_node

        return run_node(
            self,
            context,
            inputs=inputs,
            outputs=outputs,
            return_context=return_context,
            use_argparse=use_argparse,
            cli_args=cli_args,
        )


class AsyncNode(NodeElement, Generic[_R]):
    """Mutable asynchronous Node."""

    _internal_attrs = NodeElement._internal_attrs | {"wrappers"}

    def __init__(
        self,
        /,
        *,
        func: AsyncNodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Iterable["AsyncWrapper"] | None = None,
        params: Mapping[str, Any],
    ):
        super().__init__(func=func, specs=specs, params=params)
        self.wrappers = list(wrappers) if wrappers else []

    def add_wrappers(self, *wrappers: "AsyncWrapper") -> Self:
        self.wrappers.extend(wrappers)
        return self

    async def __call__(self, ctx: Context) -> _R:
        try:
            kwargs = _collect_params(self)
            wrappers = tuple(self.wrappers)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_ready(kwargs)
            raw_kwargs, eval_kwargs = _prepare_eval(self._specs, kwargs)
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

    async def run(
        self,
        context: Context | None = None,
        /,
        *,
        inputs: Mapping[RefLike, Any] | None = None,
        outputs: Any = None,
        return_context: bool = False,
        use_argparse: bool = False,
        cli_args: Sequence[str] | None = None,
    ) -> Any:
        """Execute this async Node and optionally extract outputs."""
        from .runner import run_async_node

        return await run_async_node(
            self,
            context,
            inputs=inputs,
            outputs=outputs,
            return_context=return_context,
            use_argparse=use_argparse,
            cli_args=cli_args,
        )


class Wrapper(NodeElement):
    """Mutable synchronous Wrapper."""

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
    ) -> Any:
        try:
            kwargs = _collect_params(self)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_ready(kwargs)
            raw_kwargs, eval_kwargs = _prepare_eval(self._specs, kwargs)
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


class AsyncWrapper(NodeElement):
    """Mutable asynchronous Wrapper."""

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
    ) -> Any:
        try:
            kwargs = _collect_params(self)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_ready(kwargs)
            raw_kwargs, eval_kwargs = _prepare_eval(self._specs, kwargs)
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
def _validate_conflicts(
    element_type: type[NodeElement],
    specs: Mapping[str, Spec],
    func: Callable,
) -> None:
    """Reject build parameters that would shadow the element's own API."""
    conflicts = [
        name
        for name in specs
        if name in element_type._internal_attrs
        or any(name in base.__dict__ for base in element_type.__mro__)
    ]
    if conflicts:
        raise TypeError(
            f"Build parameter name(s) {conflicts} in '{func.__name__}' conflict "
            f"with reserved {element_type.__name__} attributes."
        )


class _FactoryBase(Generic[_P, _E]):
    """Shared implementation for mode-aware graph element factories."""

    _element_types: Mapping[ExecutionMode, type[NodeElement]]

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

    def __repr__(self) -> str:
        return f"<{type(self).__name__}[{self.mode}] of {self._func.__name__}>"

    def __call__(self, *args: _P.args, **kwargs: _P.kwargs) -> _E:
        if args:
            raise TypeError("Node and Wrapper factories accept keyword arguments only.")
        with enrich_exception(f"for '{self._func.__name__}'"):
            return self.element_type(func=self._func, specs=self._specs, params=kwargs)


class NodeFactory(_FactoryBase[_P, _E]):
    """Build a Node or AsyncNode according to ``mode``."""

    _element_types: Mapping[ExecutionMode, type[NodeElement]] = {
        "sync": Node,
        "async": AsyncNode,
    }


class WrapperFactory(_FactoryBase[_P, _E]):
    """Build a Wrapper or AsyncWrapper according to ``mode``."""

    _element_types: Mapping[ExecutionMode, type[NodeElement]] = {
        "sync": Wrapper,
        "async": AsyncWrapper,
    }


def _decorate(
    func: Callable[..., Any],
    /,
    *,
    mode: ExecutionMode | None,
    resolve_type_hints: bool,
    kind: Literal["node", "wrapper"],
) -> NodeFactory[Any, Any] | WrapperFactory[Any, Any]:
    resolved_mode = _resolve_execution_mode(func, mode, kind)
    factory_type: Any
    if kind == "node":
        factory_type = NodeFactory
        runtime_count = 1
    else:
        factory_type = WrapperFactory
        runtime_count = 3
    element_type = factory_type._element_types[resolved_mode]
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.runtime_params) != runtime_count:
        raise TypeError(
            f"@{kind} '{func.__name__}' requires exactly {runtime_count} runtime "
            f"argument{'s' if runtime_count != 1 else ''}, "
            f"but found {len(analysis.runtime_params)}. "
            "All build arguments must be keyword-only."
        )
    _validate_conflicts(element_type, analysis.specs, func)
    return factory_type(
        func,
        analysis.specs,
        analysis.public_signature,
        mode=resolved_mode,
    )


class _AutoNodeDecorator(Protocol):
    @overload
    def __call__(self, func: NodeFunc[_P, _R], /) -> NodeFactory[_P, Node[_R]]: ...
    @overload
    def __call__(
        self, func: AsyncNodeFunc[_P, _R], /
    ) -> NodeFactory[_P, AsyncNode[_R]]: ...


class _AutoWrapperDecorator(Protocol):
    @overload
    def __call__(self, func: WrapperFunc[_P], /) -> WrapperFactory[_P, Wrapper]: ...
    @overload
    def __call__(
        self, func: AsyncWrapperFunc[_P], /
    ) -> WrapperFactory[_P, AsyncWrapper]: ...


def _is_async_callable(func: Callable[..., Any]) -> bool:
    """Return whether *func* is declared with ``async def``.

    Return annotations are intentionally ignored. A synchronous function that
    returns an Awaitable must opt in with ``mode="async"``.
    """
    unwrapped = inspect.unwrap(func)
    if inspect.iscoroutinefunction(unwrapped):
        return True
    return callable(unwrapped) and inspect.iscoroutinefunction(unwrapped.__call__)


def _resolve_execution_mode(
    func: Callable[..., Any],
    mode: ExecutionMode | None,
    decorator_name: str,
) -> ExecutionMode:
    if mode not in (None, "sync", "async"):
        raise ValueError(
            f"@{decorator_name} mode must be 'sync', 'async', or None, got {mode!r}."
        )
    detected_async = _is_async_callable(func)
    if mode is None:
        return "async" if detected_async else "sync"
    if mode == "sync" and detected_async:
        raise TypeError(
            f"@{decorator_name}(mode='sync') cannot decorate an async function."
        )
    return mode


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
            _decorate,
            mode=mode,
            resolve_type_hints=resolve_type_hints,
            kind="node",
        )
    return _decorate(
        func, mode=mode, resolve_type_hints=resolve_type_hints, kind="node"
    )


@overload
def wrapper(
    func: WrapperFunc[_P],
    /,
    *,
    mode: Literal["sync"] | None = None,
    resolve_type_hints: bool = True,
) -> WrapperFactory[_P, Wrapper]: ...
@overload
def wrapper(
    func: AsyncWrapperFunc[_P],
    /,
    *,
    mode: Literal["async"] | None = None,
    resolve_type_hints: bool = True,
) -> WrapperFactory[_P, AsyncWrapper]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["sync"],
    resolve_type_hints: bool = True,
) -> Callable[[WrapperFunc[_P]], WrapperFactory[_P, Wrapper]]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["async"],
    resolve_type_hints: bool = True,
) -> Callable[[AsyncWrapperFunc[_P]], WrapperFactory[_P, AsyncWrapper]]: ...
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
            _decorate,
            mode=mode,
            resolve_type_hints=resolve_type_hints,
            kind="wrapper",
        )
    return _decorate(
        func, mode=mode, resolve_type_hints=resolve_type_hints, kind="wrapper"
    )


from .eval import async_execute_eval_plan, execute_eval_plan, prepare_eval_plan
from .render import get_render_string
from .tree import NODE_ENGINE
