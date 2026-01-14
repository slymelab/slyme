from functools import wraps
from slyme.utils.logging import get_logger
from .typing import (
    Union,
    Callable,
    TypeVar,
)
from .constant import MISSING

_FuncOrMethodT = TypeVar("_FuncOrMethodT")
logger = get_logger(__name__)


def auto_decorator(
    *, index: Union[int, None] = None, keyword: Union[str, None] = None
) -> Callable[[_FuncOrMethodT], _FuncOrMethodT]:
    """
    This function serves as a meta-decorator. It allows a decorator function to
    be used as either a normal function or a decorator.

    NOTE: The ``keyword`` priority is higher than the ``index`` one.

    Keyword args:
        ``index``: The param index of the decorated func.
        ``keyword``: The param keyword of the decorated func.

    Example:
        ```Python
        from functools import wraps

        @DecoratorCall(index=0, keyword='_func')
        def example(_func=MISSING, *, log_content: str = 'function'):
            def decorator(func):
                @wraps(func)
                def wrapper(*args, **kwargs):
                    print(f'{log_content} before.')
                    func(*args, **kwargs)
                    print(f'{log_content} after.')
                return wrapper
            return decorator

        @example
        def example_func1(my_content: str):
            # NOTE: This is equivalent to:
            # @example()
            # def example_func1(my_content: str):
            #     pass
            print(my_content)

        @example(log_content='func2')
        def example_func2(my_content: str):
            print(my_content)

        def example_func3(my_content: str):
            print(my_content)

        # Either index is 0 or keyword is '_func' is fine.
        example_func3 = example(example_func3)
        example_func4 = example(example_func3, log_content='func4')
        example_func5 = example(_func=example_func3, log_content='func5')

        # function before.
        # hi
        # function after.
        example_func1('hi')
        # func2 before.
        # hi
        # func2 after.
        example_func2('hi')
        # function before.
        # hi
        # function after.
        example_func3('hi')
        # func4 before.
        # function before.
        # hi
        # function after.
        # func4 after.
        example_func4('hi')
        # func5 before.
        # function before.
        # hi
        # function after.
        # func5 after.
        example_func5('hi')
        ```
    """

    def decorator(func: _FuncOrMethodT) -> _FuncOrMethodT:
        @wraps(func)
        def wrapper(*args, **kwargs):
            arg_match = None
            # Check ``keyword`` arg match.
            if keyword is not None:
                arg_match = kwargs.get(keyword, MISSING)
            # Check ``index`` arg match.
            if index is not None and arg_match is MISSING:
                arg_match = MISSING if index >= len(args) else args[index]

            _decorator = func(*args, **kwargs)
            # Pass ``arg_match`` to ``_decorator`` if it is not ``MISSING``.
            return _decorator if arg_match is MISSING else _decorator(arg_match)

        return wrapper

    return decorator


def deprecated():
    """
    [func, level-1]
    """
    # TODO
    pass


def experimental():
    # TODO
    pass
