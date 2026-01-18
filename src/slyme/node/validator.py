"""
Node validation module, including dependency checking and structure consistency checking.
"""

from dataclasses import dataclass, field
from typing import Any
from typing_extensions import Self
from slyme.context import Context
from slyme.utils.registry import Registry
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
class VanillaDependencyInfo:
    """Strategy-specific data structure: Bag of Keys."""

    requires: set[str] = field(default_factory=set)
    produces: set[str] = field(default_factory=set)

    def update(self, other: "VanillaDependencyInfo") -> Self:
        self.requires.update(other.requires)
        self.produces.update(other.produces)
        return self


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
        # 1. Collect dependencies (VanillaDependencyInfo) directly using NODE_PYTREE_ENGINE
        info = VanillaDependencyInfo()

        # Flatten the node structure.
        # NODE_PYTREE_ENGINE is configured to handle NodeElement traversal.
        leaves, _ = NODE_PYTREE_ENGINE.flatten(node)

        for leaf in leaves:
            # NOTE: logic follows previous implementation assuming RequiresKey/ProducesKey exist.
            if isinstance(leaf, RequiresKey):  # type: ignore
                info.requires.add(leaf.path)
            elif isinstance(leaf, ProducesKey):  # type: ignore
                info.produces.add(leaf.path)

        # 2. Extract existing keys from Context
        context_data = ctx.collect_leaves()
        context_keys = set(context_data.keys())

        # 3. Calculate missing keys (Set arithmetic)
        available_keys = info.produces | context_keys
        missing_keys = info.requires - available_keys

        return VanillaDependencyReport(
            missing_keys=missing_keys,
            context_keys=context_keys,
            all_produced=info.produces,
            all_required=info.requires,
        )


# Node Structure Consistency Check
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
    if not isinstance(root, NodeElement):
        raise TypeError(f"Root must be a NodeElement, got {type(root)}")

    def _validate_recursive(obj: Any) -> None:
        # Only validate known structural units.
        if not isinstance(obj, NodeElement):
            return

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
                    attr_value, is_leaf=lambda x, _: isinstance(x, NodeElement)
                )
            )

            nodes = [x for x in leaves if isinstance(x, Node)]
            exprs = [x for x in leaves if isinstance(x, NodeExpression)]
            wrappers = [x for x in leaves if isinstance(x, NodeWrapper)]
            others = [x for x in leaves if not isinstance(x, NodeElement)]

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

            for child in nodes + exprs + wrappers:
                _validate_recursive(child)

    _validate_recursive(root)
