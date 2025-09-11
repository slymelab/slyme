from collections.abc import Generator
from contextlib import ExitStack, contextmanager
from slyme.utils.constant import STOP, Stop
from slyme.utils.typing import Any, ContextManager, Iterable, List, Tuple, TypeVar, Union, Literal

# Context Manager Stack
_EnterT_co = TypeVar("_EnterT_co", covariant=True)


@contextmanager
def context_manager_stack(
    cms: Iterable[ContextManager[_EnterT_co]],
) -> Generator[Tuple[_EnterT_co, ...], Any, None]:
    """
    Call context managers in FILO order. Exceptions will be passed through each
    context manager until they are processed. Compared to the standard ``with``
    statement, it can handle context managers of indefinite quantity. The below
    two examples are totally equivalent:
        ```Python
        # Example 1
        with A(), B(), C():
            ...

        # Example 2
        cm_list = [A(), B(), C()]
        with context_manager_stack(cm_list):
            ...
        ```
    """
    # Use ``ExitStack`` to correctly process exceptions.
    with ExitStack() as stack:
        vals: List[_EnterT_co] = []
        for cm in cms:
            val = stack.enter_context(cm)
            vals.append(val)
            # If the context manager returns ``STOP``, then directly break.
            if val is STOP:
                break
        yield tuple(vals)


def check_stop_flag(values: Tuple[Union[Stop, Any], ...]) -> bool:
    """
    Check whether the stack enter execution has normally finished or stopped.
    NOTE: This is implemented by checking the yielded values. Return ``True``
    if the stack stopped.

    ``values``: The yielded values.
    """
    # Only check whether the last value is ``STOP`` if the tuple is not empty.
    return values[-1] is STOP if values else False
