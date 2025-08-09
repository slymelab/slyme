from functools import wraps
import slyme.logging.logger as logger
from .typing.native import (
    Union,
    Callable,
    TypeVar,
    overload,
    Any,
    Mapping,
    Literal,
    Dict,
)
from .typing.extension import MISSING, Missing, unwrap_method, resolve_name

_FuncOrMethodT = TypeVar("_FuncOrMethodT")


def auto_decorator(
    *, index: Union[int, Missing] = MISSING, keyword: Union[str, Missing] = MISSING
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
            arg_match = MISSING
            # Check ``keyword`` arg match.
            if keyword is not MISSING:
                arg_match = kwargs.get(keyword, MISSING)
            # Check ``index`` arg match.
            if index is not MISSING and arg_match is MISSING:
                arg_match = MISSING if index >= len(args) else args[index]

            _decorator = func(*args, **kwargs)
            # Pass ``arg_match`` to ``_decorator`` if it is not ``MISSING``.
            return _decorator if arg_match is MISSING else _decorator(arg_match)

        return wrapper

    return decorator


def method_chaining(func):
    """
    [func, level-1]
    """

    @wraps(func)
    def wrapper(self, *args, **kwargs):
        func(self, *args, **kwargs)
        return self

    return wrapper


def deprecated():
    """
    [func, level-1]
    """
    # TODO
    pass


def experimental():
    # TODO
    pass


#
# not_implemented decorator.
#

NOT_IMPLEMENTED_ATTR_NAME = "smx_not_implemented"
NotImplementedLevelType = Union[Literal["error", "warning", "silent"], None]


def _not_implemented_error(func_name: str):
    raise NotImplementedError(
        f"You are calling the function or method ``{func_name}`` which is not implemented."
    )


def _not_implemented_warning(func_name: str):
    logger.core_logger.warning(
        f"You are calling the function or method ``{func_name}`` which is not implemented."
    )


def _not_implemented_silent(*args, **kwargs):
    """
    Do nothing.
    """
    pass


_NOT_IMPLEMENTED_LEVEL_REGISTRY: Dict[str, Callable[[str], None]] = {
    "error": _not_implemented_error,
    "warning": _not_implemented_warning,
    "silent": _not_implemented_silent,
}


@overload
def not_implemented(
    _func: Missing = MISSING, *, level: NotImplementedLevelType = "error"
) -> Callable[[_FuncOrMethodT], _FuncOrMethodT]: ...
@overload
def not_implemented(
    _func: _FuncOrMethodT, *, level: NotImplementedLevelType = "error"
) -> _FuncOrMethodT: ...
@auto_decorator(index=0, keyword="_func")
def not_implemented(
    _func=MISSING,
    *,
    level: NotImplementedLevelType = "error",
):
    """
    NOTE: When ``level`` is not None, ``@not_implemented`` will ignore the body of the decorated function or method,
    so just leaving it empty would be the best choice.
    """

    def decorator(func: _FuncOrMethodT) -> _FuncOrMethodT:
        if level is None:
            return func_setattr(func, attr_dict={NOT_IMPLEMENTED_ATTR_NAME: True})

        func_name = resolve_name(func)

        @func_setattr(attr_dict={NOT_IMPLEMENTED_ATTR_NAME: True})
        @wraps(func)
        def wrapper(*args, **kwargs):
            _NOT_IMPLEMENTED_LEVEL_REGISTRY[level](func_name)

        return wrapper

    return decorator


def is_not_implemented(func: _FuncOrMethodT) -> bool:
    static_func = unwrap_method(func)
    return getattr(static_func, NOT_IMPLEMENTED_ATTR_NAME, False)


#
# FuncSetAttr.
#


@overload
def func_setattr(
    _func: Missing = MISSING, *, attr_dict: Mapping[str, Any]
) -> Callable[[_FuncOrMethodT], _FuncOrMethodT]: ...
@overload
def func_setattr(
    _func: _FuncOrMethodT, *, attr_dict: Mapping[str, Any]
) -> _FuncOrMethodT: ...
@auto_decorator(index=0, keyword="_func")
def func_setattr(_func=MISSING, *, attr_dict: Mapping[str, Any]):
    """
    Set attributes to the function in a decorator way.
    """

    def decorator(func: _FuncOrMethodT) -> _FuncOrMethodT:
        for key, value in attr_dict.items():
            try:
                setattr(func, key, value)
            except AttributeError as e:
                from slyme.logging.logger import core_logger

                core_logger.error(str(e), stack_info=True)
        return func

    return decorator
