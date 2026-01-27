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
    ParamSpec,
    Concatenate,
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
)
from slyme.context import Context
from .base import Node, NodeExpression, NodeWrapper

__all__ = [
    "Param",
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

_Missing = Enum("Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(frozen=True)
class Param:
    """
    Dependency injection metadata for functional node parameters.
    """

    default: Union[Any, _Missing] = _MISSING
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING

    def __post_init__(self):
        if self.default is not _MISSING and self.default_factory is not _MISSING:
            raise ValueError(
                "Cannot specify both `default` and `default_factory` in Param."
            )

    def resolve(self, value: Any = _MISSING) -> Any:
        """
        Resolve the final value for the parameter.
        """
        # 1. User provided value takes precedence (even if it is None or MISSING).
        if value is not _MISSING:
            return value

        # 2. Check static default.
        if self.default is not _MISSING:
            return self.default

        # 3. Check default factory.
        if self.default_factory is not _MISSING:
            return self.default_factory()

        # 4. No value provided and no default available.
        raise ValueError("Missing required parameter.")


@dataclass(frozen=True)
class _SignatureAnalysis:
    pos_only_params: list[inspect.Parameter]
    kw_only_params: list[inspect.Parameter]
    public_signature: inspect.Signature
    # Mapping of parameter name to its default value (Param instance or raw value)
    defaults: Mapping[str, Any]


def _analyze_signature(func: Callable) -> _SignatureAnalysis:
    """
    Analyze the function signature to separate runtime parameters and config parameters.
    Also resolves Annotated Param metadata with strict validation.

    Returns:
        A _SignatureAnalysis object containing:
        1. pos_only_params: Runtime args (e.g. ctx).
        2. kw_only_params: Configuration args (e.g. *, ref_a=...).
        3. public_signature: A simplified signature for the factory function.
        4. defaults: A mapping of param names to their defaults (Param obj or raw value).

    Raises:
        TypeError: If forbidden parameter kinds are found, semantic conflicts occur,
                   or Annotated metadata is invalid.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    # Try to resolve type hints including Annotated extras
    try:
        type_hints = get_type_hints(func, include_extras=True)
    except Exception:
        # Fallback if type resolution fails
        type_hints = {}

    pos_only_params = []
    kw_only_params = []
    public_params = []  # Used for factory signature
    defaults = {}

    for p in params:
        if p.kind == inspect.Parameter.POSITIONAL_ONLY:
            pos_only_params.append(p)
        elif p.kind == inspect.Parameter.KEYWORD_ONLY:
            kw_only_params.append(p)
            public_params.append(p)

            # --- Param / Default Value Resolution Logic ---
            param_obj = None
            hint = type_hints.get(p.name)

            # 1. Check for Annotated
            if get_origin(hint) is Annotated:
                # args[0] is the type, args[1:] are the metadata
                args = get_args(hint)
                metadata = args[1:]
                
                # Strict Mode:
                # If metadata exists, it MUST contain exactly one item,
                # and that item MUST be an instance of Param.
                if metadata:
                    if len(metadata) > 1:
                        raise TypeError(
                            f"Invalid Annotated metadata for parameter '{p.name}' in '{func.__name__}'. "
                            f"Currently, only a single `Param` metadata is allowed, but found {len(metadata)} items."
                        )
                    
                    candidate = metadata[0]
                    if not isinstance(candidate, Param):
                        raise TypeError(
                            f"Invalid Annotated metadata for parameter '{p.name}' in '{func.__name__}'. "
                            f"Expected explicit `Param` instance, but got {type(candidate).__name__}. "
                            f"Other metadata types are strictly forbidden."
                        )
                    param_obj = candidate

            # 2. Conflict Check and Collection
            if param_obj is not None:
                # If Param is defined in Annotated, strictly forbid standard default values.
                if p.default is not inspect.Parameter.empty:
                    raise TypeError(
                        f"Parameter '{p.name}' in '{func.__name__}' has a semantic conflict. "
                        f"It defines a `Param` in `Annotated` but also has a standard default value. "
                        f"Please remove the standard default value assignment."
                    )
                defaults[p.name] = param_obj
            elif p.default is not inspect.Parameter.empty:
                # Fallback to standard default value
                defaults[p.name] = p.default

        else:
            # Strictly forbid ordinary arguments (*args, **kwargs, or args without / or *)
            kind_name = str(p.kind)
            raise TypeError(
                f"Function '{func.__name__}' has an invalid parameter '{p.name}' of kind {kind_name}. "
                f"Functional nodes strict rules:\n"
                f"  1. Runtime args (e.g. ctx) must be POSITIONAL_ONLY (before '/').\n"
                f"  2. Config args must be KEYWORD_ONLY (after '*')."
            )

    # The factory signature should hide the runtime args (ctx)
    public_signature = sig.replace(parameters=public_params)

    return _SignatureAnalysis(
        pos_only_params=pos_only_params,
        kw_only_params=kw_only_params,
        public_signature=public_signature,
        defaults=types.MappingProxyType(defaults),
    )


def _process_kwargs(
    instance_name: str,
    kw_params: list[inspect.Parameter],
    defaults: Mapping[str, Any],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and process kwargs with enhanced Param support:
    1. Check for unexpected arguments (strict subset).
    2. Check for missing required arguments.
    3. Inject default values (Standard or Param-resolved).

    Returns:
        The fully populated kwargs dictionary ready for binding.
    """
    allowed_names = {p.name for p in kw_params}
    input_names = set(kwargs.keys())

    # 1. Strict Subset Check: No unknown arguments allowed
    unknown_args = input_names - allowed_names
    if unknown_args:
        raise TypeError(
            f"Got unexpected keyword argument(s) {list(unknown_args)} for '{instance_name}'. "
            f"Allowed arguments: {list(allowed_names)}."
        )

    # 2. Process required arguments & Inject defaults
    final_kwargs = kwargs.copy()

    for param in kw_params:
        name = param.name
        
        # Check existence using standard python dict behavior
        is_provided = name in kwargs
        user_value = kwargs[name] if is_provided else _MISSING
        
        default_container = defaults.get(name, _MISSING)

        # Case A: Default is a Param object -> Resolve it
        if isinstance(default_container, Param):
            try:
                # Pass user value (or MISSING) to resolve logic
                final_kwargs[name] = default_container.resolve(user_value)
            except Exception as e:
                raise ValueError(
                    f"Error resolving parameter '{name}' for '{instance_name}': {e}"
                ) from e

        # Case B: Default is a standard value (and not MISSING)
        elif default_container is not _MISSING:
            # Only use standard default if user did NOT provide a value.
            # If user provided None or MISSING explicitly, we respect it.
            if not is_provided:
                final_kwargs[name] = default_container
            # else: user provided value is already in final_kwargs

        # Case C: No default exists (standard required argument)
        else:
            if not is_provided:
                raise ValueError(
                    f"Missing required configuration argument '{name}' for '{instance_name}'."
                )
            # else: user provided value is already in final_kwargs

    return final_kwargs


@dataclass(frozen=True)
class _NodeConfig:
    func: NodeFunc
    kw_params: list[inspect.Parameter]
    defaults: Mapping[str, Any]


@dataclass(frozen=True)
class _ExpressionConfig:
    func: ExpressionFunc
    kw_params: list[inspect.Parameter]
    defaults: Mapping[str, Any]


@dataclass(frozen=True)
class _WrapperConfig:
    func: WrapperFunc
    kw_params: list[inspect.Parameter]
    defaults: Mapping[str, Any]
    cm_factory: WrapperCMFactory


class _FunctionalNode(Node):
    def __init__(self, config: _NodeConfig, /, **kwargs):
        self._config = config
        # 1. Validate & Fill Defaults
        kwargs = _process_kwargs(
            config.func.__name__, config.kw_params, config.defaults, kwargs
        )

        # 2. Extract Super Args (Explicit Logic for Node)
        super_kwargs = {}
        # Node supports 'node_wrappers'. We explicitly look for it.
        if "node_wrappers" in kwargs:
            super_kwargs["node_wrappers"] = kwargs.pop("node_wrappers")

        # 3. Super Init
        super().__init__(**super_kwargs)

        # 4. Bind remaining attributes (All validation/defaults handled in step 1)
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
        # 1. Validate & Fill Defaults
        kwargs = _process_kwargs(
            config.func.__name__, config.kw_params, config.defaults, kwargs
        )

        # 3. Super Init
        super().__init__()

        # 4. Bind remaining attributes
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
        # 1. Validate & Fill Defaults
        kwargs = _process_kwargs(
            config.func.__name__, config.kw_params, config.defaults, kwargs
        )

        # 3. Super Init
        super().__init__()

        # 4. Bind remaining attributes
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
        func=func, kw_params=analysis.kw_only_params, defaults=analysis.defaults
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
        func=func, kw_params=analysis.kw_only_params, defaults=analysis.defaults
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
        defaults=analysis.defaults,
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
