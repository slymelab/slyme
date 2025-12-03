from abc import ABC, abstractmethod
from slyme.utils.typing import (
    Callable,
    TypeVar,
    Generic,
    Union,
    Any,
)
from slyme.utils.collection.base import SequenceData
from slyme.utils.composite import Component, ComponentContainer
from slyme.utils.freeze import FreezeMixin
from slyme.utils.inspect import resolve_instance_classname
from slyme.context import Context
from .exception import (
    NodeTerminate,
    NodeBreak,
    NodeContinue,
    NodeExceptionRecord,
    NodeException,
)

_ComponentT = TypeVar("_ComponentT", bound="Component")
_ComponentContainerT = TypeVar("_ComponentContainerT", bound="ComponentContainer")
_NodeT = TypeVar("_NodeT", bound="Node")
_NodeContainerT = TypeVar("_NodeContainerT", bound="NodeContainer")
_NodeWrapperT = TypeVar("_NodeWrapperT", bound="NodeWrapper")


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
            raise ValueError(f"Unknown render strategy: `{strategy}`. Available: {list(RENDER_REGISTRY.keys())}")

        render = render_cls()
        return render.render(self, **kwargs)

    def _get_render_info(self) -> "RenderInfo":
        """
        """
        return RenderInfo(
            classname=resolve_instance_classname(self),
            attr_dict={},
        )

    def check_dependency_graph(self):
        pass


class Node(
    NodeBase[_NodeT, _NodeContainerT], Generic[_NodeT, _NodeContainerT, _NodeWrapperT]
):
    """
    
    """

    def __init__(self, /, node_wrappers: SequenceData[_NodeWrapperT] = None, **kwargs):
        super().__init__(**kwargs)
        self.node_wrappers = NodeWrapperCollection[_NodeWrapperT](
            children=node_wrappers
        )

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
        render_info.attr_dict.update({
            "node_wrappers": self.node_wrappers,
        })
        return render_info


class NodeContainer(
    Node[_NodeT, _NodeContainerT, _NodeWrapperT],
    ComponentContainer[_NodeT, _NodeContainerT],
    Generic[_NodeT, _NodeContainerT, _NodeWrapperT],
):
    """
    ABC for HandlerContainers.
    """

    def __init__(
        self,
        /,
        node_wrappers: SequenceData[_NodeWrapperT] = None,
        children: SequenceData[_NodeT] = None,
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


from .wrapper import NodeWrapper, NodeWrapperCollection
from .render import RENDER_REGISTRY, RenderInfo
