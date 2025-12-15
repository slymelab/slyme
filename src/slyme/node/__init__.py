from abc import ABC, abstractmethod
from slyme.utils.typing import (
    Callable,
    TypeVar,
    Union,
    Any,
    Self,
    Generator,
)
from slyme.utils.constant import STOP
from slyme.utils.collection.base import SequenceData
from slyme.utils.composite import Component, ComponentContainer, ComponentCollection
from slyme.utils.execution import (
    GeneratorExecutorCollection,
    GeneratorExecutor,
)
from slyme.utils.execution.manager import check_stop_flag
from slyme.utils.freeze import FreezeMixin
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


class NodeBase(Component[_ComponentT, _ComponentContainerT], FreezeMixin, ABC):
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

    def check_dependency_graph(self, ctx: Context):
        pass


class Node(NodeBase["Node", "NodeContainer"]):
    """ """

    def __init__(self, /, node_wrappers: SequenceData["NodeWrapper"] = None, **kwargs):
        super().__init__(**kwargs)
        self.node_wrappers = NodeWrapperCollection[NodeWrapper](children=node_wrappers)

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
        render_info.attr_dict.update(
            {
                "node_wrappers": self.node_wrappers,
            }
        )
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


class NodeWrapper(NodeContainer):

    def _execute(self, ctx: Context) -> None:
        # The ``wrapped`` param is set to ``self``.
        with GeneratorExecutor(self._execute_yield(ctx, self)) as val:
            if val is not STOP:
                self._execute_children(ctx)

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
