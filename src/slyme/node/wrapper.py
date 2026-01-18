from abc import abstractmethod
from contextlib import contextmanager
from collections.abc import Generator
from slyme.context import Context
from .base import NodeElement, Node
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

    @contextmanager
    def __call__(self, ctx: Context, wrapped: Node) -> Generator:
        """A mixin method that wraps the generator returned by ``_execute_yield``."""
        try:
            with self.wrap(ctx, wrapped):
                yield
        # directly raise
        except NodeException:
            raise
        # wrap other Exception
        except Exception as e:
            raise NodeWrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            )
