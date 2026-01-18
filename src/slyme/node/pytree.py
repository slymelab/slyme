"""
Node tree structure engine and validation logic.
"""

from collections.abc import Iterable
from typing import Any, Optional, cast

from slyme.utils.pytree import (
    AttributeKey,
    PyTreeAux,
    PyTreeEngine,
    PYTREE_ENGINE_REGISTRY,
)
from slyme.node.base import Node, NodeElement, NodeExpression
from slyme.node.wrapper import NodeWrapper

__all__ = [
    "NODE_PYTREE_ENGINE",
    "NodeStructureError",
    "check_node_consistency",
]

# Register the node pytree engine.
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
# NOTE: Set strict=False to allow safe re-registration or overriding by subclasses.
NODE_PYTREE_ENGINE.register(
    NodeElement,
    _flatten_node_element,
    _unflatten_node_element,
    strict=False,
)


class NodeStructureError(TypeError):
    """Raised when the Node structure violates consistency rules."""

    pass


def check_node_consistency(root: Node) -> None:
    """
    Validates the structural consistency of a Node tree.

    Enforces the following topology rules:
    1.  **Type Constraint**: Nested structures must only contain ``Node`` or
        ``NodeExpression`` types.
    2.  **Wrapper Placement**: ``NodeWrapper`` instances are only allowed within
        the ``node_wrappers`` attribute of a ``Node``.
    3.  **Containment Rules**: ``NodeExpression`` and ``NodeWrapper`` cannot
        contain ``Node`` or ``NodeWrapper`` instances (downward closure).
    4.  **Purity**: Containers (lists, dicts) must be homogeneous regarding
        ``Node`` and ``NodeExpression`` (cannot mix them, nor mix with other types).

    Args:
        root: The root ``Node`` of the tree to validate.

    Raises:
        NodeStructureError: If any rule is violated.
        TypeError: If root is not a ``Node``.
    """
    if not isinstance(root, Node):
        raise TypeError(f"Root must be a Node, got {type(root)}")

    # Use a set to track visited objects and handle cyclic graphs.
    visited: set[int] = set()

    def _is_node_element(obj: Any) -> bool:
        return isinstance(obj, (Node, NodeExpression, NodeWrapper))

    def _validate_recursive(obj: Any) -> None:
        if id(obj) in visited:
            return

        # Only validate known structural units.
        if not _is_node_element(obj):
            return

        visited.add(id(obj))

        is_node = isinstance(obj, Node)
        is_expr = isinstance(obj, NodeExpression)
        is_wrapper = isinstance(obj, NodeWrapper)

        # Inspect immediate attributes using the engine.
        # NOTE: We stop the engine at `_is_node_element` to get the containers
        # holding them, rather than flattening the elements themselves.
        direct_attrs_with_path = [
            (p, c)
            for p, c in NODE_PYTREE_ENGINE.iter_with_path(
                obj, is_leaf=lambda x, _: x is not obj
            )
            if p
        ]

        for path, attr_value in direct_attrs_with_path:
            # path[0] should be AttributeKey for direct attributes of NodeElement.
            if not isinstance(path[0], AttributeKey):
                continue

            attr_name = path[0].name
            path_info = f"{type(obj).__name__}.{attr_name}"

            # Gather all children in this attribute structure.
            leaves = list(
                NODE_PYTREE_ENGINE.iter(
                    attr_value, is_leaf=lambda x, _: _is_node_element(x)
                )
            )

            nodes = [x for x in leaves if isinstance(x, Node)]
            exprs = [x for x in leaves if isinstance(x, NodeExpression)]
            wrappers = [x for x in leaves if isinstance(x, NodeWrapper)]
            others = [x for x in leaves if not _is_node_element(x)]

            has_node = bool(nodes)
            has_expr = bool(exprs)
            has_wrapper = bool(wrappers)
            has_other = bool(others)

            # --- Rule Validation ---

            # Rule 1 & 2: NodeWrapper restrictions
            if has_wrapper:
                if is_node:
                    if attr_name != "node_wrappers":
                        raise NodeStructureError(
                            f"Invalid wrapper placement at '{path_info}': "
                            "NodeWrappers must be in 'node_wrappers'."
                        )
                else:
                    raise NodeStructureError(
                        f"Invalid containment at '{path_info}': "
                        f"{type(obj).__name__} cannot hold NodeWrapper."
                    )

            # Rule 3: Downward closure (Node/Wrapper in Expr/Wrapper)
            if (is_expr or is_wrapper) and has_node:
                raise NodeStructureError(
                    f"Invalid containment at '{path_info}': "
                    f"{type(obj).__name__} cannot hold Node."
                )

            # Rule 4: Container Purity
            if has_node or has_expr:
                if has_node and has_expr:
                    raise NodeStructureError(
                        f"Mixed content at '{path_info}': "
                        "Cannot mix Node and NodeExpression."
                    )
                if has_other:
                    raise NodeStructureError(
                        f"Impure container at '{path_info}': "
                        "Found Node/NodeExpression mixed with other types."
                    )

            # --- Recursion ---
            for child in nodes + exprs + wrappers:
                _validate_recursive(child)

    _validate_recursive(root)
