"""
Node validation module, including dependency checking and structure consistency checking.
"""

from dataclasses import dataclass
from typing import Any, Union
from collections.abc import Callable
from slyme.context import Context
from slyme.utils.registry import Registry, TypeRegistry
from slyme.utils.pytree import AttributeKey
from slyme.node.base import (
    NodeElement,
    Node,
    NodeExpression,
    NODE_PYTREE_ENGINE,
)
from slyme.node.wrapper import NodeWrapper

# [Assuming RequiresKey and ProducesKey are importable from slyme.utils.store or similar]
# from slyme.utils.store import RequiresKey, ProducesKey

__all__ = [
    "DEPENDENCY_REGISTRY",
    "NodeDependencyChecker",
    "VanillaDependencyChecker",
    "VanillaDependencyReport",
    "NodeStructureError",
    "check_node_consistency",
]

# Registry definition for dependency checkers
DEPENDENCY_REGISTRY: Registry[type["NodeDependencyChecker"]] = Registry(
    "node_dependency"
)


class NodeDependencyChecker:
    """
    Base class for dependency checkers.
    """

    def check(self, node: NodeElement, ctx: Context, /, **kwargs) -> Any:
        """
        Check the dependencies of the given node against the context.
        """
        raise NotImplementedError


@dataclass
class VanillaDependencyReport:
    """Strategy-specific report."""

    missing_keys: set[str]
    context_keys: set[str]
    all_produced: set[str]
    all_required: set[str]

    @property
    def valid(self) -> bool:
        return len(self.missing_keys) == 0

    @property
    def message(self) -> str:
        if self.valid:
            return "Dependency Check Passed (Vanilla)."
        return (
            f"Dependency Check Failed (Vanilla).\n"
            f"Missing Keys: {sorted(list(self.missing_keys))}"
        )


@DEPENDENCY_REGISTRY.register(key="vanilla")
class VanillaDependencyChecker(NodeDependencyChecker):
    """
    A simple dependency checker that flattens the node to find required and produced keys.
    """

    def check(
        self,
        node: NodeElement,
        ctx: Context,
        /,
        **kwargs,
    ) -> VanillaDependencyReport:
        # 1. Collect dependencies directly using NODE_PYTREE_ENGINE
        requires: set[str] = set()
        produces: set[str] = set()

        # Flatten the node structure.
        # NODE_PYTREE_ENGINE is configured to handle NodeElement traversal.
        leaves, _ = NODE_PYTREE_ENGINE.flatten(node)

        for leaf in leaves:
            # NOTE: logic follows previous implementation assuming RequiresKey/ProducesKey exist.
            if isinstance(leaf, RequiresKey):  # type: ignore
                requires.add(leaf.path)
            elif isinstance(leaf, ProducesKey):  # type: ignore
                produces.add(leaf.path)

        # 2. Extract existing keys from Context
        context_data = ctx.collect_leaves()
        context_keys = set(context_data.keys())

        # 3. Calculate missing keys (Set arithmetic)
        available_keys = produces | context_keys
        missing_keys = requires - available_keys

        return VanillaDependencyReport(
            missing_keys=missing_keys,
            context_keys=context_keys,
            all_produced=produces,
            all_required=requires,
        )


# --- Node Structure Consistency Check ---
class NodeStructureError(TypeError):
    """Raised when the Node structure violates consistency rules."""
    pass


# Type definition for validation functions
# Args: obj (container), attr_name (field name), leaves (content of field), path_info (for error msg)
ValidatorFunc = Callable[[NodeElement, str, list[Any], str], None]
VALIDATION_REGISTRY: TypeRegistry[Any, ValidatorFunc] = TypeRegistry("node_validation")


@dataclass(frozen=True)
class _LeafStats:
    """Helper struct to hold scan results from a single pass."""
    has_node: bool = False
    has_expr: bool = False
    has_wrapper: bool = False
    has_other: bool = False


def _scan_leaves(leaves: list[Any]) -> _LeafStats:
    """
    Perform a single pass over leaves to determine content composition.
    This optimizes performance by avoiding multiple list comprehensions.
    """
    has_node = False
    has_expr = False
    has_wrapper = False
    has_other = False

    for leaf in leaves:
        if isinstance(leaf, Node):
            has_node = True
        elif isinstance(leaf, NodeExpression):
            has_expr = True
        elif isinstance(leaf, NodeWrapper):
            has_wrapper = True
        elif not isinstance(leaf, NodeElement):
            # If it's not a NodeElement at all, it's "other"
            has_other = True
        # Note: If we add more NodeElement subclasses in the future,
        # they fall through here unless checked. 
        # But generally they should inherit from one of the above or be treated as generic elements.

    return _LeafStats(has_node, has_expr, has_wrapper, has_other)


def _validate_purity(stats: _LeafStats, path_info: str) -> None:
    """Common purity check logic used by all validators."""
    # Rule: Container Purity (Cannot mix Node and Expression)
    if stats.has_node and stats.has_expr:
        raise NodeStructureError(
            f"Mixed content at '{path_info}': "
            "Cannot mix Node and NodeExpression."
        )
    
    # Rule: Impure container (NodeElement mixed with other types)
    # Only enforce if there are actual NodeElements present
    if (stats.has_node or stats.has_expr) and stats.has_other:
        raise NodeStructureError(
            f"Impure container at '{path_info}': "
            "Found Node/NodeExpression mixed with other types."
        )


@VALIDATION_REGISTRY.register(Node, key=Node)
def _validate_node_structure(
    obj: Node, attr_name: str, leaves: list[Any], path_info: str
) -> None:
    """Validator for Node instances."""
    stats = _scan_leaves(leaves)
    _validate_purity(stats, path_info)

    # Rule: NodeWrapper placement
    if stats.has_wrapper:
        if attr_name != "node_wrappers":
            raise NodeStructureError(
                f"Invalid wrapper placement at '{path_info}': "
                "NodeWrappers must be in 'node_wrappers'."
            )


@VALIDATION_REGISTRY.register(NodeExpression, key=NodeExpression)
@VALIDATION_REGISTRY.register(NodeWrapper, key=NodeWrapper)
def _validate_terminal_structure(
    obj: Union[NodeExpression, NodeWrapper], attr_name: str, leaves: list[Any], path_info: str
) -> None:
    """Validator for NodeExpression and NodeWrapper (Terminal Structures)."""
    stats = _scan_leaves(leaves)
    _validate_purity(stats, path_info)

    # Rule: Downward closure
    if stats.has_node:
        raise NodeStructureError(
            f"Invalid containment at '{path_info}': "
            f"{type(obj).__name__} cannot hold Node."
        )
    
    if stats.has_wrapper:
        raise NodeStructureError(
            f"Invalid containment at '{path_info}': "
            f"{type(obj).__name__} cannot hold NodeWrapper."
        )


@VALIDATION_REGISTRY.register(NodeElement, key=NodeElement)
def _validate_common_element(
    obj: NodeElement, attr_name: str, leaves: list[Any], path_info: str
) -> None:
    """Fallback validator for generic NodeElement subclasses."""
    stats = _scan_leaves(leaves)
    _validate_purity(stats, path_info)

    # Generic elements usually shouldn't hold wrappers (Wrappers attach to Nodes)
    if stats.has_wrapper:
        raise NodeStructureError(
            f"Invalid containment at '{path_info}': "
            f"{type(obj).__name__} cannot hold NodeWrapper."
        )


def check_node_consistency(root: Node) -> None:
    """
    Validates the structural consistency of a Node tree.
    Dispatches validation logic to registered functions based on node type.
    """
    if not isinstance(root, NodeElement):
        raise TypeError(f"Root must be a NodeElement, got {type(root)}")

    def _validate_recursive(obj: Any) -> None:
        # Only validate known structural units.
        if not isinstance(obj, NodeElement):
            return

        # Lookup the validator function for this object type
        # Uses inheritance lookup (e.g. subclass of Node uses _validate_node_structure)
        validate_func = VALIDATION_REGISTRY.lookup(type(obj), default=None)

        # Inspect immediate attributes using the engine.
        direct_attrs_with_path = [
            (p, c)
            for p, c in NODE_PYTREE_ENGINE.iter_with_path(
                obj, is_leaf=lambda x, _: x is not obj
            )
            if p
        ]

        for path, attr_value in direct_attrs_with_path:
            if not isinstance(path[0], AttributeKey):
                continue

            attr_name = path[0].name
            path_info = f"{type(obj).__name__}.{attr_name}"

            # Gather all children in this attribute structure.
            leaves = list(
                NODE_PYTREE_ENGINE.iter(
                    attr_value, is_leaf=lambda x, _: isinstance(x, NodeElement)
                )
            )

            # --- Dispatch Check Logic ---
            if validate_func is not None:
                validate_func(obj, attr_name, leaves, path_info)

            # --- Recursion ---
            # Recursively validate valid NodeElement children
            for child in leaves:
                if isinstance(child, NodeElement):
                    _validate_recursive(child)

    _validate_recursive(root)
