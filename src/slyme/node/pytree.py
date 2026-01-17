"""
Node tree structure and validation logic.
"""

from typing import Any, Iterable
from slyme.utils.pytree import (
    PyTreeEngine,
    PyTreeAux,
    AttributeKey,
    PYTREE_ENGINE_REGISTRY,
)
from slyme.node.base import Node, NodeExpression, NodeElement
from slyme.node.wrapper import NodeWrapper

# =============================================================================
# 1. Node Tree Engine & Registration
# =============================================================================

NODE_PYTREE_ENGINE = PyTreeEngine("node_pytree")
PYTREE_ENGINE_REGISTRY.register(NODE_PYTREE_ENGINE, key="node_pytree")


def _flatten_node_element(obj: NodeElement) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Generic flatten for Node objects (Node, NodeExpression, NodeWrapper).
    Flattens the __dict__ to allow structural traversal.
    """
    # Capture the class to allow precise reconstruction (if needed)
    # and use AttributeKey for semantic paths.
    data = obj.__dict__
    keys = tuple(data.keys())
    children = tuple(data.values())
    
    rich_keys = tuple(AttributeKey(k) for k in keys)
    
    return children, PyTreeAux(
        keys=rich_keys
    )


def _unflatten_node_element(children: Iterable[Any], tree_aux: PyTreeAux) -> Any:
    """
    Generic unflatten for Node objects.
    Reconstructs the object by bypassing __init__ and restoring __dict__.
    """
    cls = tree_aux.cls
    obj: NodeElement = object.__new__(cls)
    
    if tree_aux.keys is None:
        raise ValueError(f"Missing keys for unflattening {cls.__name__}")

    # Extract raw keys from AttributeKey
    raw_keys = [k.key for k in tree_aux.keys]
    obj.__dict__.update(zip(raw_keys, children))
    return obj


# Register handlers
# Note: strict=False allows overwriting if re-imported
NODE_PYTREE_ENGINE.register(NodeElement, _flatten_node_element, _unflatten_node_element, strict=False)


# =============================================================================
# 2. Validation Logic
# =============================================================================

class NodeStructureError(TypeError):
    """Raised when the Node structure violates the consistency rules."""
    pass


def _is_node_element(obj: Any) -> bool:
    """Check if the object is a structural unit in the Node system."""
    return isinstance(obj, (Node, NodeExpression, NodeWrapper))


def check_node_consistency(root: Node) -> None:
    """
    Validates the structural consistency of the Node tree starting from ``root``.
    
    Rules:
    (1) Node attributes allow mixed tuple/list/dict of Node and NodeExpression.
    (2) Node has a fixed attribute 'node_wrappers' for NodeWrappers; no other attribute can hold NodeWrapper.
    (3) NodeWrapper/NodeExpression attributes can hold NodeExpression, but NOT Node or NodeWrapper.
    (4) Nested structures (list/dict/tuple) containing Node or NodeExpression must be pure 
        (only Node or only NodeExpression, no mixing, no other types).
    """
    if not isinstance(root, Node):
        raise TypeError(f"Root must be a Node, got {type(root)}")

    # Use a set to avoid infinite recursion on cyclic graphs
    visited = set()

    def _validate_recursive(obj: Any):
        if id(obj) in visited:
            return
        # Only validate our core types
        if not _is_node_element(obj):
            return
        
        visited.add(id(obj))
        
        is_node = isinstance(obj, Node)
        is_expr = isinstance(obj, NodeExpression)
        is_wrapper = isinstance(obj, NodeWrapper)

        # Iterate over attributes using the Engine's iter logic.
        # By setting is_leaf to (x is not obj), we force the engine to:
        # 1. Yield 'obj' (because obj is obj, so is_leaf=False) -> Calls handler (e.g. _flatten_node_element)
        # 2. Treat any child (attribute value) as a leaf (because child is not obj, so is_leaf=True)
        # This gives us exactly the direct attributes of 'obj' without recursing deeper yet.
        direct_attrs_with_path = NODE_PYTREE_ENGINE.iter_with_path(
            obj,
            is_leaf=lambda x, _: x is not obj
        )

        for path, attr_value in direct_attrs_with_path:
            # Resolve attribute name from path (AttributeKey)
            # For Node objects, the path should be (AttributeKey('name'), )
            if not path or not isinstance(path[0], AttributeKey):
                continue
            
            attr_name = path[0].key

            # Step 1: Iterate over the attribute value to inspect its content structure.
            # We use is_leaf=_is_node_element to stop traversal at nested Nodes/Exprs/Wrappers.
            # This allows us to inspect the "container structure" holding them.
            leaves_iter = NODE_PYTREE_ENGINE.iter(
                attr_value, 
                is_leaf=lambda x, _: _is_node_element(x)
            )

            # Categorize leaves (Single pass optimization)
            nodes = []
            exprs = []
            wrappers = []
            others = []

            for x in leaves_iter:
                if isinstance(x, Node):
                    nodes.append(x)
                elif isinstance(x, NodeExpression):
                    exprs.append(x)
                elif isinstance(x, NodeWrapper):
                    wrappers.append(x)
                else:
                    others.append(x)

            # Flags for presence
            has_node = len(nodes) > 0
            has_expr = len(exprs) > 0
            has_wrapper = len(wrappers) > 0
            has_other = len(others) > 0

            path_info = f"{type(obj).__name__}.{attr_name}"

            # --- Rule (2) Check: NodeWrapper location ---
            if has_wrapper:
                # Wrappers are ONLY allowed in Node.node_wrappers
                if is_node:
                    if attr_name != "node_wrappers":
                        raise NodeStructureError(
                            f"Rule(2): NodeWrapper found in invalid attribute '{path_info}'. "
                            "NodeWrappers are only allowed in 'node_wrappers'."
                        )
                else:
                    # NodeWrapper/NodeExpression cannot hold NodeWrapper (Rule 3 covers this too)
                    raise NodeStructureError(
                        f"Rule(2/3): NodeWrapper found in '{path_info}'. "
                        "NodeWrappers cannot be held by NodeExpression or NodeWrapper."
                    )

            # --- Rule (3) Check: Node/Wrapper forbidden in Expr/Wrapper ---
            if is_expr or is_wrapper:
                if has_node:
                    raise NodeStructureError(
                        f"Rule(3): Node found in '{path_info}'. "
                        f"{type(obj).__name__} cannot hold Node."
                    )
                # has_wrapper checked above in Rule 2 block

            # --- Rule (4) Check: Purity of nested structures ---
            # "If a nested structure contains Node OR NodeExpression..."
            if has_node or has_expr:
                # 1. Mixing check: Node and NodeExpression cannot mix
                if has_node and has_expr:
                    raise NodeStructureError(
                        f"Rule(4): Mixed Node and NodeExpression in '{path_info}'. "
                        "Containers must be pure."
                    )
                
                # 2. Purity check: Cannot mix with other types (int, str, etc.)
                # Note: If validation reached here, we have either pure Nodes or pure Exprs (regarding each other).
                # We verify they are not mixed with 'others'.
                if has_other:
                     raise NodeStructureError(
                        f"Rule(4): Impure container in '{path_info}'. "
                        "Found Node/NodeExpression mixed with other types."
                    )
            
            # --- Recursion ---
            # Validate all child elements found in this attribute
            for child in (nodes + exprs + wrappers):
                _validate_recursive(child)

    # Start validation
    _validate_recursive(root)
