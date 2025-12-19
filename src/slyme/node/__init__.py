from abc import ABC, abstractmethod
from slyme.utils.typing import (
    Callable,
    TypeVar,
    Union,
    Any,
    Self,
    Generator,
)
from slyme.utils.collection.base import SequenceData
from slyme.utils.composite import Component, ComponentContainer, ComponentCollection
from slyme.utils.execution import GeneratorExecutorCollection
from slyme.utils.execution.manager import check_stop_flag
from slyme.utils.freeze import FreezeMixin
from slyme.utils.store import KeyFieldMixin
from slyme.utils.inspect import resolve_instance_classname
from slyme.context import Context
from .exception import (
    NodeTerminate,
    NodeBreak,
    NodeContinue,
    NodeExceptionRecord,
    NodeException,
    NodeWrapperExceptionRecord,
)

_ComponentT = TypeVar("_ComponentT", bound="Component")
_ComponentContainerT = TypeVar("_ComponentContainerT", bound="ComponentContainer")


class NodeElement(KeyFieldMixin, FreezeMixin, ABC):
    """Base class for all node-related entities, integrating essential mixins.

    Design Note:
        This class is intentionally kept minimal to ensure forward compatibility.
        It strictly encapsulates only the most fundamental attributes to prevent
        future subclasses from inheriting redundant or conflicting functionality
        that they may not require.
    """

    pass


class NodeComponent(Component[_ComponentT, _ComponentContainerT], NodeElement):
    """Base class for Node and AsyncNode."""

    # Node search operations.
    def get_by_class(
        self, cls_info: Union[type, tuple[type, ...]], /, *, strategy: str = "depth"
    ) -> tuple[_ComponentT, ...]:
        """Get the nodes that are instances of ``cls``."""
        return tuple(
            self.composite_filter(
                lambda node: isinstance(node, cls_info), strategy=strategy
            )
        )

    def get_by_filter(
        self, func: Callable[[_ComponentT], bool], /, *, strategy: str = "depth"
    ) -> tuple[_ComponentT, ...]:
        """Get the nodes by the given filter function."""
        return tuple(self.composite_filter(func, strategy=strategy))

    # Node render APIs.
    def render(self, strategy: str = "vanilla", /, **kwargs) -> Any:
        """
        Render the node structure using the specified strategy.
        """
        render_cls = RENDER_REGISTRY.get(strategy)
        if not render_cls:
            raise ValueError(
                f"Unknown render strategy: `{strategy}`. Available: {list(RENDER_REGISTRY.keys())}"
            )

        render = render_cls()
        return render.render(self, **kwargs)

    def _get_render_info(self) -> "RenderInfo":
        """ """
        return RenderInfo(
            classname=resolve_instance_classname(self),
            attr_dict={},
        )

    def check_dependency(self, ctx: Context, /, *, strategy: str = "vanilla", **kwargs):
        """Check the dependency graph of the node structure."""
        checker_cls = DEPENDENCY_REGISTRY.get(strategy)
        if not checker_cls:
            raise ValueError(
                f"Unknown dependency check strategy: `{strategy}`. Available: {list(DEPENDENCY_REGISTRY.keys())}"
            )

        checker = checker_cls()
        return checker.check(self, ctx, **kwargs)


class Node(NodeComponent["Node", "NodeContainer"]):
    """ """

    def __init__(self, /, node_wrappers: SequenceData["NodeWrapper"] = None, **kwargs):
        super().__init__(**kwargs)
        self.node_wrappers = NodeWrapperCollection(children=node_wrappers)

    # Core APIs.
    @abstractmethod
    def _execute(self, ctx: Context) -> None:
        """Custom execution operations."""
        pass

    def execute(self, ctx: Context) -> None:
        """Outer execute API."""
        try:
            self.node_wrappers.execute_wrappers(ctx, self)
        # Node Interrupt.
        except NodeTerminate as nt:
            # set ``source_node`` to the nearest node
            if nt.source_node is None:
                nt.source_node = self
            raise
        except (NodeBreak, NodeContinue):
            raise
        # Node Exceptions should not be processed.
        except NodeException:
            raise
        # Other Exception(s).
        except Exception as e:
            raise NodeExceptionRecord(exception_node=self, exception=e)

    def _get_render_info(self) -> "RenderInfo":
        render_info = super()._get_render_info()
        if len(self.node_wrappers) > 0:
            render_info.attr_dict["node_wrappers"] = self.node_wrappers
        return render_info


class NodeContainer(Node, ComponentContainer[Node, "NodeContainer"]):
    """
    ABC for HandlerContainers.
    """

    def __init__(
        self,
        /,
        node_wrappers: SequenceData["NodeWrapper"] = None,
        children: SequenceData[Node] = None,
        **kwargs,
    ):
        super().__init__(node_wrappers=node_wrappers, children=children, **kwargs)

    def _execute(self, ctx: Context) -> None:
        self._execute_children(ctx)

    def _execute_children(self, ctx: Context) -> None:
        """Sequentially execute children nodes.

        `NodeContinue` interrupt will stop the execution process and return.
        """
        try:
            for node in self:
                node.execute(ctx)
        except NodeContinue:
            # continue in the container
            pass

    def execute(self, ctx: Context) -> None:
        try:
            super().execute(ctx)
        except NodeBreak:
            # break out of the container
            pass


class NodeWrapper(Component["NodeWrapper", "NodeWrapperCollection"], NodeElement):
    """Defines the interface for auxiliary logic attached to a Node.

    Design Note:
        NodeWrappers are not considered "first-class citizens" of the primary
        graph topology. Instead, they serve as supplementary components that
        decorate, intercept, or augment the execution flow of their host Node.
    """

    @abstractmethod
    def _execute_yield(self, ctx: Context, wrapped: Union[Node, Self]) -> Generator:
        """Core node wrapper API for custom operations."""
        yield

    def execute_yield(self, ctx: Context, wrapped: Node) -> Generator:
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


class NodeWrapperCollection(ComponentCollection[NodeWrapper]):
    """Manages the storage and execution of a sequence of NodeWrappers.

    Design Note:
        - Flat Structure: Unlike `NodeComponent` which forms recursive graphs,
          this collection is designed to remain flat.
        - Complexity Control: Deeply nesting wrappers or collections is
          strictly discouraged. A flat architecture limits stack depth and
          ensures execution flow remains transparent, preventing excessive
          debugging complexity.
    """

    def execute_wrappers(self, ctx: Context, wrapped: Node) -> None:
        """"""
        if len(self) == 0:
            # Directly call `_execute`.
            return wrapped._execute(ctx)
        with GeneratorExecutorCollection(
            wrapper.execute_yield(ctx, wrapped) for wrapper in self
        ).stack_context_manager() as vals:
            if not check_stop_flag(vals):
                wrapped._execute(ctx)


from .render import RENDER_REGISTRY, RenderInfo
from .dependency import DEPENDENCY_REGISTRY
