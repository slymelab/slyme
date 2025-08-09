"""
ABC for execution control.
"""

from abc import ABC, abstractmethod
from slyme.utils.typing.native import (
    ContextManager,
    TypeVar,
    Generic,
    Generator,
    Any,
    Union,
    Tuple,
)
from slyme.utils.typing.extension import (
    Missing,
    MISSING,
)
from .base.collection import BaseListABC
from .descriptor import DescriptorProtocol

_GeneratorExecutorT = TypeVar("_GeneratorExecutorT")
_YieldT_co = TypeVar("_YieldT_co", covariant=True)
_SendT_contra = TypeVar("_SendT_contra", contravariant=True)
_ReturnT_co = TypeVar("_ReturnT_co", covariant=True)
_EnterT_co = TypeVar("_EnterT_co", covariant=True)

#
# Generator ABC.
#


class GeneratorExecutorABC(
    Generator[_YieldT_co, _SendT_contra, _ReturnT_co],
    ABC,
    Generic[_YieldT_co, _SendT_contra, _ReturnT_co],
):
    """ABC of ``GeneratorExecutor``.

    ``send`` and ``throw`` processes the ``StopIteration`` exception based on ``should_stop``. NOTE: If ``should_stop`` is ``MISSING``,
    then ``StopIteration`` will not be suppressed, and we should process the exception manually, else ``StopIteration`` will be checked
    based on the ``should_stop`` param and suppressed if the check passes.

    Parameters:
        should_stop (Union[Missing, bool, None], optional):
            Do nothing if ``should_stop`` is ``MISSING``. If ``should_stop`` is ``True``, then raise ``RuntimeError`` if ``StopIteration``
            is not raised. Similarly, if ``should_stop`` is ``False``, then raise ``RuntimeError`` if ``StopIteration`` is raised. If
            ``should_stop`` is ``None``, then suppress any ``StopIteration`` exception without any check. Defaults to ``MISSING``.
    """

    gen: DescriptorProtocol[Generator]

    def next(self, *, should_stop: Union[Missing, bool, None] = MISSING) -> _YieldT_co:
        """
        Call ``next`` and return the yielded value.
        """
        return self.send(None, should_stop=should_stop)

    @abstractmethod
    def send(
        self,
        value: _SendT_contra,
        /,
        *,
        should_stop: Union[Missing, bool, None] = MISSING,
    ) -> _YieldT_co:
        """Call ``send`` and return the yielded value."""
        pass

    # NOTE: Forward compatibility (in future versions of Python):
    # def throw(self, value, /, *, should_stop: Union[Missing, bool, None] = MISSING): ...
    @abstractmethod
    def throw(
        self,
        exc,
        value=None,
        traceback=None,
        /,
        *,
        should_stop: Union[Missing, bool, None] = MISSING,
    ) -> _YieldT_co:
        """Throw an exception and return the yielded value if possible."""
        pass


class ContextGeneratorExecutorABC(
    GeneratorExecutorABC[_YieldT_co, _SendT_contra, _ReturnT_co],
    ContextManager[_YieldT_co],
    Generic[_YieldT_co, _SendT_contra, _ReturnT_co],
):
    pass


class GeneratorExecutorCollectionABC(
    BaseListABC[_GeneratorExecutorT],
    ABC,
    Generic[_GeneratorExecutorT],
):
    @abstractmethod
    def queue_context_manager(self) -> ContextManager[Tuple]:
        """
        Queue execution. NOTE: This method should be implemented by either using
        ``@contextmanager`` or directly returning a context manager.
        """
        pass

    @abstractmethod
    def stack_context_manager(self) -> ContextManager[Tuple]:
        """
        Stack execution. NOTE: This method should be implemented by either using
        ``@contextmanager`` or directly returning a context manager.
        """
        pass

    @abstractmethod
    def __setitem__(self, __key, __value):
        """
        NOTE: The implementation class should override this method in order to dynamically
        wrap a generator into a generator executor.
        """
        return super().__setitem__(__key, __value)

    @abstractmethod
    def insert(self, __index, __object):
        """
        NOTE: The implementation class should override this method in order to dynamically
        wrap a generator into a generator executor.
        """
        return super().insert(__index, __object)


class YieldTemplateABC(ABC, Generic[_EnterT_co]):
    """
    Provide a method template for yield function.
    """

    @abstractmethod
    def gen_yield(self, *args, **kwargs) -> Generator[_EnterT_co, Any, Any]:
        """
        A generator method used to build a context manager.
        """
        yield

    @abstractmethod
    def call_gen_yield(self, *args, **kwargs) -> Generator[_EnterT_co, Any, Any]:
        """
        A mixin method that wraps ``gen_yield``.
        """
        yield
