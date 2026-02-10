"""
Core node module, consolidating base definitions and functional APIs.
"""

import inspect
import types
from abc import abstractmethod
from enum import Enum
from functools import partial, update_wrapper
from contextlib import contextmanager, AbstractContextManager, ExitStack
from dataclasses import dataclass, field
from typing import (
    TypeVar,
    Callable,
    Any,
    Generator,
    Union,
    overload,
    Annotated,
    get_type_hints,
    get_origin,
    get_args,
    Mapping,
    Optional,
    Generic,
    Iterable,
    cast,
)
from typing_extensions import ParamSpec, Concatenate, Self
from slyme.utils.common import enrich_exception
from slyme.utils.protocol import HasExtraRepr, HasTypeRepr
from slyme.utils.registry import TypeRegistry
from slyme.utils.pytree import (
    PyTreeEngine,
    PYTREE_ENGINE_REGISTRY,
    PyTreeAux,
    AttributeKey,
    MappingKey,
)
from slyme.context import Context, Ref, DEP, Dep
from .exception import (
    NodeTerminate,
    NodeExceptionRecord,
    NodeException,
    NodeExpressionExceptionRecord,
    NodeWrapperExceptionRecord,
)
from .render import get_render_string
from .validator import check_node_structure, DEPENDENCY_REGISTRY

__all__ = [
    "spec",
    "ref_spec",
    "node",
    "expression",
    "wrapper",
    "NodeElement",
    "Node",
    "NodeExpression",
    "NodeWrapper",
    "STOP",
    "NODE_PYTREE_ENGINE",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T = TypeVar("_T")
NodeFunc = Callable[Concatenate[Context, _P], None]
ExpressionFunc = Callable[Concatenate[Context, _P], _R]
WrapperFunc = Callable[Concatenate[Context, "Node", _P], Generator[Any, None, None]]
WrapperCMFactory = Callable[
    Concatenate[Context, "Node", _P], AbstractContextManager[Any]
]

_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
Stop = Enum("Stop", ["MARK"])
STOP = Stop.MARK


# --- Spec Definitions ---


@dataclass(frozen=True)
class Spec:
    """
    Dependency injection metadata for functional node parameters.
    """

    default: Union[Any, _Missing] = _MISSING
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING

    def __post_init__(self):
        if self.default is not _MISSING and self.default_factory is not _MISSING:
            raise ValueError(
                "Cannot specify both `default` and `default_factory` in Spec."
            )

    def resolve(self, value: Any = _MISSING) -> Any:
        """
        Resolve the final value for the parameter.
        """
        if value is not _MISSING:
            return value
        if self.default is not _MISSING:
            return self.default
        if self.default_factory is not _MISSING:
            return self.default_factory()
        raise ValueError("Missing required parameter.")


@dataclass(frozen=True)
class RefSpec(Spec):
    """
    Specialized Spec for Ref parameters, allowing metadata injection and type enforcement.
    """

    metadata: Optional[Mapping[str, Any]] = None

    def __post_init__(self):
        super().__post_init__()
        if self.metadata is not None:
            object.__setattr__(self, "metadata", types.MappingProxyType(self.metadata))

    def resolve(self, value: Union[_Missing, Ref] = _MISSING) -> Ref:
        # 1. Resolve the value using the base Spec logic (handling defaults)
        value = super().resolve(value)
        # 2. Type check: Ensure the resolved value is strictly a Ref
        if not isinstance(value, Ref):
            raise TypeError(
                f"The resolved value for this parameter must be an instance of 'Ref', "
                f"but got '{type(value).__name__}'."
            )
        # 3. Metadata injection: Update the Ref's metadata if specified in Spec
        if self.metadata is not None:
            return value.update_metadata(self.metadata)
        return value


# --- Spec Factory Functions ---


def spec(
    default: Union[Any, _Missing] = _MISSING,
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING,
) -> Any:
    """
    Factory function for creating a _Spec instance.
    Returns Any to bypass type checker errors when assigned as a default value.
    """
    return Spec(default=default, default_factory=default_factory)


def ref_spec(
    default: Union[Any, _Missing] = _MISSING,
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Any:
    """
    Factory function for creating a _RefSpec instance.
    Returns Any to bypass type checker errors when assigned as a default value.
    """
    return RefSpec(default=default, default_factory=default_factory, metadata=metadata)


# --- Inspection & Signature Analysis ---


@dataclass(frozen=True)
class _SignatureAnalysis:
    pos_only_params: list[inspect.Parameter]
    public_signature: inspect.Signature
    specs: Mapping[str, Spec]


def _resolve_spec(param: inspect.Parameter, hint: Any) -> Spec:
    spec_obj: Union[_Missing, Spec] = _MISSING
    # 1. Check for Annotated
    if get_origin(hint) is Annotated:
        args = get_args(hint)
        if len(args) != 2:
            raise TypeError(
                f"Invalid Annotated metadata for parameter '{param.name}'."
                f"Currently, only a single `Spec` metadata is allowed, but found {len(args) - 1} items."
            )
        candidate = args[1]
        if not isinstance(candidate, Spec):
            raise TypeError(
                f"Invalid Annotated metadata for parameter '{param.name}'."
                f"Expected explicit `Spec` instance, but got {type(candidate).__name__}. "
                f"Other metadata types are strictly forbidden (for now)."
            )
        spec_obj = candidate

    # 2. Conflict Check and Collection
    if spec_obj is not _MISSING:
        # If Spec is defined in Annotated, strictly forbid standard default values.
        if param.default is not inspect.Parameter.empty:
            raise TypeError(
                f"Parameter '{param.name}' has a semantic conflict. "
                f"It defines a `Spec` in `Annotated` but also has a standard default value. "
                f"Please remove the standard default value assignment."
            )
        return spec_obj
    elif isinstance(param.default, Spec):
        # Spec is specified through func default value.
        return param.default
    elif param.default is not inspect.Parameter.empty:
        # Create a new Spec using default value.
        return Spec(default=param.default)
    else:
        # Return an empty Spec for standard keyword arguments
        return Spec()


def _analyze_signature(func: Callable) -> _SignatureAnalysis:
    """
    Analyze the function signature to separate runtime parameters and config parameters.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    pos_only_params = []
    public_params = []  # Used for factory signature
    specs: dict[str, Spec] = {}

    type_hints = get_type_hints(func, include_extras=True)

    for p in params:
        with enrich_exception(f"in definition of '{func.__name__}'"):
            if p.kind == inspect.Parameter.POSITIONAL_ONLY:
                pos_only_params.append(p)
            elif p.kind == inspect.Parameter.KEYWORD_ONLY:
                public_params.append(p)
                # Spec Resolution Logic: Always returns a Spec object now
                specs[p.name] = _resolve_spec(p, type_hints.get(p.name))
            else:
                kind_name = str(p.kind)
                raise TypeError(
                    f"Invalid parameter '{p.name}' of kind {kind_name}. "
                    f"Functional nodes strict rules:\n"
                    f"  1. Runtime args (e.g. ctx) must be POSITIONAL_ONLY (before '/').\n"
                    f"  2. Config args must be KEYWORD_ONLY (after '*')."
                )

    public_signature = sig.replace(parameters=public_params)
    return _SignatureAnalysis(
        pos_only_params=pos_only_params,
        public_signature=public_signature,
        specs=types.MappingProxyType(specs),
    )


def _process_kwargs(
    specs: Mapping[str, Spec],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and process kwargs:
    1. Check for unexpected arguments.
    2. Check for missing required arguments.
    3. Inject Spec-resolved values.
    """
    allowed_names = set(specs.keys())
    input_names = set(kwargs.keys())

    # 1. Strict Subset Check
    unknown_args = input_names - allowed_names
    if unknown_args:
        raise TypeError(
            f"Got unexpected keyword argument(s) {list(unknown_args)}. "
            f"Allowed arguments: {list(allowed_names)}."
        )

    # 2. Apply Specs
    final_kwargs = {}
    for name, spec_obj in specs.items():
        value = kwargs.get(name, _MISSING)
        with enrich_exception(f"for parameter '{name}'"):
            value = spec_obj.resolve(value)
        final_kwargs[name] = value

    return final_kwargs


# --- Node Class Definitions ---


class NodeElement:
    """
    Base class for all node-related entities.
    Constructed directly via factory functions.
    """

    def __init__(
        self,
        /,
        *,
        func: Callable,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        self.func = func
        self.specs = specs
        # NOTE: kwargs must already be processed/resolved by _process_kwargs
        self.kwargs = kwargs

    def __getitem__(self, key: str) -> Any:
        return self.kwargs[key]

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self.specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        self.kwargs[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __repr__(self) -> str:
        return get_render_string(self)

    def extra_repr(self) -> str:
        return ""

    def type_repr(self) -> str:
        return self.func.__name__


class Node(NodeElement):
    """
    Standard executable Node.
    """

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        node_wrappers: Optional[Iterable["NodeWrapper"]] = None,
        kwargs: dict[str, Any],
    ):
        super().__init__(func=func, specs=specs, kwargs=kwargs)
        self.node_wrappers = list(node_wrappers) if node_wrappers is not None else []

    def execute(self, ctx: Context, /) -> None:
        """
        Directly executes the wrapped function with stored kwargs.
        """
        self.func(ctx, **self.kwargs)

    def add_wrappers(self, *wrappers: "NodeWrapper") -> Self:
        self.node_wrappers.extend(wrappers)
        return self

    def __call__(self, ctx: Context, /) -> None:
        """
        Outer execute API with wrapper handling.
        """
        try:
            with ExitStack() as stack:
                should_stop = False
                for wrapper in self.node_wrappers:
                    val = stack.enter_context(wrapper(ctx, self))
                    if val is STOP:
                        should_stop = True
                        break
                if not should_stop:
                    # Inline execution to reduce stack depth
                    self.func(ctx, **self.kwargs)
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
    Node that returns a value.
    """

    def __init__(
        self,
        /,
        *,
        func: ExpressionFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        super().__init__(func=func, specs=specs, kwargs=kwargs)

    def evaluate(self, ctx: Context, /) -> _R:
        return self.func(ctx, **self.kwargs)

    def __call__(self, ctx: Context, /) -> _R:
        try:
            # Inline execution
            return self.func(ctx, **self.kwargs)
        except NodeException:
            raise
        except Exception as e:
            raise NodeExpressionExceptionRecord(exception_node=self, exception=e)


class NodeWrapper(NodeElement):
    """
    Auxiliary wrapper logic for Nodes.
    """

    def __init__(
        self,
        /,
        *,
        func: WrapperFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        super().__init__(func=func, specs=specs, kwargs=kwargs)
        self.cm_factory = contextmanager(func)

    @contextmanager
    def wrap(self, ctx: Context, wrapped: Node, /) -> Generator:
        with self.cm_factory(ctx, wrapped, **self.kwargs) as val:
            yield val

    @contextmanager
    def __call__(self, ctx: Context, wrapped: Node, /) -> Generator:
        try:
            # Inline context manager usage
            with self.cm_factory(ctx, wrapped, **self.kwargs) as val:
                yield val
        except NodeException:
            raise
        except Exception as e:
            raise NodeWrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            )


# --- PyTree Engine Configuration ---
NODE_PYTREE_ENGINE = PyTreeEngine("node_engine")
PYTREE_ENGINE_REGISTRY.register(NODE_PYTREE_ENGINE, key="node_engine")


def _flatten_node_element(obj: NodeElement) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Generic flatten for NodeElement (NodeWrapper, NodeExpression).
    Expands kwargs as children.
    """
    keys = tuple(obj.kwargs.keys())
    children = tuple(obj.kwargs.values())
    rich_keys = tuple(MappingKey(k) for k in keys)

    metadata = {"func": obj.func, "specs": obj.specs}

    return children, PyTreeAux(keys=rich_keys, metadata=metadata, cls=type(obj))


def _unflatten_node_element(children: Iterable[Any], aux: PyTreeAux) -> Any:
    """
    Generic unflatten for NodeElement.
    """
    if aux.cls is None or aux.keys is None:
        raise ValueError("Missing info in PyTreeAux for NodeElement unflattening.")

    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))

    return aux.cls(
        func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs
    )


def _flatten_node(obj: Node) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Specific flatten for Node.
    Expands node_wrappers + kwargs.
    """
    # 1. Wrappers
    children = [obj.node_wrappers]
    rich_keys = [AttributeKey("node_wrappers")]

    # 2. Kwargs
    for k, v in obj.kwargs.items():
        children.append(v)
        rich_keys.append(MappingKey(k))

    metadata = {"func": obj.func, "specs": obj.specs}

    return tuple(children), PyTreeAux(
        keys=tuple(rich_keys), metadata=metadata, cls=Node
    )


def _unflatten_node(children: Iterable[Any], aux: PyTreeAux) -> Any:
    """
    Specific unflatten for Node.
    """
    children_iter = iter(children)
    keys_iter = iter(aux.keys)

    # 1. Wrappers
    _ = next(keys_iter)  # AttributeKey("node_wrappers")
    node_wrappers = next(children_iter)

    # 2. Kwargs
    kwargs = {}
    for key in keys_iter:
        raw_key = cast("MappingKey", key).key
        val = next(children_iter)
        kwargs[raw_key] = val

    return Node(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        node_wrappers=node_wrappers,
        kwargs=kwargs,
    )


# Register strictly (no inheritance, specific handlers)
NODE_PYTREE_ENGINE.register(Node, _flatten_node, _unflatten_node, strict=True)
# NodeElement logic serves NodeExpression and NodeWrapper
NODE_PYTREE_ENGINE.register(
    NodeExpression, _flatten_node_element, _unflatten_node_element, strict=True
)
NODE_PYTREE_ENGINE.register(
    NodeWrapper, _flatten_node_element, _unflatten_node_element, strict=True
)


# --- Functional Factory & Decorators ---


class _FunctionalFactory(Generic[_T, _P]):
    """
    Factory class for creating functional node instances.
    """

    def __init__(self, cls: type[_T], analysis: _SignatureAnalysis) -> None:
        update_wrapper(
            self, analysis.specs
        )  # Use any object, just to hold context if needed, but really we want to wrap the original func which isn't available here directly in init params if we don't pass it.
        # Actually we need to wrap the factory with the original function's metadata
        # But here we don't have 'func' in signature.
        # Let's assume the decorator handles the wrapping, or we pass func in.
        # To match previous logic, we rely on the caller to update_wrapper if needed
        # OR we change signature to take `func`.
        # The previous code did: update_wrapper(self, config.func).
        # We'll adapt:
        self._cls = cls
        self._analysis = analysis
        self.__signature__ = analysis.public_signature

    def _create(self, **kwargs) -> _T:
        # Common creation logic
        # 1. Validate & Apply Specs
        # We need the function object. It's not in analysis.
        # Let's assume we need to change __init__ to accept func.
        raise NotImplementedError("Should not be called directly without func context.")

    # We need to restructure this slightly to hold the func.
    # Re-defining __init__ to be cleaner.


class FunctionalFactory(Generic[_T, _P]):
    def __init__(self, cls: type[_T], func: Callable, analysis: _SignatureAnalysis):
        update_wrapper(self, func)
        self._cls = cls
        self._func = func
        self._specs = analysis.specs
        self.__signature__ = analysis.public_signature

    def __call__(self, *_: _P.args, **kwargs: _P.kwargs) -> _T:
        """
        Create the node instance using keyword arguments.
        """
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = _process_kwargs(self._specs, kwargs)

        # Determine extra args based on class
        extra_args = {}
        if self._cls is Node and "node_wrappers" in final_kwargs:
            extra_args["node_wrappers"] = final_kwargs.pop("node_wrappers")

        return self._cls(
            func=self._func, specs=self._specs, kwargs=final_kwargs, **extra_args
        )

    @overload
    def bind(self, /, *_: _P.args, **overrides: _P.kwargs) -> _T: ...
    @overload
    def bind(self, /, *scopes: Mapping[str, Any], **overrides: Any) -> _T: ...
    def bind(self, /, *scopes: Mapping[str, Any], **overrides: Any) -> _T:
        """
        Create the node instance by binding parameters from scopes and overrides.
        """
        # 1. Start with explicit overrides
        final_kwargs = dict(overrides)

        # 2. Iterate through required parameters defined in the specs
        for name in self._specs.keys():
            if name in final_kwargs:
                continue
            # Look in scopes (reverse order)
            for scope in reversed(scopes):
                if name in scope:
                    final_kwargs[name] = scope[name]
                    break

        return self(**final_kwargs)


def _node(func: NodeFunc[_P], /) -> FunctionalFactory[Node, _P]:
    analysis = _analyze_signature(func)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return FunctionalFactory(Node, func, analysis)


@overload
def node(
    func: _Missing = _MISSING, /
) -> Callable[[NodeFunc[_P]], FunctionalFactory[Node, _P]]: ...
@overload
def node(func: NodeFunc[_P], /) -> FunctionalFactory[Node, _P]: ...
def node(
    func: Union[NodeFunc[_P], _Missing] = _MISSING, /
) -> Union[
    Callable[[NodeFunc[_P]], FunctionalFactory[Node, _P]], FunctionalFactory[Node, _P]
]:
    if func is _MISSING:
        return partial(_node)
    else:
        return _node(func)


def _expression(
    func: ExpressionFunc[_P, _R], /
) -> FunctionalFactory[NodeExpression[_R], _P]:
    analysis = _analyze_signature(func)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@expression '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return FunctionalFactory(NodeExpression, func, analysis)


@overload
def expression(
    func: _Missing = _MISSING, /
) -> Callable[[ExpressionFunc[_P, _R]], FunctionalFactory[NodeExpression[_R], _P]]: ...
@overload
def expression(
    func: ExpressionFunc[_P, _R], /
) -> FunctionalFactory[NodeExpression[_R], _P]: ...
def expression(func: Union[ExpressionFunc[_P, _R], _Missing] = _MISSING, /) -> Union[
    Callable[[ExpressionFunc[_P, _R]], FunctionalFactory[NodeExpression[_R], _P]],
    FunctionalFactory[NodeExpression[_R], _P],
]:
    if func is _MISSING:
        return partial(_expression)
    else:
        return _expression(func)


def _wrapper(func: WrapperFunc[_P], /) -> FunctionalFactory[NodeWrapper, _P]:
    analysis = _analyze_signature(func)
    if len(analysis.pos_only_params) != 2:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 2 positional-only arguments (ctx, wrapped), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return FunctionalFactory(NodeWrapper, func, analysis)


@overload
def wrapper(
    func: _Missing = _MISSING, /
) -> Callable[[WrapperFunc[_P]], FunctionalFactory[NodeWrapper, _P]]: ...
@overload
def wrapper(func: WrapperFunc[_P], /) -> FunctionalFactory[NodeWrapper, _P]: ...
def wrapper(func: Union[WrapperFunc[_P], _Missing] = _MISSING, /) -> Union[
    Callable[[WrapperFunc[_P]], FunctionalFactory[NodeWrapper, _P]],
    FunctionalFactory[NodeWrapper, _P],
]:
    if func is _MISSING:
        return partial(_wrapper)
    else:
        return _wrapper(func)
