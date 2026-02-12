"""
Node validation module, including dependency checking and structure consistency checking.
"""

from dataclasses import dataclass
from typing import Any, Union, Protocol
from collections.abc import Callable
from slyme.utils.registry import Registry, TypeRegistry
from slyme.utils.pytree import AttributeKey, PyTreeKey
from slyme.context import Context, Dep, DEP, Ref
from .core import (
    NodeElement,
    Node,
    Expression,
    Wrapper,
)
from .tree import NODE_PYTREE_ENGINE

__all__ = [
    "DEPENDENCY_REGISTRY",
    "vanilla_dependency_check",
    "VanillaDependencyReport",
    "NodeStructureError",
    "check_node_structure",
]

# Registry definition for dependency checkers
DEPENDENCY_REGISTRY: Registry["_NodeDependencyChecker"] = Registry("node_dependency")


class _NodeDependencyChecker(Protocol):
    """
    Protocol for dependency checkers.
    """

    def __call__(self, node: NodeElement, ctx: Context, /, **kwargs) -> Any:
        """
        Check the dependencies of the given node against the context.
        """
        ...


@dataclass
class VanillaDependencyReport:
    """Strategy-specific report."""

    missing_keys: set[str]
    context_keys: set[str]
    all_provided: set[str]
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
def vanilla_dependency_check(
    node: NodeElement,
    ctx: Context,
    /,
    **kwargs,
) -> VanillaDependencyReport:
    """
    A simple dependency checker that flattens the node to find required and provided keys.
    """
    # 1. Collect dependencies directly using NODE_PYTREE_ENGINE
    requires: set[str] = set()
    provides: set[str] = set()

    # Flatten the node structure.
    # NODE_PYTREE_ENGINE is configured to handle NodeElement traversal.
    leaves = tuple(NODE_PYTREE_ENGINE.iter(node))

    for leaf in leaves:
        if isinstance(leaf, Ref):
            dep_mode = leaf.metadata.get(DEP, Dep.NONE)
            path = leaf.path
            if dep_mode & Dep.REQUIRE:
                requires.add(path)
            if dep_mode & Dep.PROVIDE:
                provides.add(path)

    # 2. Extract existing keys from Context
    context_data = ctx.collect_leaves()
    context_keys = set(context_data.keys())

    # 3. Calculate missing keys (Set arithmetic)
    available_keys = provides | context_keys
    missing_keys = requires - available_keys

    return VanillaDependencyReport(
        missing_keys=missing_keys,
        context_keys=context_keys,
        all_provided=provides,
        all_required=requires,
    )


# --- Node Structure Consistency Check ---
class NodeStructureError(TypeError):
    """Raised when the Node structure violates consistency rules."""

    pass


# Type definition for validation functions
# Args: obj (container), key (PyTreeKey), leaves
ValidatorFunc = Callable[[NodeElement, PyTreeKey, list[Any]], None]
VALIDATION_REGISTRY: TypeRegistry[Any, ValidatorFunc] = TypeRegistry("node_validation")


# --- Leaf Scanning Logic ---

# Configuration: Types that must be independently tracked and kept pure.
# Any object belonging to these types (or their subclasses) is treated as a distinct category.
_TRACKED_CATEGORIES = (Node, Expression, Wrapper, Ref)
# Marker for any type not in the tracked categories.
_OTHERS_MARKER = None


def _scan_leaves(leaves: list[Any]) -> set[Union[type, None]]:
    """
    Scan the leaves and return a set of categories found.

    Returns:
        A set containing:
        - The specific tracked class (e.g., Node, Ref) if found.
        - _OTHERS_MARKER (None) if a non-tracked object is found.
    """
    stats: set[Union[type, None]] = set()

    for leaf in leaves:
        found_category = False
        for category in _TRACKED_CATEGORIES:
            if isinstance(leaf, category):
                stats.add(category)
                found_category = True
                break

        if not found_category:
            stats.add(_OTHERS_MARKER)

    return stats


def _validate_purity(stats: set[Union[type, None]], path_info: str) -> None:
    """
    Enforce purity: A container can only hold elements of ONE tracked category,
    OR purely untracked elements (Others).

    Logic:
        - {Node}: OK
        - {Node, Expression}: Error (Mixed tracked types)
        - {Node, None}: Error (Tracked type mixed with Others)
        - {None}: OK (Pure Others, internal mix of Others is allowed)
        - {}: OK (Empty)
    """
    if len(stats) > 1:
        # Construct readable error details
        names = []
        for cat in stats:
            if cat is _OTHERS_MARKER:
                names.append("Others")
            else:
                names.append(cat.__name__)
        raise NodeStructureError(
            f"Mixed content at '{path_info}': "
            f"Found mixed types {names}. Containers must be homogenous regarding "
            f"Nodes, Expressions, Wrappers, and Refs."
        )


@VALIDATION_REGISTRY.register(key=Node)
def _validate_node_structure(obj: Node, key: PyTreeKey, leaves: list[Any]) -> None:
    """Validator for Node instances."""
    stats = _scan_leaves(leaves)
    type_name = obj.type_repr()
    path_info = key.codify(type_name)
    _validate_purity(stats, path_info)

    # Rule: Wrapper placement
    # Wrappers are ONLY allowed in the 'wrappers' attribute.
    if Wrapper in stats:
        is_wrappers_attr = isinstance(key, AttributeKey) and key.name == "wrappers"
        if not is_wrappers_attr:
            raise NodeStructureError(
                f"Invalid wrapper placement at {path_info}: "
                f"Wrappers must be in {type_name}.wrappers."
            )


@VALIDATION_REGISTRY.register(key=Expression)
@VALIDATION_REGISTRY.register(key=Wrapper)
def _validate_terminal_structure(
    obj: Union[Expression, Wrapper],
    key: PyTreeKey,
    leaves: list[Any],
) -> None:
    """Validator for Expression and Wrapper (Terminal Structures)."""
    stats = _scan_leaves(leaves)
    type_name = obj.type_repr()
    path_info = key.codify(type_name)
    _validate_purity(stats, path_info)

    # Rule: Downward closure
    if Node in stats:
        raise NodeStructureError(
            f"Invalid containment at {path_info}: " f"{type_name} cannot hold Node."
        )
    if Wrapper in stats:
        raise NodeStructureError(
            f"Invalid containment at {path_info}: "
            f"{type_name} cannot hold Wrapper."
        )


def check_node_structure(root: Node) -> None:
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
            # path is tuple[PyTreeKey, ...]
            # Since we iterate immediate children (is_leaf stops at obj's children),
            # path length is guaranteed to be 1.
            key = path[0]
            # Gather all children in this attribute structure.
            leaves = list(
                NODE_PYTREE_ENGINE.iter(
                    attr_value, is_leaf=lambda x, _: isinstance(x, NodeElement)
                )
            )
            # --- Dispatch Check Logic ---
            if validate_func is not None:
                validate_func(obj, key, leaves)
            # --- Recursion ---
            # Recursively validate valid NodeElement children
            for child in leaves:
                if isinstance(child, NodeElement):
                    _validate_recursive(child)

    _validate_recursive(root)
