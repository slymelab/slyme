"""
Functional API for slyme nodes.
"""

import inspect
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
)
from slyme.utils.constant import Missing, MISSING
from slyme.node import Node, NodeExpression, NodeWrapper
from slyme.context import Context

__all__ = [
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


@dataclass(frozen=True)
class _SignatureAnalysis:
    pos_only_params: list[inspect.Parameter]
    kw_only_params: list[inspect.Parameter]
    public_signature: inspect.Signature


def _analyze_signature(func: Callable) -> _SignatureAnalysis:
    """
    Analyze the function signature to separate runtime parameters and config parameters.

    Returns:
        A _SignatureAnalysis object containing:
        1. pos_only_params: Runtime args (e.g. ctx).
        2. kw_only_params: Configuration args (e.g. *, ref_a=...).
        3. public_signature: A simplified signature for the factory function.

    Raises:
        TypeError: If forbidden parameter kinds are found.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    pos_only_params = []
    kw_only_params = []
    public_params = []  # Used for factory signature

    for p in params:
        if p.kind == inspect.Parameter.POSITIONAL_ONLY:
            pos_only_params.append(p)
        elif p.kind == inspect.Parameter.KEYWORD_ONLY:
            kw_only_params.append(p)
            public_params.append(p)
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
    )


def _process_kwargs(
    instance_name: str, kw_params: list[inspect.Parameter], kwargs: dict[str, Any]
) -> dict[str, Any]:
    """
    Validate and process kwargs:
    1. Check for unexpected arguments (strict subset).
    2. Check for missing required arguments.
    3. Inject default values for missing optional arguments.

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
    # We construct a new dict to ensure cleanliness (though modifying in-place is also an option)
    final_kwargs = kwargs.copy()

    for param in kw_params:
        name = param.name
        if name not in final_kwargs:
            if param.default is inspect.Parameter.empty:
                raise ValueError(
                    f"Missing required configuration argument '{name}' for '{instance_name}'."
                )
            else:
                # Inject default value
                final_kwargs[name] = param.default

    return final_kwargs


@dataclass(frozen=True)
class _NodeConfig:
    func: NodeFunc
    kw_params: list[inspect.Parameter]


@dataclass(frozen=True)
class _ExpressionConfig:
    func: ExpressionFunc
    kw_params: list[inspect.Parameter]


@dataclass(frozen=True)
class _WrapperConfig:
    func: WrapperFunc
    kw_params: list[inspect.Parameter]
    cm_factory: WrapperCMFactory


class _FunctionalNode(Node):
    def __init__(self, config: _NodeConfig, /, **kwargs):
        self._config = config
        # 1. Validate & Fill Defaults
        kwargs = _process_kwargs(config.func.__name__, config.kw_params, kwargs)

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
        kwargs = _process_kwargs(config.func.__name__, config.kw_params, kwargs)

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
        kwargs = _process_kwargs(config.func.__name__, config.kw_params, kwargs)

        # 3. Super Init
        super().__init__()

        # 4. Bind remaining attributes
        for name, val in kwargs.items():
            setattr(self, name, val)

    @contextmanager
    def wrap(self, ctx: Context, wrapped: Node, /) -> Generator[None, None, None]:
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

    config = _NodeConfig(func=func, kw_params=analysis.kw_only_params)
    return _create_factory(_FunctionalNode, config, analysis.public_signature)


@overload
def node(
    func: Missing = MISSING, /
) -> Callable[[NodeFunc[_P]], Callable[_P, Node]]: ...
@overload
def node(func: NodeFunc[_P], /) -> Callable[_P, Node]: ...
def node(
    func: Union[NodeFunc[_P], Missing] = MISSING, /
) -> Union[Callable[[NodeFunc[_P]], Callable[_P, Node]], Callable[_P, Node]]:
    if func is MISSING:
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

    config = _ExpressionConfig(func=func, kw_params=analysis.kw_only_params)
    return _create_factory(_FunctionalExpression, config, analysis.public_signature)


@overload
def expression(
    func: Missing = MISSING, /
) -> Callable[[ExpressionFunc[_P, _R]], Callable[_P, NodeExpression[_R]]]: ...
@overload
def expression(func: ExpressionFunc[_P, _R], /) -> Callable[_P, NodeExpression[_R]]: ...
def expression(func: Union[ExpressionFunc[_P, _R], Missing] = MISSING, /) -> Union[
    Callable[[ExpressionFunc[_P, _R]], Callable[_P, NodeExpression[_R]]],
    Callable[_P, NodeExpression[_R]],
]:
    if func is MISSING:
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
        func=func, kw_params=analysis.kw_only_params, cm_factory=_cm_factory
    )
    return _create_factory(_FunctionalWrapper, config, analysis.public_signature)


@overload
def wrapper(
    func: Missing = MISSING, /
) -> Callable[[WrapperFunc[_P]], Callable[_P, NodeWrapper]]: ...
@overload
def wrapper(func: WrapperFunc[_P], /) -> Callable[_P, NodeWrapper]: ...
def wrapper(
    func: Union[WrapperFunc[_P], Missing] = MISSING, /
) -> Union[
    Callable[[WrapperFunc[_P]], Callable[_P, NodeWrapper]], Callable[_P, NodeWrapper]
]:
    if func is MISSING:
        return partial(_wrapper)
    else:
        return _wrapper(func)
