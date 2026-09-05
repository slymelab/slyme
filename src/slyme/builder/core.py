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

from collections.abc import Callable
from enum import Enum
from functools import wraps
from typing import (
    Any,
    ParamSpec,
    TypeVar,
    overload,
)

from slyme.node import AsyncNode, Node

_NodeT = TypeVar("_NodeT", bound=Node[Any] | AsyncNode[Any])
_P = ParamSpec("_P")
# Marker for missing arguments to handle @builder vs @builder()
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


def _builder(
    func: Callable[_P, _NodeT],
    /,
) -> Callable[_P, _NodeT]:
    @wraps(func)
    def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _NodeT:
        node = func(*args, **kwargs)
        if node is None:
            raise ValueError(
                f"The builder function '{func.__name__}' returned None. "
                "Did you forget to return the constructed Node?"
            )
        if not isinstance(node, (Node, AsyncNode)):
            raise TypeError(
                f"The builder function '{func.__name__}' returned "
                f"{type(node).__name__}, expected a Node or AsyncNode."
            )

        return node

    return wrapper


@overload
def builder(
    func: _Missing = _MISSING,
    /,
) -> Callable[[Callable[_P, _NodeT]], Callable[_P, _NodeT]]: ...
@overload
def builder(
    func: Callable[_P, _NodeT],
    /,
) -> Callable[_P, _NodeT]: ...
def builder(
    func: Callable[_P, _NodeT] | _Missing = _MISSING,
    /,
) -> Callable[_P, _NodeT] | Callable[[Callable[_P, _NodeT]], Callable[_P, _NodeT]]:
    """Decorate a function that constructs and returns a Node or AsyncNode."""
    if func is _MISSING:
        return _builder
    else:
        return _builder(func)
