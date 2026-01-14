from abc import abstractmethod
from contextlib import contextmanager
from collections.abc import Generator
from slyme.context import Context
from slyme.utils.collection import MutableSequenceProxy
from .base import NodeElement, Node, NodeExpression
from .exception import (
    NodeException,
    NodeWrapperExceptionRecord,
)


class NodeWrapper(NodeElement):
    """Defines the interface for auxiliary logic attached to a Node.

    Design Note:
        NodeWrappers are not considered "first-class citizens" of the primary
        graph topology. Instead, they serve as supplementary components that
        decorate, intercept, or augment the execution flow of their host Node.
    """

    @abstractmethod
    @contextmanager
    def wrap(self, ctx: Context, wrapped: Node) -> Generator:
        """Core node wrapper API for custom operations."""
        yield

    def __call__(self, ctx: Context, wrapped: Node) -> Generator:
        """A mixin method that wraps the generator returned by ``_execute_yield``."""
        try:
            yield from self.wrap(ctx, wrapped)
        # directly raise
        except NodeException:
            raise
        # wrap other Exception
        except Exception as e:
            raise NodeWrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            )


class NodeWrapperList(MutableSequenceProxy[NodeWrapper]):
    """Manages the storage and execution of a sequence of NodeWrappers.

    Design Note:
        - Flat Structure: Unlike `NodeComponent` which forms recursive graphs,
          this collection is designed to remain flat.
        - Complexity Control: Deeply nesting wrappers or collections is
          strictly discouraged. A flat architecture limits stack depth and
          ensures execution flow remains transparent, preventing excessive
          debugging complexity.
    """

    def __call__(self, ctx: Context, wrapped: Node) -> None:
        """"""
        if len(self) == 0:
            # Directly call `_execute`.
            return wrapped.execute(ctx)
        with GeneratorExecutorList(
            wrapper(ctx, wrapped) for wrapper in self
        ).stack_context_manager() as vals:
            if not check_stop_flag(vals):
                wrapped.execute(ctx)
