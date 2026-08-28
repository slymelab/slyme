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

import types
import inspect
from enum import Enum
from functools import partial, update_wrapper
from typing import (
    TypeVar,
    Callable,
    Any,
    Union,
    overload,
    Mapping,
    Optional,
    Generic,
    Iterable,
    Sequence,
    Awaitable,
    Literal,
)
from typing_extensions import ParamSpec, Concatenate, Protocol, Self
from slyme.utils.exception import enrich_exception
from slyme.context import Context, RefLike
from .exception import (
    NodeTerminate,
    NodeExceptionRecord,
    NodeException,
    WrapperExceptionRecord,
)
from .signature import (
    Spec,
    process_kwargs,
    analyze_signature,
    UNDEFINED,
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


def _snapshot_kwargs(kwargs: Mapping[str, Any]) -> Mapping[str, Any]:
    """Freeze built-in containers without traversing Node-related objects."""
    return NODE_SNAPSHOT_ENGINE.map(lambda x: x, dict(kwargs))


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


def _validate_kwargs(specs: Mapping[str, Spec], kwargs: Mapping[str, Any]) -> None:
    allowed_names = set(specs.keys())
    input_names = set(kwargs.keys())

    # 1. Exact match check
    unknown_args = input_names - allowed_names
    missing_args = allowed_names - input_names

    if unknown_args or missing_args:
        msg_parts = []
        if unknown_args:
            msg_parts.append(f"unexpected keyword argument(s) {list(unknown_args)}")
        if missing_args:
            msg_parts.append(f"missing required argument(s) {list(missing_args)}")

        raise TypeError(
            f"Got {' and '.join(msg_parts)}. Allowed arguments: {list(allowed_names)}."
        )

    # 2. Check for UNDEFINED values
    undefined_args = [k for k, v in kwargs.items() if v is UNDEFINED]
    if undefined_args:
        raise ValueError(f"Missing required parameter(s): {undefined_args}.")


class _MutableMixin:
    """Controlled mutation for Node and Wrapper build parameters."""

    _specs: Mapping[str, Spec]
    _kwargs: dict[str, Any]

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        spec_obj = self._specs[key]
        with enrich_exception(f"for parameter '{key}'"):
            value = spec_obj._build(value)
        self._kwargs[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"Cannot delete attribute '{name}' on {type(self).__name__}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot set attribute '{name}' on {type(self).__name__}. "
            "Internal structure is protected."
        )


# Node Family
class NodeElement:
    """Base class for node-related graph elements."""

    _func: Callable
    _specs: Mapping[str, Spec]
    _kwargs: Mapping[str, Any]

    @property
    def func(self) -> Callable:
        return self._func

    @property
    def specs(self) -> Mapping[str, Spec]:
        return self._specs

    @property
    def kwargs(self) -> Mapping[str, Any]:
        if isinstance(self._kwargs, types.MappingProxyType):
            return self._kwargs
        return types.MappingProxyType(self._kwargs)

    def __getitem__(self, key: str) -> Any:
        return self._kwargs[key]

    def __repr__(self) -> str:
        return get_render_string(self)

    def extra_repr(self) -> str:
        return ""

    def type_repr(self) -> str:
        return f"{self._func.__name__}<{self.__class__.__name__}>"


class BaseNode(NodeElement):
    pass


class Node(_MutableMixin, BaseNode, Generic[_R]):
    """Mutable synchronous Node executed from a call-local snapshot."""

    _kwargs: dict[str, Any]
    wrappers: list["Wrapper"]

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Optional[Iterable["Wrapper"]] = None,
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "wrappers", list(wrappers) if wrappers else [])
        object.__setattr__(self, "_kwargs", kwargs)

    def add_wrappers(self, *wrappers: "Wrapper") -> Self:
        self.wrappers.extend(wrappers)
        return self

    def __call__(self, ctx: Context) -> _R:
        try:
            kwargs = _snapshot_kwargs(self._kwargs)
            wrappers = tuple(self.wrappers)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_kwargs(self._specs, kwargs)
            raw_kwargs, eval_kwargs = _prepare_eval(self._specs, kwargs)
            if not eval_kwargs:
                chain: Callable[[Context], _R] = partial(
                    self._func, **raw_kwargs
                )
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
        context: Optional[Context] = None,
        /,
        *,
        inputs: Optional[Mapping[RefLike, Any]] = None,
        outputs: Any = None,
        return_context: bool = False,
        use_argparse: bool = False,
        cli_args: Optional[Sequence[str]] = None,
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

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "wrappers":
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)


class AsyncNode(_MutableMixin, BaseNode, Generic[_R]):
    """Mutable asynchronous Node executed from a call-local snapshot."""

    _kwargs: dict[str, Any]
    wrappers: list["AsyncWrapper"]

    def __init__(
        self,
        /,
        *,
        func: AsyncNodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Optional[Iterable["AsyncWrapper"]] = None,
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "wrappers", list(wrappers) if wrappers else [])
        object.__setattr__(self, "_kwargs", kwargs)

    def add_wrappers(self, *wrappers: "AsyncWrapper") -> Self:
        self.wrappers.extend(wrappers)
        return self

    async def __call__(self, ctx: Context) -> _R:
        try:
            kwargs = _snapshot_kwargs(self._kwargs)
            wrappers = tuple(self.wrappers)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_kwargs(self._specs, kwargs)
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
        context: Optional[Context] = None,
        /,
        *,
        inputs: Optional[Mapping[RefLike, Any]] = None,
        outputs: Any = None,
        return_context: bool = False,
        use_argparse: bool = False,
        cli_args: Optional[Sequence[str]] = None,
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

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "wrappers":
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)


class BaseWrapper(NodeElement):
    pass


class Wrapper(_MutableMixin, BaseWrapper):
    """Mutable synchronous Wrapper executed from a call-local snapshot."""

    _kwargs: dict[str, Any]  # Override: Mapping -> dict

    def __init__(
        self,
        /,
        *,
        func: WrapperFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

    def __call__(
        self,
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
    ) -> Any:
        try:
            kwargs = _snapshot_kwargs(self._kwargs)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_kwargs(self._specs, kwargs)
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


class AsyncWrapper(_MutableMixin, BaseWrapper):
    """Mutable asynchronous Wrapper executed from a call-local snapshot."""

    _kwargs: dict[str, Any]

    def __init__(
        self,
        /,
        *,
        func: AsyncWrapperFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

    async def __call__(
        self,
        ctx: Context,
        wrapped: AsyncNode[Any],
        call_next: Callable[[Context], Awaitable[Any]],
    ) -> Any:
        try:
            kwargs = _snapshot_kwargs(self._kwargs)
            with enrich_exception(f"in call preparation for '{self._func.__name__}'"):
                _validate_kwargs(self._specs, kwargs)
            raw_kwargs, eval_kwargs = _prepare_eval(self._specs, kwargs)
            if eval_kwargs:
                raw_kwargs.update(
                    await async_execute_eval_plan(
                        ctx, prepare_eval_plan(eval_kwargs)
                    )
                )
            return await self._func(ctx, wrapped, call_next, **raw_kwargs)
        except NodeException:
            raise
        except Exception as e:
            raise WrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            ) from e


# Functional Factory & Decorators
class BaseFactory:
    _func: Callable

    @property
    def func(self) -> Callable:
        return self._func

    def __repr__(self) -> str:
        return f"<{type(self).__name__} of {self._func.__name__}>"


class NodeFactory(BaseFactory, Generic[_P, _R]):
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

    def __call__(self, **kwargs: _P.kwargs) -> Node[_R]:
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, kwargs)
        # Note: wrappers are intentionally omitted to avoid parameter conflict.
        # Users should use .add_wrappers() explicitly.
        return Node(func=self._func, specs=self._specs, kwargs=final_kwargs)


class WrapperFactory(BaseFactory, Generic[_P]):
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

    def __call__(self, **kwargs: _P.kwargs) -> Wrapper:
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, kwargs)
        return Wrapper(func=self._func, specs=self._specs, kwargs=final_kwargs)


class AsyncNodeFactory(BaseFactory, Generic[_P, _R]):
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

    def __call__(self, **kwargs: _P.kwargs) -> AsyncNode[_R]:
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, kwargs)
        # Note: wrappers are intentionally omitted to avoid parameter conflict.
        # Users should use .add_wrappers() explicitly.
        return AsyncNode(func=self._func, specs=self._specs, kwargs=final_kwargs)


class AsyncWrapperFactory(BaseFactory, Generic[_P]):
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

    def __call__(self, **kwargs: _P.kwargs) -> AsyncWrapper:
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, kwargs)
        return AsyncWrapper(func=self._func, specs=self._specs, kwargs=final_kwargs)


def _node(
    func: NodeFunc[_P, _R], /, *, resolve_type_hints: bool
) -> NodeFactory[_P, _R]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.runtime_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 runtime argument, "
            f"but found {len(analysis.runtime_params)}. "
            "All build arguments must be keyword-only."
        )
    return NodeFactory(func, analysis.specs, analysis.public_signature)


def _async_node(
    func: AsyncNodeFunc[_P, _R], /, *, resolve_type_hints: bool
) -> AsyncNodeFactory[_P, _R]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.runtime_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 runtime argument, "
            f"but found {len(analysis.runtime_params)}. "
            "All build arguments must be keyword-only."
        )
    return AsyncNodeFactory(func, analysis.specs, analysis.public_signature)


def _wrapper(
    func: WrapperFunc[_P], /, *, resolve_type_hints: bool
) -> WrapperFactory[_P]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.runtime_params) != 3:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 3 runtime arguments, "
            f"but found {len(analysis.runtime_params)}. "
            "All build arguments must be keyword-only."
        )
    return WrapperFactory(func, analysis.specs, analysis.public_signature)


def _async_wrapper(
    func: AsyncWrapperFunc[_P], /, *, resolve_type_hints: bool
) -> AsyncWrapperFactory[_P]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.runtime_params) != 3:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 3 runtime arguments, "
            f"but found {len(analysis.runtime_params)}. "
            "All build arguments must be keyword-only."
        )
    return AsyncWrapperFactory(func, analysis.specs, analysis.public_signature)


class _AutoNodeDecorator(Protocol):
    @overload
    def __call__(self, func: NodeFunc[_P, _R], /) -> NodeFactory[_P, _R]: ...
    @overload
    def __call__(
        self, func: AsyncNodeFunc[_P, _R], /
    ) -> AsyncNodeFactory[_P, _R]: ...


class _AutoWrapperDecorator(Protocol):
    @overload
    def __call__(self, func: WrapperFunc[_P], /) -> WrapperFactory[_P]: ...
    @overload
    def __call__(self, func: AsyncWrapperFunc[_P], /) -> AsyncWrapperFactory[_P]: ...


def _is_async_callable(func: Callable[..., Any]) -> bool:
    """Return whether *func* is declared with ``async def``.

    Return annotations are intentionally ignored. A synchronous function that
    returns an Awaitable must opt in with ``mode="async"``.
    """
    unwrapped = inspect.unwrap(func)
    if inspect.iscoroutinefunction(unwrapped):
        return True
    call = getattr(unwrapped, "__call__", None)
    return call is not None and inspect.iscoroutinefunction(call)


def _resolve_execution_mode(
    func: Callable[..., Any],
    mode: Optional[ExecutionMode],
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


def _dispatch_node(
    func: Callable[..., Any],
    /,
    *,
    mode: Optional[ExecutionMode],
    resolve_type_hints: bool,
) -> Union[NodeFactory[Any, Any], AsyncNodeFactory[Any, Any]]:
    resolved_mode = _resolve_execution_mode(func, mode, "node")
    if resolved_mode == "async":
        return _async_node(func, resolve_type_hints=resolve_type_hints)
    return _node(func, resolve_type_hints=resolve_type_hints)


def _dispatch_wrapper(
    func: Callable[..., Any],
    /,
    *,
    mode: Optional[ExecutionMode],
    resolve_type_hints: bool,
) -> Union[WrapperFactory[Any], AsyncWrapperFactory[Any]]:
    resolved_mode = _resolve_execution_mode(func, mode, "wrapper")
    if resolved_mode == "async":
        return _async_wrapper(func, resolve_type_hints=resolve_type_hints)
    return _wrapper(func, resolve_type_hints=resolve_type_hints)


@overload
def node(
    func: NodeFunc[_P, _R],
    /,
    *,
    mode: Optional[Literal["sync"]] = None,
    resolve_type_hints: bool = True,
) -> NodeFactory[_P, _R]: ...
@overload
def node(
    func: AsyncNodeFunc[_P, _R],
    /,
    *,
    mode: Optional[Literal["async"]] = None,
    resolve_type_hints: bool = True,
) -> AsyncNodeFactory[_P, _R]: ...
@overload
def node(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["sync"],
    resolve_type_hints: bool = True,
) -> Callable[[NodeFunc[_P, _R]], NodeFactory[_P, _R]]: ...
@overload
def node(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["async"],
    resolve_type_hints: bool = True,
) -> Callable[[AsyncNodeFunc[_P, _R]], AsyncNodeFactory[_P, _R]]: ...
@overload
def node(
    func: _Missing = _MISSING,
    /,
    *,
    mode: None = None,
    resolve_type_hints: bool = True,
) -> _AutoNodeDecorator: ...
def node(
    func: Union[Callable[..., Any], _Missing] = _MISSING,
    /,
    *,
    mode: Optional[ExecutionMode] = None,
    resolve_type_hints: bool = True,
) -> Any:
    """Create a synchronous or asynchronous Node factory.

    With ``mode=None``, ``async def`` callables are detected by inspection; all
    other callables are synchronous. Use ``mode="async"`` for a regular
    function that returns an Awaitable.
    """
    if func is _MISSING:
        return partial(_dispatch_node, mode=mode, resolve_type_hints=resolve_type_hints)
    return _dispatch_node(func, mode=mode, resolve_type_hints=resolve_type_hints)


@overload
def wrapper(
    func: WrapperFunc[_P],
    /,
    *,
    mode: Optional[Literal["sync"]] = None,
    resolve_type_hints: bool = True,
) -> WrapperFactory[_P]: ...
@overload
def wrapper(
    func: AsyncWrapperFunc[_P],
    /,
    *,
    mode: Optional[Literal["async"]] = None,
    resolve_type_hints: bool = True,
) -> AsyncWrapperFactory[_P]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["sync"],
    resolve_type_hints: bool = True,
) -> Callable[[WrapperFunc[_P]], WrapperFactory[_P]]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: Literal["async"],
    resolve_type_hints: bool = True,
) -> Callable[[AsyncWrapperFunc[_P]], AsyncWrapperFactory[_P]]: ...
@overload
def wrapper(
    func: _Missing = _MISSING,
    /,
    *,
    mode: None = None,
    resolve_type_hints: bool = True,
) -> _AutoWrapperDecorator: ...
def wrapper(
    func: Union[Callable[..., Any], _Missing] = _MISSING,
    /,
    *,
    mode: Optional[ExecutionMode] = None,
    resolve_type_hints: bool = True,
) -> Any:
    """Create a synchronous or asynchronous Wrapper factory."""
    if func is _MISSING:
        return partial(
            _dispatch_wrapper, mode=mode, resolve_type_hints=resolve_type_hints
        )
    return _dispatch_wrapper(func, mode=mode, resolve_type_hints=resolve_type_hints)


from .tree import NODE_ENGINE, NODE_SNAPSHOT_ENGINE
from .render import get_render_string
from .eval import prepare_eval_plan, execute_eval_plan, async_execute_eval_plan
