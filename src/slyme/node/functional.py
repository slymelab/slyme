"""
Functional API for slyme nodes.
"""

import inspect
import types
from enum import Enum
from functools import wraps, partial
from contextlib import contextmanager, AbstractContextManager
from dataclasses import dataclass
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
)
from typing_extensions import ParamSpec, Concatenate
from slyme.utils.common import enrich_exception
from slyme.context import Context, Ref
from .base import Node, NodeExpression, NodeWrapper

__all__ = [
    "spec",
    "ref_spec",
    "node",
    "expression",
    "wrapper",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T = TypeVar("_T")
NodeFunc = Callable[Concatenate[Context, _P], None]
ExpressionFunc = Callable[Concatenate[Context, _P], _R]
WrapperFunc = Callable[Concatenate[Context, Node, _P], Generator[Any, None, None]]
WrapperCMFactory = Callable[Concatenate[Context, Node, _P], AbstractContextManager[Any]]

_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


# Spec Definitions
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


# Spec Functions
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
    return RefSpec(
        default=default, 
        default_factory=default_factory, 
        metadata=metadata
    )


# Inspect operations.
@dataclass(frozen=True)
class _SignatureAnalysis:
    pos_only_params: list[inspect.Parameter]
    kw_only_params: list[inspect.Parameter]
    public_signature: inspect.Signature
    specs: Mapping[str, Spec]


def _resolve_spec(param: inspect.Parameter, hint: Any) -> Union[Spec, _Missing]:
    spec_obj: Union[_Missing, Spec] = _MISSING
    # 1. Check for Annotated
    if get_origin(hint) is Annotated:
        # NOTE: For now we only support Annotated[T, Spec],
        # and we will consider relaxing this restriction if needed.
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
        return _MISSING


def _analyze_signature(func: Callable) -> _SignatureAnalysis:
    """
    Analyze the function signature to separate runtime parameters and config parameters.
    Also resolves Annotated Param metadata with strict validation.

    Returns:
        A _SignatureAnalysis object containing:
        1. pos_only_params: Runtime args (e.g. ctx).
        2. kw_only_params: Configuration args (e.g. *, ref_a=...).
        3. public_signature: A simplified signature for the factory function.
        4. specs: A mapping of Spec objects.

    Raises:
        TypeError: If forbidden parameter kinds are found, semantic conflicts occur,
                   or Annotated metadata is invalid.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    pos_only_params = []
    kw_only_params = []
    public_params = []  # Used for factory signature
    specs: dict[str, Spec] = {}

    type_hints = get_type_hints(func, include_extras=True)

    for p in params:
        # Use error scope for each parameter to provide fine-grained context
        with enrich_exception(f"in definition of '{func.__name__}'"):
            if p.kind == inspect.Parameter.POSITIONAL_ONLY:
                pos_only_params.append(p)
            elif p.kind == inspect.Parameter.KEYWORD_ONLY:
                kw_only_params.append(p)
                public_params.append(p)
                # Spec Resolution Logic
                spec = _resolve_spec(p, type_hints.get(p.name))
                if spec is not _MISSING:
                    specs[p.name] = spec
            else:
                # Strictly forbid ordinary arguments (*args, **kwargs, or args without / or *)
                kind_name = str(p.kind)
                raise TypeError(
                    f"Invalid parameter '{p.name}' of kind {kind_name}. "
                    f"Functional nodes strict rules:\n"
                    f"  1. Runtime args (e.g. ctx) must be POSITIONAL_ONLY (before '/').\n"
                    f"  2. Config args must be KEYWORD_ONLY (after '*')."
                )

    # The factory signature should hide the runtime args.
    public_signature = sig.replace(parameters=public_params)
    return _SignatureAnalysis(
        pos_only_params=pos_only_params,
        kw_only_params=kw_only_params,
        public_signature=public_signature,
        specs=types.MappingProxyType(specs),
    )


def _process_kwargs(
    kw_params: list[inspect.Parameter],
    specs: Mapping[str, Spec],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and process kwargs with enhanced Spec support:
    1. Check for unexpected arguments (strict subset).
    2. Check for missing required arguments.
    3. Inject Spec-resolved values.

    Returns:
        The fully populated kwargs dictionary ready for binding.
    """
    allowed_names = {p.name for p in kw_params}
    input_names = set(kwargs.keys())

    # 1. Strict Subset Check: No unknown arguments allowed
    unknown_args = input_names - allowed_names
    if unknown_args:
        raise TypeError(
            f"Got unexpected keyword argument(s) {list(unknown_args)}. "
            f"Allowed arguments: {list(allowed_names)}."
        )

    # 2. Apply Specs
    final_kwargs = {}
    for param in kw_params:
        name = param.name
        # Apply Spec.
        value = kwargs.get(name, _MISSING)
        if name in specs:
            with enrich_exception(f"for parameter '{name}'"):
                value = specs[name].resolve(value)

        if value is not _MISSING:
            final_kwargs[name] = value
        else:
            raise ValueError(f"Missing required configuration argument '{name}'.")
    return final_kwargs


@dataclass(frozen=True)
class _NodeConfig:
    func: NodeFunc
    kw_params: list[inspect.Parameter]
    specs: Mapping[str, Spec]


@dataclass(frozen=True)
class _ExpressionConfig:
    func: ExpressionFunc
    kw_params: list[inspect.Parameter]
    specs: Mapping[str, Spec]


@dataclass(frozen=True)
class _WrapperConfig:
    func: WrapperFunc
    kw_params: list[inspect.Parameter]
    specs: Mapping[str, Spec]
    cm_factory: WrapperCMFactory


# Node Elements
class _FunctionalNode(Node):
    def __init__(self, config: _NodeConfig, /, **kwargs):
        self._config = config
        # 1. Validate & Apply Specs
        with enrich_exception(f"for '{config.func.__name__}'"):
            kwargs = _process_kwargs(config.kw_params, config.specs, kwargs)
        # 2. Extract Super Args (Explicit Logic for Node)
        super_kwargs = {}
        # Node supports "node_wrappers". We explicitly look for it.
        if "node_wrappers" in kwargs:
            super_kwargs["node_wrappers"] = kwargs.pop("node_wrappers")
        # 3. Super Init
        super().__init__(**super_kwargs)
        # 4. Bind remaining attributes
        for name, val in kwargs.items():
            setattr(self, name, val)

    def execute(self, ctx: Context, /) -> None:
        config_kwargs = {p.name: getattr(self, p.name) for p in self._config.kw_params}
        return self._config.func(ctx, **config_kwargs)

    def type_repr(self) -> str:
        return self._config.func.__name__


class _FunctionalExpression(NodeExpression):
    def __init__(self, config: _ExpressionConfig, /, **kwargs):
        self._config = config
        # 1. Validate & Apply Specs
        with enrich_exception(f"for '{config.func.__name__}'"):
            kwargs = _process_kwargs(config.kw_params, config.specs, kwargs)
        # 2. Super Init
        super().__init__()
        # 3. Bind remaining attributes
        for name, val in kwargs.items():
            setattr(self, name, val)

    def evaluate(self, ctx: Context, /) -> Any:
        config_kwargs = {p.name: getattr(self, p.name) for p in self._config.kw_params}
        return self._config.func(ctx, **config_kwargs)

    def type_repr(self) -> str:
        return self._config.func.__name__


class _FunctionalWrapper(NodeWrapper):
    def __init__(self, config: _WrapperConfig, /, **kwargs):
        self._config = config
        # 1. Validate & Apply Specs
        with enrich_exception(f"for '{config.func.__name__}'"):
            kwargs = _process_kwargs(config.kw_params, config.specs, kwargs)
        # 2. Super Init
        super().__init__()
        # 3. Bind remaining attributes
        for name, val in kwargs.items():
            setattr(self, name, val)

    @contextmanager
    def wrap(self, ctx: Context, wrapped: Node, /) -> Generator[Any, None, None]:
        config_kwargs = {p.name: getattr(self, p.name) for p in self._config.kw_params}
        with self._config.cm_factory(ctx, wrapped, **config_kwargs) as val:
            yield val

    def type_repr(self) -> str:
        return self._config.func.__name__


# Union type for configs
_Config = Union[_NodeConfig, _ExpressionConfig, _WrapperConfig]


def _create_factory(
    cls: type[_T], config: _Config, public_sig: inspect.Signature
) -> Callable[..., _T]:
    """
    Creates the factory function that looks like the original function but returns a class instance.
    """

    @wraps(config.func)
    def factory(**kwargs):
        return cls(config, **kwargs)

    # Masquerade the signature
    factory.__signature__ = public_sig
    # Backdoor for testing
    factory.cls = cls
    return factory


# Decorators
def _node(func: NodeFunc[_P], /) -> Callable[_P, Node]:
    """
    Decorator to convert a function into a Node factory.
    Requires exactly 1 POSITIONAL_ONLY argument: ctx.
    """
    analysis = _analyze_signature(func)

    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )

    config = _NodeConfig(
        func=func, kw_params=analysis.kw_only_params, specs=analysis.specs
    )
    return _create_factory(_FunctionalNode, config, analysis.public_signature)


@overload
def node(
    func: _Missing = _MISSING, /
) -> Callable[[NodeFunc[_P]], Callable[_P, Node]]: ...
@overload
def node(func: NodeFunc[_P], /) -> Callable[_P, Node]: ...
def node(
    func: Union[NodeFunc[_P], _Missing] = _MISSING, /
) -> Union[Callable[[NodeFunc[_P]], Callable[_P, Node]], Callable[_P, Node]]:
    if func is _MISSING:
        return partial(_node)
    else:
        return _node(func)


def _expression(func: ExpressionFunc[_P, _R], /) -> Callable[_P, NodeExpression[_R]]:
    """
    Decorator to convert a function into a NodeExpression factory.
    Requires exactly 1 POSITIONAL_ONLY argument: ctx.
    """
    analysis = _analyze_signature(func)

    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@expression '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )

    config = _ExpressionConfig(
        func=func, kw_params=analysis.kw_only_params, specs=analysis.specs
    )
    return _create_factory(_FunctionalExpression, config, analysis.public_signature)


@overload
def expression(
    func: _Missing = _MISSING, /
) -> Callable[[ExpressionFunc[_P, _R]], Callable[_P, NodeExpression[_R]]]: ...
@overload
def expression(func: ExpressionFunc[_P, _R], /) -> Callable[_P, NodeExpression[_R]]: ...
def expression(func: Union[ExpressionFunc[_P, _R], _Missing] = _MISSING, /) -> Union[
    Callable[[ExpressionFunc[_P, _R]], Callable[_P, NodeExpression[_R]]],
    Callable[_P, NodeExpression[_R]],
]:
    if func is _MISSING:
        return partial(_expression)
    else:
        return _expression(func)


def _wrapper(func: WrapperFunc[_P], /) -> Callable[_P, NodeWrapper]:
    """
    Decorator to convert a generator function into a NodeWrapper factory.
    Requires exactly 2 POSITIONAL_ONLY arguments: ctx, wrapped.
    """
    analysis = _analyze_signature(func)

    if len(analysis.pos_only_params) != 2:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 2 positional-only arguments (ctx, wrapped), "
            f"but found {len(analysis.pos_only_params)}."
        )

    _cm_factory = contextmanager(func)
    config = _WrapperConfig(
        func=func,
        kw_params=analysis.kw_only_params,
        specs=analysis.specs,
        cm_factory=_cm_factory,
    )
    return _create_factory(_FunctionalWrapper, config, analysis.public_signature)


@overload
def wrapper(
    func: _Missing = _MISSING, /
) -> Callable[[WrapperFunc[_P]], Callable[_P, NodeWrapper]]: ...
@overload
def wrapper(func: WrapperFunc[_P], /) -> Callable[_P, NodeWrapper]: ...
def wrapper(
    func: Union[WrapperFunc[_P], _Missing] = _MISSING, /
) -> Union[
    Callable[[WrapperFunc[_P]], Callable[_P, NodeWrapper]], Callable[_P, NodeWrapper]
]:
    if func is _MISSING:
        return partial(_wrapper)
    else:
        return _wrapper(func)
