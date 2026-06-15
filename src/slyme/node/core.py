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
from abc import ABC, abstractmethod
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
)
from typing_extensions import ParamSpec, Concatenate, Self
from slyme.utils.exception import enrich_exception
from slyme.context import Context, RefFactory
from .exception import (
    NodeTerminate,
    NodeExceptionRecord,
    NodeException,
    ExpressionExceptionRecord,
    WrapperExceptionRecord,
)
from .signature import (
    Spec,
    process_kwargs,
    analyze_signature,
    resolve_arguments,
    UNDEFINED,
)

__all__ = [
    "Config",
    "node",
    "expression",
    "wrapper",
    "NodeElement",
    "Node",
    "NodeDef",
    "NodeExec",
    "Expression",
    "ExpressionDef",
    "ExpressionExec",
    "Wrapper",
    "WrapperDef",
    "WrapperExec",
    "AsyncNode",
    "AsyncNodeDef",
    "AsyncNodeExec",
    "AsyncExpression",
    "AsyncExpressionDef",
    "AsyncExpressionExec",
    "AsyncWrapper",
    "AsyncWrapperDef",
    "AsyncWrapperExec",
    "NODE_ENGINE",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")

# Update: NodeFunc now must return Context to align with the chain protocol.
NodeFunc = Callable[Concatenate[Context, _P], Context]
ExpressionFunc = Callable[Concatenate[Context, _P], _R]
# Wrapper definition: (ctx, wrapped, call_next, ...args) -> Context
# call_next definition: (Context) -> Context
WrapperFunc = Callable[
    Concatenate[Context, "Node", Callable[[Context], Context], _P], Context
]
AsyncNodeFunc = Callable[Concatenate[Context, _P], Awaitable[Context]]
AsyncExpressionFunc = Callable[Concatenate[Context, _P], Awaitable[_R]]
AsyncWrapperFunc = Callable[
    Concatenate[Context, "AsyncNode", Callable[[Context], Awaitable[Context]], _P],
    Awaitable[Context],
]
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


def _prepare_map(x: Any) -> Any:
    """Leaf transform for :func:`NODE_PREPARE_ENGINE.map`. Normalizes RefFactory→Ref."""
    if isinstance(x, RefFactory):
        return x()
    return x


class Config:
    check_return_type: bool = True


def _ensure_context_return(func: Callable[_P, Any]) -> Callable[_P, Context]:
    """
    Wrap a callable to ensure it returns a Context object.
    """

    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Context:
        result = func(*args, **kwargs)
        if not isinstance(result, Context):
            raise TypeError(
                f"Node execution return type mismatch. "
                f"Expected 'Context', but got '{type(result).__name__}'. "
                f"Function: {func}"
            )
        return result

    return wrapper


def _ensure_async_context_return(
    func: Callable[_P, Awaitable[Any]],
) -> Callable[_P, Awaitable[Context]]:
    """
    Wrap an async callable to ensure it returns a Context object.
    """

    async def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> Context:
        result = await func(*args, **kwargs)
        if not isinstance(result, Context):
            raise TypeError(
                f"Node execution return type mismatch. "
                f"Expected 'Context', but got '{type(result).__name__}'. "
                f"Function: {func}"
            )
        return result

    return wrapper


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


class _DefMixin:
    """
    Mixin for mutable definition classes.
    """

    _specs: Mapping[str, Spec]
    _kwargs: dict[str, Any]

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        raise TypeError(
            f"{type(self).__name__} is a definition and not callable. "
            f"Please call `.prepare()` to obtain an Exec instance first."
        )

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


class _ExecMixin:
    """
    Mixin for immutable execution classes.
    """

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(
            f"{type(self).__name__} is immutable and does not support item assignment."
        )

    def __delattr__(self, name: str) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")


# Node Family
class NodeElement(ABC):
    """
    Base class for all node-related entities.
    Abstract definition that separates API from implementation.
    """

    _func: Callable
    _specs: Mapping[str, Spec]
    _kwargs: Mapping[str, Any]

    @abstractmethod
    def prepare(self) -> "NodeElement":
        """
        Prepare the node for execution.
        Converts the Definition structure into an Execution structure.
        """
        pass

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


class Node(BaseNode):
    """
    Abstract base class for NodeDef and NodeExec.
    """

    _func: NodeFunc
    wrappers: Sequence["Wrapper"]  # Changed: Union[...] -> Sequence

    @abstractmethod
    def __call__(self, ctx: Context) -> Context:
        pass


class NodeDef(_DefMixin, Node):
    """
    Mutable definition of a Node. Allows modification during build time.
    """

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

    def prepare(self) -> "NodeExec":
        # Use the specialized NODE_PREPARE_PYTREE_ENGINE to perform a deep transform
        # of the structure (List -> Tuple, Dict -> MappingProxy, Def -> Exec).
        # We map strict identity because the transformation happens in the 'unflatten' phase
        # of the registered types in NODE_PREPARE_PYTREE_ENGINE.
        return NODE_PREPARE_ENGINE.map(_prepare_map, self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "wrappers":
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)


class NodeExec(_ExecMixin, Node):
    """
    Immutable execution version of a Node.
    """

    # composed_func signature: (Context) -> Context
    _prepared_func: Callable[[Context], Context]
    wrappers: tuple["Wrapper", ...]

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Iterable["Wrapper"],
        kwargs: Mapping[str, Any],
    ):
        with enrich_exception(f"in execution initialization for '{func.__name__}'"):
            _validate_kwargs(specs, kwargs)

        wrappers = tuple(wrappers)
        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "wrappers", wrappers)
        object.__setattr__(self, "_kwargs", kwargs)
        # --- Composition Logic (Onion Model) ---
        # 1. Inner Core: Bind kwargs to the user function.
        # Signature: (Context) -> Context
        raw_kwargs, eval_kwargs = _prepare_eval(specs, kwargs)

        if not eval_kwargs:
            chain: Callable[[Context], Context] = partial(func, **raw_kwargs)
        else:
            eval_plan = prepare_eval_plan(eval_kwargs)

            def chain(ctx: Context) -> Context:
                return func(ctx, **raw_kwargs, **execute_eval_plan(ctx, eval_plan))

        if Config.check_return_type:
            chain = _ensure_context_return(chain)

        # 2. Build the middleware chain.
        # Wrappers are applied from inside out (reversed order of list).
        for wrapper in reversed(wrappers):
            # wrapper is WrapperExec which is callable: (ctx, wrapped, call_next) -> Context
            # We partially apply `wrapped` (self) and `call_next` (current chain head)
            # to create the new chain head: (Context) -> Context
            chain = partial(wrapper, wrapped=self, call_next=chain)
        object.__setattr__(self, "_prepared_func", chain)

    def prepare(self) -> Self:
        return self

    def __call__(self, ctx: Context) -> Context:
        try:
            # Execute the pre-composed chain
            return self._prepared_func(ctx)
        # Node Interrupts
        except (NodeTerminate, ExpressionExceptionRecord) as e:
            if e.source_node is None:
                e.source_node = self
            raise
        # Direct Node Exceptions
        except NodeException:
            raise
        # General Exceptions
        except Exception as e:
            raise NodeExceptionRecord(exception_node=self, exception=e) from e


class AsyncNode(BaseNode):
    """
    Abstract base class for AsyncNodeDef and AsyncNodeExec.
    """

    _func: AsyncNodeFunc
    wrappers: Sequence["AsyncWrapper"]

    @abstractmethod
    async def __call__(self, ctx: Context) -> Context:
        pass


class AsyncNodeDef(_DefMixin, AsyncNode):
    """
    Mutable definition of an AsyncNode.
    """

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

    def prepare(self) -> "AsyncNodeExec":
        return NODE_PREPARE_ENGINE.map(_prepare_map, self)

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "wrappers":
            object.__setattr__(self, name, value)
        else:
            super().__setattr__(name, value)


class AsyncNodeExec(_ExecMixin, AsyncNode):
    """
    Immutable execution version of an AsyncNode.
    """

    _prepared_func: Callable[[Context], Awaitable[Context]]
    wrappers: tuple["AsyncWrapper", ...]

    def __init__(
        self,
        /,
        *,
        func: AsyncNodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Iterable["AsyncWrapper"],
        kwargs: Mapping[str, Any],
    ):
        with enrich_exception(f"in execution initialization for '{func.__name__}'"):
            _validate_kwargs(specs, kwargs)

        wrappers = tuple(wrappers)
        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "wrappers", wrappers)
        object.__setattr__(self, "_kwargs", kwargs)

        raw_kwargs, eval_kwargs = _prepare_eval(specs, kwargs)

        if not eval_kwargs:
            chain = partial(func, **raw_kwargs)
        else:
            eval_plan = prepare_eval_plan(eval_kwargs)

            async def chain(ctx: Context) -> Context:
                return await func(
                    ctx, **raw_kwargs, **await async_execute_eval_plan(ctx, eval_plan)
                )

        if Config.check_return_type:
            chain = _ensure_async_context_return(chain)

        for wrapper in reversed(wrappers):
            chain = partial(wrapper, wrapped=self, call_next=chain)
        object.__setattr__(self, "_prepared_func", chain)

    def prepare(self) -> Self:
        return self

    async def __call__(self, ctx: Context) -> Context:
        try:
            return await self._prepared_func(ctx)
        except (NodeTerminate, ExpressionExceptionRecord) as e:
            if e.source_node is None:
                e.source_node = self
            raise
        except NodeException:
            raise
        except Exception as e:
            raise NodeExceptionRecord(exception_node=self, exception=e) from e


class BaseExpression(NodeElement):
    pass


class Expression(BaseExpression, Generic[_R]):
    """
    Abstract base class for ExpressionDef and ExpressionExec.
    """

    _func: ExpressionFunc

    @abstractmethod
    def __call__(self, ctx: Context) -> _R:
        pass


class ExpressionDef(_DefMixin, Expression[_R]):
    """
    Mutable definition of an Expression.
    """

    _kwargs: dict[str, Any]  # Override: Mapping -> dict

    def __init__(
        self,
        /,
        *,
        func: ExpressionFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

    def prepare(self) -> "ExpressionExec[_R]":
        return NODE_PREPARE_ENGINE.map(_prepare_map, self)


class ExpressionExec(_ExecMixin, Expression[_R]):
    """
    Immutable execution version of an Expression.
    """

    # composed_func signature: (Context) -> _R
    _prepared_func: Callable[[Context], _R]

    def __init__(
        self,
        /,
        *,
        func: ExpressionFunc,
        specs: Mapping[str, Spec],
        kwargs: Mapping[str, Any],
    ):
        with enrich_exception(f"in execution initialization for '{func.__name__}'"):
            _validate_kwargs(specs, kwargs)

        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)
        # Optimization: Pre-bind kwargs using partial
        raw_kwargs, eval_kwargs = _prepare_eval(specs, kwargs)

        if not eval_kwargs:
            prepared_func = partial(func, **raw_kwargs)
        else:
            eval_plan = prepare_eval_plan(eval_kwargs)

            def prepared_func(ctx: Context) -> _R:
                return func(ctx, **raw_kwargs, **execute_eval_plan(ctx, eval_plan))

        object.__setattr__(self, "_prepared_func", prepared_func)

    def prepare(self) -> Self:
        return self

    def __call__(self, ctx: Context) -> _R:
        try:
            return self._prepared_func(ctx)
        except NodeException:
            raise
        except Exception as e:
            raise ExpressionExceptionRecord(exception_node=self, exception=e) from e


class AsyncExpression(BaseExpression, Generic[_R]):
    """
    Abstract base class for AsyncExpressionDef and AsyncExpressionExec.
    """

    _func: AsyncExpressionFunc

    @abstractmethod
    async def __call__(self, ctx: Context) -> _R:
        pass


class AsyncExpressionDef(_DefMixin, AsyncExpression[_R]):
    """
    Mutable definition of an AsyncExpression.
    """

    _kwargs: dict[str, Any]

    def __init__(
        self,
        /,
        *,
        func: AsyncExpressionFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

    def prepare(self) -> "AsyncExpressionExec[_R]":
        return NODE_PREPARE_ENGINE.map(_prepare_map, self)


class AsyncExpressionExec(_ExecMixin, AsyncExpression[_R]):
    """
    Immutable execution version of an AsyncExpression.
    """

    _prepared_func: Callable[[Context], Awaitable[_R]]

    def __init__(
        self,
        /,
        *,
        func: AsyncExpressionFunc,
        specs: Mapping[str, Spec],
        kwargs: Mapping[str, Any],
    ):
        with enrich_exception(f"in execution initialization for '{func.__name__}'"):
            _validate_kwargs(specs, kwargs)

        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

        raw_kwargs, eval_kwargs = _prepare_eval(specs, kwargs)

        if not eval_kwargs:
            prepared_func = partial(func, **raw_kwargs)
        else:
            eval_plan = prepare_eval_plan(eval_kwargs)

            async def prepared_func(ctx: Context) -> _R:
                return await func(
                    ctx, **raw_kwargs, **await async_execute_eval_plan(ctx, eval_plan)
                )

        object.__setattr__(self, "_prepared_func", prepared_func)

    def prepare(self) -> Self:
        return self

    async def __call__(self, ctx: Context) -> _R:
        try:
            return await self._prepared_func(ctx)
        except NodeException:
            raise
        except Exception as e:
            raise ExpressionExceptionRecord(exception_node=self, exception=e) from e


class BaseWrapper(NodeElement):
    pass


class Wrapper(BaseWrapper):
    """
    Abstract base class for WrapperDef and WrapperExec.
    """

    _func: WrapperFunc

    @abstractmethod
    def __call__(
        self,
        ctx: Context,
        wrapped: Node,
        call_next: Callable[[Context], Context],
    ) -> Context:
        pass


class WrapperDef(_DefMixin, Wrapper):
    """
    Mutable definition of a Wrapper.
    """

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

    def prepare(self) -> "WrapperExec":
        return NODE_PREPARE_ENGINE.map(_prepare_map, self)


class WrapperExec(_ExecMixin, Wrapper):
    """
    Immutable execution version of a Wrapper.
    """

    # composed_func signature: (ctx, wrapped, call_next) -> Context
    _prepared_func: Callable[
        [Context, Node, Callable[[Context], Context]],
        Context,
    ]

    def __init__(
        self,
        /,
        *,
        func: WrapperFunc,
        specs: Mapping[str, Spec],
        kwargs: Mapping[str, Any],
    ):
        with enrich_exception(f"in execution initialization for '{func.__name__}'"):
            _validate_kwargs(specs, kwargs)

        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)
        # Optimization: Pre-bind kwargs using partial
        raw_kwargs, eval_kwargs = _prepare_eval(specs, kwargs)

        if not eval_kwargs:
            prepared_func = partial(func, **raw_kwargs)
        else:
            eval_plan = prepare_eval_plan(eval_kwargs)

            def prepared_func(
                ctx: Context,
                wrapped: Node,
                call_next: Callable[[Context], Context],
            ) -> Context:
                return func(
                    ctx,
                    wrapped,
                    call_next,
                    **raw_kwargs,
                    **execute_eval_plan(ctx, eval_plan),
                )

        if Config.check_return_type:
            prepared_func = _ensure_context_return(prepared_func)
        object.__setattr__(self, "_prepared_func", prepared_func)

    def prepare(self) -> Self:
        return self

    def __call__(
        self,
        ctx: Context,
        wrapped: Node,
        call_next: Callable[[Context], Context],
    ) -> Context:
        try:
            return self._prepared_func(ctx, wrapped, call_next)
        except NodeException:
            raise
        except Exception as e:
            raise WrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            ) from e


class AsyncWrapper(BaseWrapper):
    """
    Abstract base class for AsyncWrapperDef and AsyncWrapperExec.
    """

    _func: AsyncWrapperFunc

    @abstractmethod
    async def __call__(
        self,
        ctx: Context,
        wrapped: AsyncNode,
        call_next: Callable[[Context], Awaitable[Context]],
    ) -> Context:
        pass


class AsyncWrapperDef(_DefMixin, AsyncWrapper):
    """
    Mutable definition of an AsyncWrapper.
    """

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

    def prepare(self) -> "AsyncWrapperExec":
        return NODE_PREPARE_ENGINE.map(_prepare_map, self)


class AsyncWrapperExec(_ExecMixin, AsyncWrapper):
    """
    Immutable execution version of an AsyncWrapper.
    """

    _prepared_func: Callable[
        [Context, Node, Callable[[Context], Awaitable[Context]]],
        Awaitable[Context],
    ]

    def __init__(
        self,
        /,
        *,
        func: AsyncWrapperFunc,
        specs: Mapping[str, Spec],
        kwargs: Mapping[str, Any],
    ):
        with enrich_exception(f"in execution initialization for '{func.__name__}'"):
            _validate_kwargs(specs, kwargs)

        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

        raw_kwargs, eval_kwargs = _prepare_eval(specs, kwargs)

        if not eval_kwargs:
            prepared_func = partial(func, **raw_kwargs)
        else:
            eval_plan = prepare_eval_plan(eval_kwargs)

            async def prepared_func(
                ctx: Context,
                wrapped: AsyncNode,
                call_next: Callable[[Context], Awaitable[Context]],
            ) -> Context:
                return await func(
                    ctx,
                    wrapped,
                    call_next,
                    **raw_kwargs,
                    **await async_execute_eval_plan(ctx, eval_plan),
                )

        if Config.check_return_type:
            prepared_func = _ensure_async_context_return(prepared_func)
        object.__setattr__(self, "_prepared_func", prepared_func)

    def prepare(self) -> Self:
        return self

    async def __call__(
        self,
        ctx: Context,
        wrapped: AsyncNode,
        call_next: Callable[[Context], Awaitable[Context]],
    ) -> Context:
        try:
            return await self._prepared_func(ctx, wrapped, call_next)
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


class NodeFactory(BaseFactory, Generic[_P]):
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

    @overload
    def __call__(
        self,
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> NodeDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> NodeDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> NodeDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        scope3: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> NodeDef: ...
    @overload
    def __call__(self, *scopes: Any, **kwargs: Any) -> NodeDef: ...
    def __call__(self, *scopes: Any, **kwargs: Any) -> NodeDef:
        """
        Create the node instance by resolving parameters from scopes and overrides.
        """
        resolved_kwargs = resolve_arguments(self._specs, scopes, kwargs)
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, resolved_kwargs)
        # Note: wrappers are intentionally omitted to avoid parameter conflict.
        # Users should use .add_wrappers() explicitly.
        return NodeDef(func=self._func, specs=self._specs, kwargs=final_kwargs)


class ExpressionFactory(BaseFactory, Generic[_P, _R]):
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

    @overload
    def __call__(
        self,
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> ExpressionDef[_R]: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> ExpressionDef[_R]: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> ExpressionDef[_R]: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        scope3: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> ExpressionDef[_R]: ...
    @overload
    def __call__(self, *scopes: Any, **kwargs: Any) -> ExpressionDef[_R]: ...
    def __call__(self, *scopes: Any, **kwargs: Any) -> ExpressionDef[_R]:
        """
        Create the node instance by resolving parameters from scopes and overrides.
        """
        resolved_kwargs = resolve_arguments(self._specs, scopes, kwargs)
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, resolved_kwargs)
        return ExpressionDef(func=self._func, specs=self._specs, kwargs=final_kwargs)


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

    @overload
    def __call__(
        self,
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> WrapperDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> WrapperDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> WrapperDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        scope3: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> WrapperDef: ...
    @overload
    def __call__(self, *scopes: Any, **kwargs: Any) -> WrapperDef: ...
    def __call__(self, *scopes: Any, **kwargs: Any) -> WrapperDef:
        """
        Create the node instance by resolving parameters from scopes and overrides.
        """
        resolved_kwargs = resolve_arguments(self._specs, scopes, kwargs)
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, resolved_kwargs)
        return WrapperDef(func=self._func, specs=self._specs, kwargs=final_kwargs)


class AsyncNodeFactory(BaseFactory, Generic[_P]):
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

    @overload
    def __call__(
        self,
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncNodeDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncNodeDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncNodeDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        scope3: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncNodeDef: ...
    @overload
    def __call__(self, *scopes: Any, **kwargs: Any) -> AsyncNodeDef: ...
    def __call__(self, *scopes: Any, **kwargs: Any) -> AsyncNodeDef:
        """
        Create the node instance by resolving parameters from scopes and overrides.
        """
        resolved_kwargs = resolve_arguments(self._specs, scopes, kwargs)
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, resolved_kwargs)
        # Note: wrappers are intentionally omitted to avoid parameter conflict.
        # Users should use .add_wrappers() explicitly.
        return AsyncNodeDef(func=self._func, specs=self._specs, kwargs=final_kwargs)


class AsyncExpressionFactory(BaseFactory, Generic[_P, _R]):
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

    @overload
    def __call__(
        self,
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncExpressionDef[_R]: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncExpressionDef[_R]: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncExpressionDef[_R]: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        scope3: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncExpressionDef[_R]: ...
    @overload
    def __call__(self, *scopes: Any, **kwargs: Any) -> AsyncExpressionDef[_R]: ...
    def __call__(self, *scopes: Any, **kwargs: Any) -> AsyncExpressionDef[_R]:
        """
        Create the node instance by resolving parameters from scopes and overrides.
        """
        resolved_kwargs = resolve_arguments(self._specs, scopes, kwargs)
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, resolved_kwargs)
        return AsyncExpressionDef(
            func=self._func, specs=self._specs, kwargs=final_kwargs
        )


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

    @overload
    def __call__(
        self,
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncWrapperDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncWrapperDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncWrapperDef: ...
    @overload
    def __call__(
        self,
        scope1: Mapping[str, Any],
        scope2: Mapping[str, Any],
        scope3: Mapping[str, Any],
        /,
        *_: _P.args,
        **kwargs: _P.kwargs,
    ) -> AsyncWrapperDef: ...
    @overload
    def __call__(self, *scopes: Any, **kwargs: Any) -> AsyncWrapperDef: ...
    def __call__(self, *scopes: Any, **kwargs: Any) -> AsyncWrapperDef:
        """
        Create the node instance by resolving parameters from scopes and overrides.
        """
        resolved_kwargs = resolve_arguments(self._specs, scopes, kwargs)
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, resolved_kwargs)
        return AsyncWrapperDef(func=self._func, specs=self._specs, kwargs=final_kwargs)


def _node(func: NodeFunc[_P], /, *, resolve_type_hints: bool) -> NodeFactory[_P]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return NodeFactory(func, analysis.specs, analysis.public_signature)


@overload
def node(
    func: _Missing = _MISSING, /, *, resolve_type_hints: bool = True
) -> Callable[[NodeFunc[_P]], NodeFactory[_P]]: ...
@overload
def node(
    func: NodeFunc[_P], /, *, resolve_type_hints: bool = True
) -> NodeFactory[_P]: ...
def node(
    func: Union[NodeFunc[_P], _Missing] = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Union[
    Callable[[NodeFunc[_P]], NodeFactory[_P]],
    NodeFactory[_P],
]:
    if func is _MISSING:
        return partial(_node, resolve_type_hints=resolve_type_hints)
    else:
        return _node(func, resolve_type_hints=resolve_type_hints)


def _expression(
    func: ExpressionFunc[_P, _R], /, *, resolve_type_hints: bool
) -> ExpressionFactory[_P, _R]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@expression '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return ExpressionFactory(func, analysis.specs, analysis.public_signature)


@overload
def expression(
    func: _Missing = _MISSING, /, *, resolve_type_hints: bool = True
) -> Callable[[ExpressionFunc[_P, _R]], ExpressionFactory[_P, _R]]: ...
@overload
def expression(
    func: ExpressionFunc[_P, _R], /, *, resolve_type_hints: bool = True
) -> ExpressionFactory[_P, _R]: ...
def expression(
    func: Union[ExpressionFunc[_P, _R], _Missing] = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Union[
    Callable[[ExpressionFunc[_P, _R]], ExpressionFactory[_P, _R]],
    ExpressionFactory[_P, _R],
]:
    if func is _MISSING:
        return partial(_expression, resolve_type_hints=resolve_type_hints)
    else:
        return _expression(func, resolve_type_hints=resolve_type_hints)


def _wrapper(
    func: WrapperFunc[_P], /, *, resolve_type_hints: bool
) -> WrapperFactory[_P]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    # Wrapper signature: (ctx, wrapped, call_next, **kwargs)
    # The first 3 arguments should be positional-only.
    if len(analysis.pos_only_params) != 3:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 3 positional-only arguments (ctx, wrapped, call_next), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return WrapperFactory(func, analysis.specs, analysis.public_signature)


@overload
def wrapper(
    func: _Missing = _MISSING, /, *, resolve_type_hints: bool = True
) -> Callable[[WrapperFunc[_P]], WrapperFactory[_P]]: ...
@overload
def wrapper(
    func: WrapperFunc[_P], /, *, resolve_type_hints: bool = True
) -> WrapperFactory[_P]: ...
def wrapper(
    func: Union[WrapperFunc[_P], _Missing] = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Union[
    Callable[[WrapperFunc[_P]], WrapperFactory[_P]],
    WrapperFactory[_P],
]:
    if func is _MISSING:
        return partial(_wrapper, resolve_type_hints=resolve_type_hints)
    else:
        return _wrapper(func, resolve_type_hints=resolve_type_hints)


def _async_node(
    func: AsyncNodeFunc[_P], /, *, resolve_type_hints: bool
) -> AsyncNodeFactory[_P]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@async_node '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return AsyncNodeFactory(func, analysis.specs, analysis.public_signature)


@overload
def async_node(
    func: _Missing = _MISSING, /, *, resolve_type_hints: bool = True
) -> Callable[[AsyncNodeFunc[_P]], AsyncNodeFactory[_P]]: ...
@overload
def async_node(
    func: AsyncNodeFunc[_P], /, *, resolve_type_hints: bool = True
) -> AsyncNodeFactory[_P]: ...
def async_node(
    func: Union[AsyncNodeFunc[_P], _Missing] = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Union[
    Callable[[AsyncNodeFunc[_P]], AsyncNodeFactory[_P]],
    AsyncNodeFactory[_P],
]:
    if func is _MISSING:
        return partial(_async_node, resolve_type_hints=resolve_type_hints)
    else:
        return _async_node(func, resolve_type_hints=resolve_type_hints)


def _async_expression(
    func: AsyncExpressionFunc[_P, _R], /, *, resolve_type_hints: bool
) -> AsyncExpressionFactory[_P, _R]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@async_expression '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return AsyncExpressionFactory(func, analysis.specs, analysis.public_signature)


@overload
def async_expression(
    func: _Missing = _MISSING, /, *, resolve_type_hints: bool = True
) -> Callable[[AsyncExpressionFunc[_P, _R]], AsyncExpressionFactory[_P, _R]]: ...
@overload
def async_expression(
    func: AsyncExpressionFunc[_P, _R], /, *, resolve_type_hints: bool = True
) -> AsyncExpressionFactory[_P, _R]: ...
def async_expression(
    func: Union[AsyncExpressionFunc[_P, _R], _Missing] = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Union[
    Callable[[AsyncExpressionFunc[_P, _R]], AsyncExpressionFactory[_P, _R]],
    AsyncExpressionFactory[_P, _R],
]:
    if func is _MISSING:
        return partial(_async_expression, resolve_type_hints=resolve_type_hints)
    else:
        return _async_expression(func, resolve_type_hints=resolve_type_hints)


def _async_wrapper(
    func: AsyncWrapperFunc[_P], /, *, resolve_type_hints: bool
) -> AsyncWrapperFactory[_P]:
    analysis = analyze_signature(func, resolve_type_hints=resolve_type_hints)
    if len(analysis.pos_only_params) != 3:
        raise TypeError(
            f"@async_wrapper '{func.__name__}' requires exactly 3 positional-only arguments (ctx, wrapped, call_next), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return AsyncWrapperFactory(func, analysis.specs, analysis.public_signature)


@overload
def async_wrapper(
    func: _Missing = _MISSING, /, *, resolve_type_hints: bool = True
) -> Callable[[AsyncWrapperFunc[_P]], AsyncWrapperFactory[_P]]: ...
@overload
def async_wrapper(
    func: AsyncWrapperFunc[_P], /, *, resolve_type_hints: bool = True
) -> AsyncWrapperFactory[_P]: ...
def async_wrapper(
    func: Union[AsyncWrapperFunc[_P], _Missing] = _MISSING,
    /,
    *,
    resolve_type_hints: bool = True,
) -> Union[
    Callable[[AsyncWrapperFunc[_P]], AsyncWrapperFactory[_P]],
    AsyncWrapperFactory[_P],
]:
    if func is _MISSING:
        return partial(_async_wrapper, resolve_type_hints=resolve_type_hints)
    else:
        return _async_wrapper(func, resolve_type_hints=resolve_type_hints)


from .tree import NODE_ENGINE, NODE_PREPARE_ENGINE
from .render import get_render_string
from .eval import prepare_eval_plan, execute_eval_plan, async_execute_eval_plan
