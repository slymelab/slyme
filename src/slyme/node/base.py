from abc import ABC, abstractmethod
from enum import Enum
from collections.abc import Generator, Iterable
from typing import (
    TypeVar,
    Any,
    Generic,
    cast,
)
from contextlib import ExitStack, contextmanager
from slyme.utils.collection import SequenceData
from slyme.utils.pytree import (
    AttributeKey,
    PyTreeAux,
    PyTreeEngine,
    PYTREE_ENGINE_REGISTRY,
)
from slyme.context import Context
from .exception import (
    NodeTerminate,
    NodeExceptionRecord,
    NodeException,
    NodeExpressionExceptionRecord,
    NodeWrapperExceptionRecord,
)

_R = TypeVar("_R")
Stop = Enum("Stop", ["MARK"])
STOP = Stop.MARK


class NodeElement(ABC):
    """Base class for all node-related entities, integrating essential mixins.

    Design Note:
        This class is intentionally kept minimal to ensure forward compatibility.
        It strictly encapsulates only the most fundamental attributes to prevent
        future subclasses from inheriting redundant or conflicting functionality
        that they may not require.
    """

    def __repr__(self) -> str:
        return get_render_string(self)

    def extra_repr(self) -> str:
        return ""

    def type_repr(self) -> str:
        return type(self).__name__


# NOTE: Register the node pytree engine.
NODE_PYTREE_ENGINE = PyTreeEngine("node_engine")
PYTREE_ENGINE_REGISTRY.register(NODE_PYTREE_ENGINE, key="node_engine")


def _flatten_node_element(obj: NodeElement) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Generic flatten handler for ``NodeElement`` subclasses.

    Flattens the object's ``__dict__`` to expose attributes as children,
    using ``AttributeKey`` for semantic path tracking.
    """
    # NOTE: Directly access __dict__ to avoid getattr overhead and potential
    # side effects triggered by properties or descriptors.
    data = obj.__dict__
    keys = tuple(data.keys())
    children = tuple(data.values())
    # Wrap keys in AttributeKey for path reconstruction.
    rich_keys = tuple(AttributeKey(k) for k in keys)
    return children, PyTreeAux(keys=rich_keys)


def _unflatten_node_element(children: Iterable[Any], tree_aux: PyTreeAux) -> Any:
    """
    Generic unflatten handler for ``NodeElement`` subclasses.

    Restores the object state by bypassing ``__init__`` and directly updating ``__dict__``.
    """
    cls = tree_aux.cls
    if cls is None:
        raise ValueError(
            "Missing class info in PyTreeAux for NodeElement unflattening."
        )

    # NOTE: Bypass __init__ to creating a raw instance, strictly mimicking
    # the behavior of generic serialization/deserialization.
    obj: object = object.__new__(cls)  # type: ignore

    if tree_aux.keys is None:
        raise ValueError(f"Missing keys for unflattening {cls.__name__}")

    # Extract raw keys from AttributeKey to restore __dict__.
    raw_keys = [cast("AttributeKey", k).name for k in tree_aux.keys]  # type: ignore
    obj.__dict__.update(zip(raw_keys, children))
    return obj


# Register the handlers.
NODE_PYTREE_ENGINE.register(
    NodeElement,
    _flatten_node_element,
    _unflatten_node_element,
)


# Custom base classes.
class Node(NodeElement):
    """ """

    # TODO: Helper function for nodes/expressions/refs/check_structure/check_dependency, etc.

    def __init__(
        self, /, *, node_wrappers: SequenceData["NodeWrapper"] = None, **kwargs
    ):
        super().__init__(**kwargs)
        self.node_wrappers = list(node_wrappers) if node_wrappers is not None else []

    # Core APIs.
    @abstractmethod
    def execute(self, ctx: Context, /) -> None:
        """Custom execution operations."""
        pass

    def __call__(self, ctx: Context, /) -> None:
        """Outer execute API."""
        try:
            with ExitStack() as stack:
                should_stop = False
                for wrapper in self.node_wrappers:
                    val = stack.enter_context(wrapper(ctx, self))
                    if val is STOP:
                        should_stop = True
                        break
                if not should_stop:
                    self.execute(ctx)
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


class NodeExpression(NodeElement, Generic[_R]):

    @abstractmethod
    def evaluate(self, ctx: Context, /) -> _R:
        pass

    def __call__(self, ctx: Context, /) -> _R:
        try:
            return self.evaluate(ctx)
        # directly raise
        except NodeException:
            raise
        # wrap other Exception
        except Exception as e:
            raise NodeExpressionExceptionRecord(exception_node=self, exception=e)


class NodeWrapper(NodeElement):
    """Defines the interface for auxiliary logic attached to a Node.

    Design Note:
        NodeWrappers are not considered "first-class citizens" of the primary
        graph topology. Instead, they serve as supplementary components that
        decorate, intercept, or augment the execution flow of their host Node.
    """

    @abstractmethod
    @contextmanager
    def wrap(self, ctx: Context, wrapped: Node, /) -> Generator:
        """Core node wrapper API for custom operations."""
        yield

    @contextmanager
    def __call__(self, ctx: Context, wrapped: Node, /) -> Generator:
        """A mixin method that wraps the generator returned by ``_execute_yield``."""
        try:
            with self.wrap(ctx, wrapped) as val:
                yield val
        # directly raise
        except NodeException:
            raise
        # wrap other Exception
        except Exception as e:
            raise NodeWrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            )


from .render import get_render_string
from .validator import check_node_structure, DEPENDENCY_REGISTRY
