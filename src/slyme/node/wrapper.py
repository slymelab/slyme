from abc import abstractmethod
from slyme.utils.typing import (
    Generator,
)
from slyme.utils.attribute import AttributeTypeRegistry
from slyme.utils.composite import Component, ComponentList
from slyme.utils.execution import GeneratorExecutorList
from slyme.utils.execution.manager import check_stop_flag
from slyme.context import Context
from .base import NodeElement, Node, NodeExpression
from .exception import (
    NodeException,
    NodeWrapperExceptionRecord,
)


class NodeWrapper(Component["NodeWrapper"], NodeElement):
    """Defines the interface for auxiliary logic attached to a Node.

    Design Note:
        NodeWrappers are not considered "first-class citizens" of the primary
        graph topology. Instead, they serve as supplementary components that
        decorate, intercept, or augment the execution flow of their host Node.
    """

    def _init_attr_registry(self, registry: AttributeTypeRegistry) -> None:
        super()._init_attr_registry(registry)
        registry.register_type(NodeExpression)

    @abstractmethod
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


class NodeWrapperList(ComponentList[NodeWrapper]):
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
