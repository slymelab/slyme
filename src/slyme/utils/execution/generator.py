"""Execution control."""

from types import TracebackType
from contextlib import contextmanager
from collections.abc import Generator
from slyme.utils.typing import (
    TypeVar,
    Union,
    Tuple,
    Any,
    ContextManager,
    Iterable,
    SupportsIndex,
    Callable,
    Type,
    cast,
    Self,
)
from slyme.utils.constant import MISSING, Missing
from slyme.utils.collection import MutableSequenceProxy
from slyme.utils.collection.base import SequenceData
from .manager import context_manager_stack

_YieldT_co = TypeVar("_YieldT_co", covariant=True)
_SendT_contra = TypeVar("_SendT_contra", contravariant=True)
_ReturnT_co = TypeVar("_ReturnT_co", covariant=True)
_GeneratorExecutorT = TypeVar("_GeneratorExecutorT", bound="GeneratorExecutor")


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


class GeneratorExecutor(Generator[_YieldT_co, _SendT_contra, _ReturnT_co]):
    """Wraps a generator for advanced features.

    ``send`` and ``throw`` processes the ``StopIteration`` exception based on ``should_stop``. NOTE: If ``should_stop`` is ``MISSING``,
    then ``StopIteration`` will not be suppressed, and we should process the exception manually, else ``StopIteration`` will be checked
    based on the ``should_stop`` param and suppressed if the check passes.

    Parameters:
        should_stop (Union[Missing, bool, None], optional):
            Do nothing if ``should_stop`` is ``MISSING``. If ``should_stop`` is ``True``, then raise ``RuntimeError`` if ``StopIteration``
            is not raised. Similarly, if ``should_stop`` is ``False``, then raise ``RuntimeError`` if ``StopIteration`` is raised. If
            ``should_stop`` is ``None``, then suppress any ``StopIteration`` exception without any check. Defaults to ``MISSING``.
    """

    __slots__ = ("_gen", "exit_send_callback")

    def __init__(
        self,
        gen: Generator[_YieldT_co, _SendT_contra, _ReturnT_co],
        /,
        *,
        exit_send_callback: Union[Callable[[Self], _SendT_contra], None] = None,
    ):
        super().__init__()
        self._gen = gen
        self.exit_send_callback = exit_send_callback

    @property
    def gen(self) -> Generator[_YieldT_co, _SendT_contra, _ReturnT_co]:
        # NOTE: gen is readonly for safety reasons.
        return self._gen

    def next(self, *, should_stop: Union[Missing, bool, None] = MISSING) -> _YieldT_co:
        """Call ``next`` and return the yielded value."""
        return self.send(None, should_stop=should_stop)

    def send(
        self,
        value: _SendT_contra,
        /,
        *,
        should_stop: Union[Missing, bool, None] = MISSING,
    ) -> _YieldT_co:
        if (
            not isinstance(should_stop, bool)
            and should_stop is not None
            and should_stop is not MISSING
        ):
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
        if (
            not isinstance(should_stop, bool)
            and should_stop is not None
            and should_stop is not MISSING
        ):
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


class GeneratorExecutorCollection(MutableSequenceProxy[_GeneratorExecutorT]):
    def __init__(
        self,
        /,
        generator_executors: SequenceData[Union[_GeneratorExecutorT, Generator]] = None,
    ):
        super().__init__(sequence_data=generator_executors)

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
        gen_queue = tuple(self)
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
        return context_manager_stack(tuple(self))

    def __setitem__(
        self,
        key: Union[SupportsIndex, slice],
        value: Union[
            _GeneratorExecutorT,
            Generator,
            Iterable[Union[_GeneratorExecutorT, Generator]],
        ],
        /,
    ):
        if isinstance(key, slice):
            value = (
                (
                    item
                    if isinstance(item, GeneratorExecutor)
                    else GeneratorExecutor(item)
                )
                for item in cast(Iterable[Union[_GeneratorExecutorT, Generator]], value)
            )
        else:
            value = (
                value
                if isinstance(value, GeneratorExecutor)
                else GeneratorExecutor(value)
            )
        return super().__setitem__(key, value)

    def insert(
        self, index: SupportsIndex, object: Union[_GeneratorExecutorT, Generator], /
    ):
        object = (
            object
            if isinstance(object, GeneratorExecutor)
            else GeneratorExecutor(object)
        )
        return super().insert(index, object)
