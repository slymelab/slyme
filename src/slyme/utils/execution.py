"""
Execution control.
"""

from types import TracebackType
from functools import partial
from contextlib import contextmanager, ExitStack, nullcontext
from slyme.utils.abc.execution import (
    GeneratorExecutorABC,
    ContextGeneratorExecutorABC,
    GeneratorExecutorCollectionABC,
)
from slyme.utils.typing.native import (
    TypeVar,
    Union,
    Generator,
    Tuple,
    Any,
    ContextManager,
    Generic,
    List,
    Iterable,
    SupportsIndex,
    Callable,
    Type,
)
from slyme.utils.typing.extension import (
    MISSING,
    Stop,
    STOP,
    Missing,
    EmptyFlag,
)
from .base.collection import BaseList

_T = TypeVar("_T")
_YieldT_co = TypeVar("_YieldT_co", covariant=True)
_SendT_contra = TypeVar("_SendT_contra", contravariant=True)
_ReturnT_co = TypeVar("_ReturnT_co", covariant=True)
_GeneratorExecutorT = TypeVar("_GeneratorExecutorT", bound=GeneratorExecutorABC)


class GeneratorExecutorCollection(
    BaseList[_GeneratorExecutorT],
    GeneratorExecutorCollectionABC[_GeneratorExecutorT],
    Generic[_GeneratorExecutorT],
):
    def __init__(
        self,
        __list_like: Union[
            Iterable[Union[_GeneratorExecutorT, Generator]], EmptyFlag
        ] = MISSING,
    ):
        super().__init__(__list_like)

    @contextmanager
    def queue_context_manager(self) -> Generator[Tuple, Any, None]:
        """
        Sequentially call the generators on ``__enter__`` and ``__exit__``. Tuple of
        yielded values from the generators will be yielded.

        NOTE: The generators should be yield-once.

        NOTE: Exceptions will NOT be processed by the queue.

        NOTE: Queue modifications will NOT take effect after ``__enter__``.
        """
        # Copy ``self`` for consistency.
        gen_queue = BaseList(self)
        # Call next and yield a tuple of yielded values.
        vals = tuple(gen.next(should_stop=False) for gen in gen_queue)
        yield vals
        # Call next.
        for gen in gen_queue:
            gen.next(should_stop=True)

    def stack_context_manager(self) -> ContextManager[Tuple]:
        """
        Execute the generators in FILO order. Exceptions can be processed.

        NOTE: The generators should be yield-once.
        """
        gen_stack = BaseList(self)
        return context_manager_stack(gen_stack)

    def __setitem__(
        self,
        __key: Union[SupportsIndex, slice],
        __value: Union[
            _GeneratorExecutorT,
            Generator,
            Iterable[Union[_GeneratorExecutorT, Generator]],
        ],
    ):
        if isinstance(__key, slice):
            __value = (
                (
                    item
                    if isinstance(item, GeneratorExecutorABC)
                    else GeneratorExecutor(item)
                )
                for item in __value
            )
        else:
            __value = (
                __value
                if isinstance(__value, GeneratorExecutorABC)
                else GeneratorExecutor(__value)
            )
        return super().__setitem__(__key, __value)

    def insert(
        self, __index: SupportsIndex, __object: Union[_GeneratorExecutorT, Generator]
    ):
        __object = (
            __object
            if isinstance(__object, GeneratorExecutorABC)
            else GeneratorExecutor(__object)
        )
        return super().insert(__index, __object)


#
# Generator Context Manager.
#


@contextmanager
def generator_context_manager(gen: Generator[_YieldT_co, _SendT_contra, _ReturnT_co]):
    """
    Wraps a generator into a context manager. NOTE: The generator should be yield-once, otherwise a
    ``RuntimeError`` will be raised by ``@contextmanager``.
    """
    yield from gen


empty_context_manager = nullcontext


#
# Context Manager Stack
#

_EnterT_co = TypeVar("_EnterT_co", covariant=True)


@contextmanager
def _general_context_manager_stack(
    cm_provider: Callable[[_T, List[_EnterT_co]], ContextManager[_EnterT_co]],
    sources: Iterable[_T],
) -> Generator[Tuple[_EnterT_co, ...], Any, None]:
    """
    TODO: document
    """
    # Use ``ExitStack`` to correctly process exceptions.
    with ExitStack() as stack:
        vals: List[_EnterT_co] = []
        for source in sources:
            cm = cm_provider(source=source, vals=vals)
            val = stack.enter_context(cm)
            vals.append(val)
            # If the context manager returns ``STOP``, then directly break.
            if val is STOP:
                break
        yield tuple(vals)


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
    return _general_context_manager_stack(lambda source, **_: source, cms)


def check_stop_flag(values: Tuple[Union[Stop, Any], ...]) -> bool:
    """
    Check whether the stack enter execution has normally finished or stopped.
    NOTE: This is implemented by checking the yielded values. Return ``True``
    if the stack stopped.

    ``values``: The yielded values.
    """
    # Only check whether the last value is ``STOP`` if the tuple is not empty.
    return values[-1] is STOP if len(values) > 0 else False


from .descriptor import ReadonlyDescriptor


class GeneratorExecutor(
    GeneratorExecutorABC[_YieldT_co, _SendT_contra, _ReturnT_co],
    Generic[_YieldT_co, _SendT_contra, _ReturnT_co],
):
    # NOTE: ``gen`` is now readonly for safety reasons.
    gen = ReadonlyDescriptor()

    def __init__(self, gen: Generator[_YieldT_co, _SendT_contra, _ReturnT_co], /):
        super().__init__()
        self.gen = gen

    def send(
        self,
        value: _SendT_contra,
        /,
        *,
        should_stop: Union[Missing, bool, None] = MISSING,
    ) -> _YieldT_co:
        if not isinstance(should_stop, (Missing, bool)) or should_stop is not None:
            raise ValueError(
                f"``should_stop`` should be ``MISSING``, ``None`` or a boolean value."
            )
        if should_stop is MISSING:
            return self.gen.send(value)
        # Check stop
        try:
            result = self.gen.send(value)
        except StopIteration as e:
            # NOTE: ``StopIteration`` is automatically suppressed here if ``should_stop`` is ``None``
            if should_stop is False:
                raise RuntimeError("Generator should not stop.") from None
            else:
                return e.value
        else:
            if should_stop is True:
                try:
                    raise RuntimeError("Generator didn't stop.")
                finally:
                    # NOTE: Manually close the generator because it should stop.
                    self.close()
            else:
                return result

    def throw(
        self,
        exc,
        value=None,
        traceback=None,
        /,
        *,
        should_stop: Union[Missing, bool, None] = MISSING,
    ) -> _YieldT_co:
        if not isinstance(should_stop, (Missing, bool)) or should_stop is not None:
            raise ValueError(
                f"``should_stop`` should be ``MISSING``, ``None`` or a boolean value."
            )
        if should_stop is MISSING:
            return self.gen.throw(exc, value, traceback)
        exc_instance = _normalize_throw_exception(exc, value, traceback)
        return self._throw(exc_instance, should_stop=should_stop)

    def _throw(
        self,
        value: BaseException,
        /,
        *,
        should_stop: Union[bool, None],
    ):
        """Correctly process ``should_stop`` in ``throw``

        NOTE: This method is for forward compatibility with the ``generator.throw``
        method. The (typ, value, traceback) tuple is deprecated in the future version,
        and only one argument (typ or value) will be preserved. We design ``_throw``
        method to reuse the code between different ``throw`` definitions (which are
        dynamically executed based on ``sys.version_info``).

        Args:
            val (BaseException): The exception instance.
            should_stop (bool | None): See ``GeneratorExecutorABC`` for more details.
        """
        # NOTE: The following code is modified from ``contextlib._GeneratorContextManager``
        try:
            result = self.gen.throw(value)
        except StopIteration as exc:
            # Suppress StopIteration *unless* it's the same exception that
            # was passed to throw(). This prevents a StopIteration thrown
            # by the caller from being suppressed.
            if exc is value:
                raise
            # Here the exception thrown is already processed by the generator,
            # and the generator normally ends.
            elif should_stop is False:
                raise RuntimeError("Generator should not stop.") from None
            else:
                # NOTE: If ``should_stop`` is ``None`` or ``True``, then
                # ``StopIteration`` is suppressed, and the value is returned.
                return exc.value
        except RuntimeError as exc:
            # Avoid suppressing if a StopIteration exception
            # was passed to throw() and later wrapped into a RuntimeError
            # (see PEP 479 for sync generators; async generators also
            # have this behavior). But do this only if the exception wrapped
            # by the RuntimeError is actually Stop(Async)Iteration.
            if isinstance(value, StopIteration) and exc.__cause__ is value:
                # NOTE: Should raise the original value.
                raise value
            # Directly re-raise the exception to the caller if it is not wrapped.
            raise
        # Here the exception thrown is already processed by the generator,
        # and the generator does not end.
        if should_stop is True:
            try:
                raise RuntimeError("Generator didn't stop.")
            finally:
                # NOTE: Manually close the generator because it should stop.
                self.close()
        else:
            return result


class ContextGeneratorExecutor(
    GeneratorExecutor[_YieldT_co, _SendT_contra, _ReturnT_co],
    ContextGeneratorExecutorABC[_YieldT_co, _SendT_contra, _ReturnT_co],
    Generic[_YieldT_co, _SendT_contra, _ReturnT_co],
):
    def __init__(
        self,
        gen: Generator[_YieldT_co, _SendT_contra, _ReturnT_co],
        /,
        *,
        exit_send_callback: Union[
            Callable[
                [ContextGeneratorExecutorABC[_YieldT_co, _SendT_contra, _ReturnT_co]],
                _SendT_contra,
            ],
            None,
        ] = None,
    ):
        super().__init__(gen)
        self.exit_send_callback = exit_send_callback

    # Context manager protocol
    def __enter__(self):
        return self.next(should_stop=False)

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            exit_send_callback = self.exit_send_callback
            if exit_send_callback is None:
                self.next(should_stop=True)
            else:
                self.send(exit_send_callback(self), should_stop=True)
        else:
            if exc_value is None:
                # Need to force instantiation so we can reliably
                # tell if we get the same exception back
                exc_value = exc_type()
            try:
                self.throw(exc_value, should_stop=True)
            except BaseException as e:
                if e is exc_value:
                    # Change the traceback to the original one.
                    e.__traceback__ = traceback
                    return False
                # There is a new exception raised by the generator.
                raise


def _normalize_throw_exception(
    exc: Union[BaseException, Type[BaseException]],
    value: Union[None, object, BaseException],
    traceback: Union[None, TracebackType],
) -> BaseException:
    """Mimics the behavior of generator.throw() to construct a normalized, raisable exception
    instance from a (type, value, traceback) triplet.

    Returns:
        ``BaseException``: The exception instance.
    """
    if isinstance(exc, BaseException):
        # Modern usage: directly return the exception instance
        return exc

    # Create the exception instance.
    if value is None:
        # If value is not provided, create a default instance.
        instance = exc()
    elif isinstance(value, exc):
        # If value is already an instance of the type, use it directly.
        instance = value
    elif isinstance(value, tuple):
        # Unpacking the ``exc_value`` tuple.
        instance = exc(*value)
    else:
        # Directly create the instance using ``exc_value``.
        instance = exc(value)

    # Attach the traceback.
    if traceback is not None:
        instance = instance.with_traceback(traceback)

    return instance
