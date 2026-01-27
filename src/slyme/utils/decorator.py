import logging
from functools import wraps
from typing import TypeVar, ParamSpec, Concatenate, Callable

logger = logging.getLogger(__name__)
_T = TypeVar("_T")
_P = ParamSpec("_P")


def deprecated():
    pass


def experimental():
    pass


def return_self(
    func: Callable[Concatenate[_T, _P], None]
) -> Callable[Concatenate[_T, _P], _T]:
    @wraps(func)
    def decorator(self, *args, **kwargs):
        func(self, *args, **kwargs)
        return self
    return decorator
