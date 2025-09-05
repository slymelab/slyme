from abc import ABC, abstractmethod
from slyme.utils.typing import (
    Callable,
    TypeVar,
    Generic,
    Union,
    Any,
    Tuple,
    Dict,
    List,
    Type,
)
from slyme.utils.collection.base import SequenceData
from slyme.utils.composite import Component, ComponentContainer
from slyme.context import Context
from .exception import (
    NodeTerminate,
    NodeBreak,
    NodeContinue,
    NodeExceptionRecord,
    NodeException,
)

_NodeT = TypeVar("_NodeT", bound="Node")
_NodeContainerT = TypeVar("_NodeContainerT", bound="NodeContainer")
_NodeWrapperT = TypeVar("_NodeWrapperT", bound="NodeWrapper")


class Node(
    Component[_NodeT, _NodeContainerT],
    ABC,
    Generic[_NodeT, _NodeContainerT, _NodeWrapperT],
):
    """
    ABC for all handlers.

    Generics:

    ```Python
    HandlerABC[
        _HandlerT: HandlerABC,
        _HandlerContainerT: HandlerContainerABC,
        _HandlerWrapperT: HandlerWrapperABC,
        _HandlerWrapperContainerT: HandlerWrapperContainerABC,
        _ContextT
    ]
    ```
    """

    def __init__(self, /, node_wrappers: SequenceData[_NodeWrapperT] = None, **kwargs):
        super().__init__(**kwargs)
        self.node_wrappers = NodeWrapperCollection[_NodeWrapperT](
            sequence_data=node_wrappers
        )

    # Core APIs.
    @abstractmethod
    def _execute(self, ctx: Context) -> None:
        """Custom execution operations."""
        pass

    def execute(self, ctx: Context) -> None:
        """Outer execute API."""
        try:
            return self.node_wrappers.execute_wrappers(ctx, self)
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

    #
    # Handler search operations.
    #

    @abstractmethod
    def get_by_class(self, __class: Union[Type, Tuple[Type, ...]]) -> List[_NodeT]:
        """
        Get the handlers that are instances of ``__class``.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @abstractmethod
    def get_by_filter(self, __func: Callable[[_NodeT], bool]) -> List[_NodeT]:
        """
        Get the handlers by the given filter function.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    #
    # Handler display APIs.
    #

    @abstractmethod
    def display(self, *args, **kwargs) -> None:
        """
        Display the handler structure.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @abstractmethod
    def get_display_attr_dict(self) -> Dict[str, Any]:
        """
        Return the names and values of attributes to be displayed.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @abstractmethod
    def get_classname(self) -> str:
        """
        Get the class name of the handler (for display).

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass


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
        return self._execute_children(ctx)

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
            return super().execute(ctx)
        except NodeBreak:
            # break out of the container
            pass


from .wrapper import NodeWrapper, NodeWrapperCollection
