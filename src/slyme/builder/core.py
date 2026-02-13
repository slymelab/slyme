from functools import wraps, partial
from enum import Enum
from typing import (
    TypeVar,
    Callable,
    Union,
    overload,
)
from typing_extensions import ParamSpec
from slyme.node import Node, check_node_structure

_NodeT = TypeVar("_NodeT", bound=Node)
_P = ParamSpec("_P")
# Marker for missing arguments to handle @builder vs @builder()
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


def _builder(
    func: Callable[_P, _NodeT],
    /,
    *,
    check_structure: bool = True,
) -> Callable[_P, _NodeT]:
    @wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _NodeT:
        node = func(*args, **kwargs)
        if node is None:
            raise ValueError(
                f"The builder function '{func.__name__}' returned None. "
                "Did you forget to return the constructed Node?"
            )

        if check_structure:
            check_node_structure(node)
        return node

    return wrapper


@overload
def builder(
    func: _Missing = _MISSING,
    /,
    *,
    check_structure: bool = True,
) -> Callable[[Callable[_P, _NodeT]], Callable[_P, _NodeT]]: ...
@overload
def builder(
    func: Callable[_P, _NodeT],
    /,
    *,
    check_structure: bool = True,
) -> Callable[_P, _NodeT]: ...
def builder(
    func: Union[Callable[_P, _NodeT], _Missing] = _MISSING,
    /,
    *,
    check_structure: bool = True,
) -> Union[
    Callable[_P, _NodeT],
    Callable[[Callable[_P, _NodeT]], Callable[_P, _NodeT]],
]:
    """
    Decorator to create a builder function.
    It wraps the function to ensure it returns a valid Node and optionally checks the structure.
    """
    if func is _MISSING:
        return partial(_builder, check_structure=check_structure)
    else:
        return _builder(func, check_structure=check_structure)
