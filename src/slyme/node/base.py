from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import (
    TypeVar,
    Union,
    Any,
    Generic,
)
from slyme.utils.collection.base import SequenceData
from slyme.utils.store import StoreKeyMixin
from slyme.context import Context
from .exception import (
    NodeTerminate,
    NodeExceptionRecord,
    NodeException,
    NodeExpressionExceptionRecord,
)

_R = TypeVar("_R")


class NodeElement(StoreKeyMixin, ABC):
    """Base class for all node-related entities, integrating essential mixins.

    Design Note:
        This class is intentionally kept minimal to ensure forward compatibility.
        It strictly encapsulates only the most fundamental attributes to prevent
        future subclasses from inheriting redundant or conflicting functionality
        that they may not require.
    """

    pass


class NodeComponent(NodeElement):
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


class Node(NodeComponent["Node"]):
    """ """

    def __init__(self, /, node_wrappers: SequenceData["NodeWrapper"] = None, **kwargs):
        super().__init__(**kwargs)
        self.node_wrappers = NodeWrapperList(children=node_wrappers)

    # Core APIs.
    @abstractmethod
    def execute(self, ctx: Context) -> None:
        """Custom execution operations."""
        pass

    def __call__(self, ctx: Context) -> None:
        """Outer execute API."""
        try:
            self.node_wrappers(ctx, self)
        # Node Interrupt.
        except (
            NodeTerminate,
            NodeExpressionExceptionRecord,
        ) as e:
            # set ``source_node`` to the nearest node
            if e.source_node is None:
                e.source_node = self
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


class NodeExpression(NodeElement, Generic[_R]):

    @abstractmethod
    def evaluate(self, ctx: Context) -> _R:
        pass

    def __call__(self, ctx: Context) -> _R:
        try:
            return self.evaluate(ctx)
        # directly raise
        except NodeException:
            raise
        # wrap other Exception
        except Exception as e:
            raise NodeExpressionExceptionRecord(exception_node=self, exception=e)


from .wrapper import NodeWrapper, NodeWrapperList
from .render import RENDER_REGISTRY, RenderInfo
from .dependency import DEPENDENCY_REGISTRY
