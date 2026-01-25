"""
Functional API for slyme nodes.
"""

import inspect
from functools import wraps, partial
from contextlib import contextmanager
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

P = ParamSpec("P")
R = TypeVar("R")


def _analyze_signature(
    func: Callable,
) -> tuple[list[inspect.Parameter], list[inspect.Parameter], inspect.Signature]:
    """
    Analyze the function signature to separate runtime parameters and config parameters.

    Returns:
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
    return pos_only_params, kw_only_params, public_signature


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


def _create_factory(
    func: Callable, cls: type, public_sig: inspect.Signature
) -> Callable:
    """
    Creates the factory function that looks like the original function but returns a class instance.
    """

    @wraps(func)
    def factory(**kwargs):
        return cls(**kwargs)

    # Masquerade the signature
    factory.__signature__ = public_sig  # type: ignore
    # Backdoor for testing
    factory.cls = cls  # type: ignore
    return factory


def _copy_metadata(cls: type, func: Callable) -> None:
    cls.__name__ = func.__name__
    cls.__qualname__ = func.__qualname__
    cls.__module__ = func.__module__
    cls.__doc__ = func.__doc__


# --- Functional Decorators ---
NodeFunc = Callable[Concatenate[Context, P], None]
ExpressionFunc = Callable[Concatenate[Context, P], R]
WrapperFunc = Callable[Concatenate[Context, Node, P], Generator[Any, None, None]]


def _node(func: NodeFunc[P], /) -> Callable[P, Node]:
    """
    Decorator to convert a function into a Node factory.
    Requires exactly 1 POSITIONAL_ONLY argument: ctx.
    """
    pos_params, kw_params, public_sig = _analyze_signature(func)

    if len(pos_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(pos_params)}."
        )

    class FunctionalNode(Node):
        def __init__(self, /, **kwargs):
            # 1. Validate & Fill Defaults
            kwargs = _process_kwargs(func.__name__, kw_params, kwargs)

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
            config_kwargs = {p.name: getattr(self, p.name) for p in kw_params}
            return func(ctx, **config_kwargs)

    _copy_metadata(FunctionalNode, func)
    return _create_factory(func, FunctionalNode, public_sig)


@overload
def node(func: Missing = MISSING, /) -> Callable[[NodeFunc[P]], Callable[P, Node]]: ...
@overload
def node(func: NodeFunc[P], /) -> Callable[P, Node]: ...
def node(
    func: Union[NodeFunc[P], Missing] = MISSING, /
) -> Union[Callable[[NodeFunc[P]], Callable[P, Node]], Callable[P, Node]]:
    if func is MISSING:
        return partial(_node)
    else:
        return _node(func)


def _expression(func: ExpressionFunc[P, R], /) -> Callable[P, NodeExpression[R]]:
    """
    Decorator to convert a function into a NodeExpression factory.
    Requires exactly 1 POSITIONAL_ONLY argument: ctx.
    """
    pos_params, kw_params, public_sig = _analyze_signature(func)

    if len(pos_params) != 1:
        raise TypeError(
            f"@expression '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(pos_params)}."
        )

    class FunctionalExpression(NodeExpression):
        def __init__(self, /, **kwargs):
            # 1. Validate & Fill Defaults
            kwargs = _process_kwargs(func.__name__, kw_params, kwargs)

            # 3. Super Init (NodeExpression usually doesn't take node_wrappers)
            super().__init__()

            # 4. Bind remaining attributes
            for name, val in kwargs.items():
                setattr(self, name, val)

        def evaluate(self, ctx: Context, /) -> R:
            config_kwargs = {p.name: getattr(self, p.name) for p in kw_params}
            return func(ctx, **config_kwargs)

    _copy_metadata(FunctionalExpression, func)
    return _create_factory(func, FunctionalExpression, public_sig)


@overload
def expression(
    func: Missing = MISSING, /
) -> Callable[[ExpressionFunc[P, R]], Callable[P, NodeExpression[R]]]: ...
@overload
def expression(func: ExpressionFunc[P, R], /) -> Callable[P, NodeExpression[R]]: ...
def expression(func: Union[ExpressionFunc[P, R], Missing] = MISSING, /) -> Union[
    Callable[[ExpressionFunc[P, R]], Callable[P, NodeExpression[R]]],
    Callable[P, NodeExpression[R]],
]:
    if func is MISSING:
        return partial(_expression)
    else:
        return _expression(func)


def _wrapper(func: WrapperFunc[P], /) -> Callable[P, NodeWrapper]:
    """
    Decorator to convert a generator function into a NodeWrapper factory.
    Requires exactly 2 POSITIONAL_ONLY arguments: ctx, wrapped.
    """
    pos_params, kw_params, public_sig = _analyze_signature(func)

    if len(pos_params) != 2:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 2 positional-only arguments (ctx, wrapped), "
            f"but found {len(pos_params)}."
        )

    _cm_factory = contextmanager(func)

    class FunctionalWrapper(NodeWrapper):
        def __init__(self, /, **kwargs):
            # 1. Validate & Fill Defaults
            kwargs = _process_kwargs(func.__name__, kw_params, kwargs)

            # 3. Super Init (NodeWrapper usually doesn't take node_wrappers)
            super().__init__()

            # 4. Bind remaining attributes
            for name, val in kwargs.items():
                setattr(self, name, val)

        @contextmanager
        def wrap(self, ctx: Context, wrapped: Node, /) -> Generator[None, None, None]:
            config_kwargs = {p.name: getattr(self, p.name) for p in kw_params}
            with _cm_factory(ctx, wrapped, **config_kwargs):
                yield

    _copy_metadata(FunctionalWrapper, func)
    return _create_factory(func, FunctionalWrapper, public_sig)


@overload
def wrapper(
    func: Missing = MISSING, /
) -> Callable[[WrapperFunc[P]], Callable[P, NodeWrapper]]: ...
@overload
def wrapper(func: WrapperFunc[P], /) -> Callable[P, NodeWrapper]: ...
def wrapper(
    func: Union[WrapperFunc[P], Missing] = MISSING, /
) -> Union[
    Callable[[WrapperFunc[P]], Callable[P, NodeWrapper]], Callable[P, NodeWrapper]
]:
    if func is MISSING:
        return partial(_wrapper)
    else:
        return _wrapper(func)
