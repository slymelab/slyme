from abc import abstractmethod
from slyme.utils.typing import TypeVar, Union, Generator, Self
from slyme.utils.collection import MutableSequenceProxy
from slyme.utils.execution import (
    GeneratorExecutorCollection,
    check_stop_flag,
    generator_context_manager,
)
from slyme.utils.constant import STOP
from slyme.context import Context
from . import Node, NodeContainer
from .exception import NodeException, NodeWrapperExceptionRecord

_NodeT = TypeVar("_NodeT", bound=Node)
_NodeContainerT = TypeVar("_NodeContainerT", bound=NodeContainer)
_NodeWrapperT = TypeVar("_NodeWrapperT", bound="NodeWrapper")


class NodeWrapper(NodeContainer[_NodeT, _NodeContainerT, _NodeWrapperT]):

    def _execute(self, ctx: Context) -> None:
        # The ``wrapped`` param is set to ``self``.
        with generator_context_manager(self._execute_yield(ctx, self)) as val:
            if val is not STOP:
                self._execute_children(ctx)

    @abstractmethod
    def _execute_yield(
        self, ctx: Context, wrapped: Union[_NodeT, Self]
    ) -> Generator:
        """Core node wrapper API for custom operations."""
        yield

    def execute_yield(self, ctx: Context, wrapped: _NodeT) -> Generator:
        """A mixin method that wraps the generator returned by ``_execute_yield``."""
        try:
            yield from self._execute_yield(ctx, wrapped)
        # directly raise
        except NodeException:
            raise
        # wrap other Exception
        except Exception as e:
            raise NodeWrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            )


class NodeWrapperCollection(MutableSequenceProxy[_NodeWrapperT]):

    def execute_wrappers(self, ctx: Context, wrapped: _NodeT) -> None:
        """"""
        if len(self) == 0:
            # Directly call `_execute`.
            return wrapped._execute(ctx)
        with GeneratorExecutorCollection(
            wrapper.execute_yield(ctx, wrapped) for wrapper in self
        ).stack_context_manager() as vals:
            if not check_stop_flag(vals):
                wrapped._execute(ctx)
