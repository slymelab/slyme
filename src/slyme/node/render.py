"""
Node rendering module.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional
from slyme.utils.registry import TypeRegistry
from slyme.utils.store import Field
from slyme.node.base import NodeElement, Node, NodeExpression
from slyme.node.wrapper import NodeWrapper
from slyme.node.pytree import NODE_PYTREE_ENGINE

__all__ = ["get_render_string"]

# Tree rendering constants
TREE_BRANCH = "├── "
TREE_LAST = "└── "
TREE_VERTICAL = "│   "
TREE_SPACER = "    "
GROUP_CONNECTOR = "│ => "

# Registry to determine the category of an object during rendering.
RENDER_TYPE_REGISTRY = TypeRegistry("render_category")
RENDER_TYPE_REGISTRY.register("nodes", key=Node)
RENDER_TYPE_REGISTRY.register("expressions", key=NodeExpression)
RENDER_TYPE_REGISTRY.register("wrappers", key=NodeWrapper)
RENDER_TYPE_REGISTRY.register("fields", key=Field)

# Types that should use the "Grouped" rendering strategy.
_GROUPED_RENDER_TYPES = (NodeElement,)

# Configuration for grouped rendering: (Category Name, Display Title)
_CATEGORY_CONFIG = [
    ("wrappers", "@wrappers"),
    ("fields", "#fields"),
    ("expressions", "$expressions"),
    ("nodes", "(nodes)"),
]


@dataclass
class _RenderResult:
    """Internal result holder for recursive rendering."""

    lines: list[str]
    category: Optional[str]


@dataclass
class _RenderItem:
    """Holds information about a child node to be rendered."""

    key: str
    child: Any
    result: _RenderResult


def get_render_string(obj: Any) -> str:
    """
    Generate a formatted tree string representation of a Node object.

    This function traverses the Node structure using ``NODE_PYTREE_ENGINE``
    and formats it based on registered categories and render strategies.
    """
    header = _get_node_header(obj)
    result = _build_render_lines(obj)
    return "\n".join([header] + result.lines)


def _get_node_header(obj: Any) -> str:
    """Resolve the display header for a single object."""
    type_name = type(obj).__name__
    extra_repr: str = getattr(obj, "extra_repr", lambda: "")()
    return f"{type_name}({extra_repr})" if extra_repr else type_name


def _build_render_lines(obj: Any) -> _RenderResult:
    """
    Recursively build render lines for the object structure.
    Returns the lines representing the children of `obj`.
    """
    # 1. Determine Category for grouped rendering.
    my_category = RENDER_TYPE_REGISTRY.lookup(type(obj), default=None)

    # 2. Collect Children via PyTree Engine
    # NOTE: We use `is_leaf` to inspect the immediate attributes without flattening nested Nodes.
    children_with_path = [
        (path, child)
        for path, child in NODE_PYTREE_ENGINE.iter_with_path(
            obj, is_leaf=lambda x, _: x is not obj
        )
        if path
    ]

    # 3. Process Children Recursively
    flat_children: list[_RenderItem] = []

    for path, child in children_with_path:
        child_result = _build_render_lines(child)

        # Only display children that have a valid category or content
        if child_result.category is not None:
            key_str = path[-1].codify("")  # e.g., ".name" or "[0]"
            item = _RenderItem(key=key_str, child=child, result=child_result)
            flat_children.append(item)

    # 4. Infer Category for Containers (if not already set)
    # Since the container is guaranteed to be pure (homogenous categories),
    # we can simply inherit the category from the first child.
    if my_category is None and flat_children:
        my_category = flat_children[0].result.category

    if my_category is None:
        return _RenderResult([], None)

    # 5. Dispatch Rendering Strategy
    lines = []
    if isinstance(obj, _GROUPED_RENDER_TYPES):
        lines = _render_grouped(flat_children)
    else:
        lines = _render_direct(flat_children)

    return _RenderResult(lines, my_category)


def _render_grouped(items: list[_RenderItem]) -> list[str]:
    """
    Strategy: Group children by their category (nodes, wrappers, etc.).
    Returns a list of rendered lines.
    """
    # Group children by category locally, avoiding redundancy in the main loop
    classified_children = defaultdict(list)
    for item in items:
        classified_children[item.result.category].append(item)

    lines = []
    # Filter active categories based on config order
    active_cats = [(c, t) for c, t in _CATEGORY_CONFIG if classified_children.get(c)]
    count = len(active_cats)

    for i, (cat_name, cat_title) in enumerate(active_cats):
        is_last_cat = i == count - 1
        cat_items = classified_children[cat_name]

        # Determine the connector/prefix style for the LAST item in this group.
        if is_last_cat:
            last_connector = TREE_LAST
            last_prefix = TREE_SPACER
        else:
            last_connector = TREE_BRANCH
            last_prefix = TREE_VERTICAL

        lines.append(f"{GROUP_CONNECTOR}{cat_title}")
        lines.extend(_render_children_lines(cat_items, last_connector, last_prefix))

    return lines


def _render_direct(items: list[_RenderItem]) -> list[str]:
    """
    Strategy: Render children linearly.
    """
    # Direct rendering implies no subsequent groups, so the last item always terminates.
    return _render_children_lines(items, TREE_LAST, TREE_SPACER)


def _render_children_lines(
    items: list[_RenderItem], last_connector: str, last_prefix: str
) -> list[str]:
    """
    Standard rendering of a list of children items.
    Handles tree connectors and indentation using passed-in styles for the last item.

    Args:
        items: List of (key, child, result) tuples.
        last_connector: The connector string to use for the *very last* item in this list.
        last_prefix: The indentation string to use for the children of the *very last* item.
    """
    lines = []
    count = len(items)
    for i, item in enumerate(items):
        is_last = i == count - 1

        # Select connector and prefix based on position
        if is_last:
            connector = last_connector
            prefix = last_prefix
        else:
            connector = TREE_BRANCH
            prefix = TREE_VERTICAL

        child_header = _get_node_header(item.child)
        lines.append(f"{connector}{item.key} {child_header}")
        child_res = item.result
        if child_res.lines:
            for line in child_res.lines:
                lines.append(f"{prefix}{line}")
    return lines
