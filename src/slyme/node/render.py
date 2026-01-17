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
    extra_repr = getattr(obj, "extra_repr", lambda: "")()
    return f"{type_name}({extra_repr})" if extra_repr else type_name


def _build_render_lines(obj: Any) -> _RenderResult:
    """
    Recursively build render lines for the object structure.
    Returns the lines representing the children of `obj`.
    """
    # 1. Determine Category
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
    classified_children = defaultdict(list)
    flat_children = []
    has_valid_children = False

    for path, child in children_with_path:
        child_result = _build_render_lines(child)

        # Only display children that have a valid category or content
        if child_result.category is not None:
            has_valid_children = True
            key_str = path[-1].codify("")  # e.g., ".name" or "[0]"
            item = (key_str, child, child_result)

            classified_children[child_result.category].append(item)
            flat_children.append(item)

    # 4. Infer Category for Containers (if not already set)
    # If the object itself isn't registered but contains renderable items (like a list of Nodes),
    # we infer its category from its children to decide how to group it.
    if my_category is None and has_valid_children:
        for cat, _ in _CATEGORY_CONFIG:
            if cat in classified_children:
                my_category = cat
                break
        if my_category is None:
            my_category = next(iter(classified_children.keys()), None)

    if my_category is None:
        return _RenderResult([], None)

    # 5. Dispatch Rendering Strategy
    lines = []
    if isinstance(obj, _GROUPED_RENDER_TYPES):
        lines = _render_grouped(classified_children)
    elif my_category == "wrappers":
        lines = _render_direct(flat_children, is_last_cat=False)
    else:
        lines = _render_direct(flat_children, is_last_cat=True)

    return _RenderResult(lines, my_category)


def _render_grouped(classified_children: dict[str, list]) -> list[str]:
    """
    Strategy: Group children by their category (nodes, wrappers, etc.).
    Returns a list of rendered lines.
    """
    lines = []
    # Filter active categories based on config order
    active_cats = [(c, t) for c, t in _CATEGORY_CONFIG if classified_children.get(c)]
    count = len(active_cats)

    for i, (cat_name, cat_title) in enumerate(active_cats):
        is_last_cat = i == count - 1
        items = classified_children[cat_name]

        connector = "│ "
        lines.append(f"{connector}{cat_title}")

        if cat_name == "wrappers":
            lines.extend(_render_wrapper_group(items))
        else:
            lines.extend(
                _render_children_lines(items, is_last_cat=is_last_cat)
            )

    return lines


def _render_direct(
    items: list[tuple[str, Any, _RenderResult]], is_last_cat: bool = True
) -> list[str]:
    """
    Strategy: Render children linearly.
    """
    return _render_children_lines(items, is_last_cat=is_last_cat)


def _render_children_lines(
    items: list[tuple[str, Any, _RenderResult]], is_last_cat: bool = True
) -> list[str]:
    """
    Standard rendering of a list of children items.
    Handles tree connectors (├──, └──) and indentation.
    """
    lines = []
    count = len(items)
    for i, (key_str, child, child_res) in enumerate(items):
        is_last = i == count - 1
        connector = "└── " if is_last and is_last_cat else "├── "

        child_header = _get_node_header(child)
        lines.append(f"{connector}{key_str} {child_header}")

        if child_res.lines:
            child_prefix = "    " if is_last and is_last_cat else "│   "
            for line in child_res.lines:
                lines.append(f"{child_prefix}{line}")
    return lines


def _render_wrapper_group(
    items: list[tuple[str, Any, _RenderResult]]
) -> list[str]:
    """
    Special handling for wrappers to avoid showing container indices.
    Simply appends the wrapper's content lines.
    """
    lines = []
    for _, _, child_res in items:
        lines.extend(child_res.lines)
    return lines
