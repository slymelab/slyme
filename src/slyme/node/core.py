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
)
from typing_extensions import ParamSpec, Concatenate, Self
from slyme.utils.exception import enrich_exception
from slyme.context import Context
from .exception import (
    NodeTerminate,
    NodeExceptionRecord,
    NodeException,
    NodeExpressionExceptionRecord,
    NodeWrapperExceptionRecord,
)
from .signature import (
    Spec,
    process_kwargs,
    analyze_signature,
    SignatureAnalysis,
    resolve_arguments,
)

__all__ = [
    "node",
    "expression",
    "wrapper",
    "NodeElement",
    "Node",
    "NodeDef",
    "NodeExec",
    "NodeExpression",
    "NodeExpressionDef",
    "NodeExpressionExec",
    "NodeWrapper",
    "NodeWrapperDef",
    "NodeWrapperExec",
    "NODE_PYTREE_ENGINE",
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
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


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

    def __getitem__(self, key: str) -> Any:
        return self._kwargs[key]

    def __repr__(self) -> str:
        return get_render_string(self)

    def extra_repr(self) -> str:
        return ""

    def type_repr(self) -> str:
        return self._func.__name__


class Node(NodeElement):
    """
    Abstract base class for NodeDef and NodeExec.
    """

    _func: NodeFunc
    wrappers: Sequence["NodeWrapper"]  # Changed: Union[...] -> Sequence

    @abstractmethod
    def __call__(self, ctx: Context) -> Context:
        pass


class NodeDef(Node):
    """
    Mutable definition of a Node. Allows modification during build time.
    """

    _kwargs: dict[str, Any]
    wrappers: list["NodeWrapper"]

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Optional[Iterable["NodeWrapper"]] = None,
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "wrappers", list(wrappers) if wrappers else [])
        object.__setattr__(self, "_kwargs", kwargs)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        spec_obj = self._specs[key]
        with enrich_exception(f"for parameter '{key}'"):
            value = spec_obj.resolve(value)
        self._kwargs[key] = value

    def add_wrappers(self, *wrappers: "NodeWrapper") -> Self:
        self.wrappers.extend(wrappers)
        return self

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __call__(self, ctx: Context) -> Context:
        raise RuntimeError(
            f"Cannot execute {type(self).__name__}. "
            f"Please call `.prepare()` to obtain a generic `{NodeExec.__name__}` first."
        )

    def prepare(self) -> "NodeExec":
        # Use the specialized NODE_PREPARE_PYTREE_ENGINE to perform a deep transform
        # of the structure (List -> Tuple, Dict -> MappingProxy, Def -> Exec).
        # We map strict identity because the transformation happens in the 'unflatten' phase
        # of the registered types in NODE_PREPARE_PYTREE_ENGINE.
        return NODE_PREPARE_PYTREE_ENGINE.map(lambda x: x, self)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"Cannot delete attribute '{name}' on {type(self).__name__}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "wrappers":
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(
                f"Cannot set attribute '{name}' on {type(self).__name__}. "
                "Only 'wrappers' can be modified."
            )


class NodeExec(Node):
    """
    Immutable execution version of a Node.
    """

    # composed_func signature: (Context) -> Context
    _prepared_func: Callable[[Context], Context]
    wrappers: tuple["NodeWrapper", ...]

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Iterable["NodeWrapper"],
        kwargs: Mapping[str, Any],
    ):
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
        chain: Callable[[Context], Context] = partial(func, **kwargs)
        # 2. Build the middleware chain.
        # Wrappers are applied from inside out (reversed order of list).
        for wrapper in reversed(wrappers):
            # wrapper is NodeWrapperExec which is callable: (ctx, wrapped, call_next) -> Context
            # We partially apply `wrapped` (self) and `call_next` (current chain head)
            # to create the new chain head: (Context) -> Context
            chain = partial(wrapper, wrapped=self, call_next=chain)
        object.__setattr__(self, "_prepared_func", chain)

    def prepare(self) -> Self:
        return self

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(
            f"{type(self).__name__} is immutable and does not support item assignment."
        )

    def __delattr__(self, name: str) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __call__(self, ctx: Context) -> Context:
        try:
            # Execute the pre-composed chain
            return self._prepared_func(ctx)
        # Node Interrupts
        except (NodeTerminate, NodeExpressionExceptionRecord) as e:
            if e.source_node is None:
                e.source_node = self
            raise
        # Direct Node Exceptions
        except NodeException:
            raise
        # General Exceptions
        except Exception as e:
            raise NodeExceptionRecord(exception_node=self, exception=e)


class NodeExpression(NodeElement, Generic[_R]):
    """
    Abstract base class for NodeExpressionDef and NodeExpressionExec.
    """

    _func: ExpressionFunc

    @abstractmethod
    def __call__(self, ctx: Context) -> _R:
        pass


class NodeExpressionDef(NodeExpression[_R]):
    """
    Mutable definition of a NodeExpression.
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

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        spec_obj = self._specs[key]
        with enrich_exception(f"for parameter '{key}'"):
            value = spec_obj.resolve(value)
        self._kwargs[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __call__(self, ctx: Context) -> _R:
        raise RuntimeError(
            f"Cannot execute {type(self).__name__}. "
            f"Please call `.prepare()` to obtain a generic `{NodeExpressionExec.__name__}` first."
        )

    def prepare(self) -> "NodeExpressionExec[_R]":
        return NODE_PREPARE_PYTREE_ENGINE.map(lambda x: x, self)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"Cannot delete attribute '{name}' on {type(self).__name__}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot set attribute '{name}' on {type(self).__name__}. "
            "Internal structure is protected."
        )


class NodeExpressionExec(NodeExpression[_R]):
    """
    Immutable execution version of a NodeExpression.
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
        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)
        # Optimization: Pre-bind kwargs using partial
        object.__setattr__(self, "_prepared_func", partial(func, **kwargs))

    def prepare(self) -> Self:
        return self

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(
            f"{type(self).__name__} is immutable and does not support item assignment."
        )

    def __delattr__(self, name: str) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __call__(self, ctx: Context) -> _R:
        try:
            return self._prepared_func(ctx)
        except NodeException:
            raise
        except Exception as e:
            raise NodeExpressionExceptionRecord(exception_node=self, exception=e)


class NodeWrapper(NodeElement):
    """
    Abstract base class for NodeWrapperDef and NodeWrapperExec.
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


class NodeWrapperDef(NodeWrapper):
    """
    Mutable definition of a NodeWrapper.
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

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        spec_obj = self._specs[key]
        with enrich_exception(f"for parameter '{key}'"):
            value = spec_obj.resolve(value)
        self._kwargs[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __call__(
        self,
        ctx: Context,
        wrapped: Node,
        call_next: Callable[[Context], Context],
    ) -> Context:
        raise RuntimeError(
            f"Cannot execute {type(self).__name__}. "
            f"Please call `.prepare()` to obtain a generic `{NodeWrapperExec.__name__}` first."
        )

    def prepare(self) -> "NodeWrapperExec":
        return NODE_PREPARE_PYTREE_ENGINE.map(lambda x: x, self)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"Cannot delete attribute '{name}' on {type(self).__name__}"
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot set attribute '{name}' on {type(self).__name__}. "
            "Internal structure is protected."
        )


class NodeWrapperExec(NodeWrapper):
    """
    Immutable execution version of a NodeWrapper.
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
        if not isinstance(kwargs, types.MappingProxyType):
            kwargs = types.MappingProxyType(kwargs)
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)
        # Optimization: Pre-bind kwargs using partial
        object.__setattr__(self, "_prepared_func", partial(func, **kwargs))

    def prepare(self) -> Self:
        return self

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(
            f"{type(self).__name__} is immutable and does not support item assignment."
        )

    def __delattr__(self, name: str) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

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
            raise NodeWrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            )


# Functional Factory & Decorators
class NodeFactory(Generic[_P]):
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

    def __repr__(self) -> str:
        return f"<NodeFactory of {self._func.__name__}>"

    def __call__(self, *_: _P.args, **kwargs: _P.kwargs) -> NodeDef:
        """
        Create the node instance using keyword arguments.
        """
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, kwargs)
        # Note: wrappers are intentionally omitted to avoid parameter conflict.
        # Users should use .add_wrappers() explicitly.
        return NodeDef(func=self._func, specs=self._specs, kwargs=final_kwargs)

    @overload
    def create(self, /, *_: _P.args, **overrides: _P.kwargs) -> NodeDef: ...
    @overload
    def create(self, /, *sources: Mapping[str, Any], **overrides: Any) -> NodeDef: ...
    def create(self, /, *sources: Mapping[str, Any], **overrides: Any) -> NodeDef:
        """
        Create the node instance by resolving parameters from sources and overrides.
        """
        final_kwargs = resolve_arguments(self._specs, sources, overrides)
        return self(**final_kwargs)


class NodeExpressionFactory(Generic[_P, _R]):
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

    def __repr__(self) -> str:
        return f"<NodeExpressionFactory of {self._func.__name__}>"

    def __call__(self, *_: _P.args, **kwargs: _P.kwargs) -> NodeExpressionDef[_R]:
        """
        Create the node instance using keyword arguments.
        """
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, kwargs)
        return NodeExpressionDef(
            func=self._func, specs=self._specs, kwargs=final_kwargs
        )

    @overload
    def create(
        self, /, *_: _P.args, **overrides: _P.kwargs
    ) -> NodeExpressionDef[_R]: ...
    @overload
    def create(
        self, /, *sources: Mapping[str, Any], **overrides: Any
    ) -> NodeExpressionDef[_R]: ...
    def create(
        self, /, *sources: Mapping[str, Any], **overrides: Any
    ) -> NodeExpressionDef[_R]:
        """
        Create the node instance by resolving parameters from sources and overrides.
        """
        final_kwargs = resolve_arguments(self._specs, sources, overrides)
        return self(**final_kwargs)


class NodeWrapperFactory(Generic[_P]):
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

    def __repr__(self) -> str:
        return f"<NodeWrapperFactory of {self._func.__name__}>"

    def __call__(self, *_: _P.args, **kwargs: _P.kwargs) -> NodeWrapperDef:
        """
        Create the node instance using keyword arguments.
        """
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = process_kwargs(self._specs, kwargs)
        return NodeWrapperDef(func=self._func, specs=self._specs, kwargs=final_kwargs)

    @overload
    def create(self, /, *_: _P.args, **overrides: _P.kwargs) -> NodeWrapperDef: ...
    @overload
    def create(
        self, /, *sources: Mapping[str, Any], **overrides: Any
    ) -> NodeWrapperDef: ...
    def create(
        self, /, *sources: Mapping[str, Any], **overrides: Any
    ) -> NodeWrapperDef:
        """
        Create the node instance by resolving parameters from sources and overrides.
        """
        final_kwargs = resolve_arguments(self._specs, sources, overrides)
        return self(**final_kwargs)


def _node(func: NodeFunc[_P], /) -> NodeFactory[_P]:
    analysis = analyze_signature(func)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return NodeFactory(func, analysis.specs, analysis.public_signature)


@overload
def node(func: _Missing = _MISSING, /) -> Callable[[NodeFunc[_P]], NodeFactory[_P]]: ...
@overload
def node(func: NodeFunc[_P], /) -> NodeFactory[_P]: ...
def node(func: Union[NodeFunc[_P], _Missing] = _MISSING, /) -> Union[
    Callable[[NodeFunc[_P]], NodeFactory[_P]],
    NodeFactory[_P],
]:
    if func is _MISSING:
        return partial(_node)
    else:
        return _node(func)


def _expression(func: ExpressionFunc[_P, _R], /) -> NodeExpressionFactory[_P, _R]:
    analysis = analyze_signature(func)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@expression '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return NodeExpressionFactory(func, analysis.specs, analysis.public_signature)


@overload
def expression(
    func: _Missing = _MISSING, /
) -> Callable[[ExpressionFunc[_P, _R]], NodeExpressionFactory[_P, _R]]: ...
@overload
def expression(func: ExpressionFunc[_P, _R], /) -> NodeExpressionFactory[_P, _R]: ...
def expression(func: Union[ExpressionFunc[_P, _R], _Missing] = _MISSING, /) -> Union[
    Callable[[ExpressionFunc[_P, _R]], NodeExpressionFactory[_P, _R]],
    NodeExpressionFactory[_P, _R],
]:
    if func is _MISSING:
        return partial(_expression)
    else:
        return _expression(func)


def _wrapper(func: WrapperFunc[_P], /) -> NodeWrapperFactory[_P]:
    analysis = analyze_signature(func)
    # Wrapper signature: (ctx, wrapped, call_next, **kwargs)
    # The first 3 arguments should be positional-only.
    if len(analysis.pos_only_params) != 3:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 3 positional-only arguments (ctx, wrapped, call_next), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return NodeWrapperFactory(func, analysis.specs, analysis.public_signature)


@overload
def wrapper(
    func: _Missing = _MISSING, /
) -> Callable[[WrapperFunc[_P]], NodeWrapperFactory[_P]]: ...
@overload
def wrapper(func: WrapperFunc[_P], /) -> NodeWrapperFactory[_P]: ...
def wrapper(func: Union[WrapperFunc[_P], _Missing] = _MISSING, /) -> Union[
    Callable[[WrapperFunc[_P]], NodeWrapperFactory[_P]],
    NodeWrapperFactory[_P],
]:
    if func is _MISSING:
        return partial(_wrapper)
    else:
        return _wrapper(func)


from .tree import NODE_PYTREE_ENGINE, NODE_PREPARE_PYTREE_ENGINE
from .render import get_render_string
