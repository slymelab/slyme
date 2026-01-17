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
        _render_grouped(lines, classified_children)
    else:
        _render_direct(lines, flat_children)

    return _RenderResult(lines, my_category)


def _render_grouped(
    target_lines: list[str], classified_children: dict[str, list]
) -> None:
    """
    Strategy: Group children by their category (nodes, wrappers, etc.).
    """
    # Filter active categories based on config order
    active_cats = [(c, t) for c, t in _CATEGORY_CONFIG if classified_children.get(c)]
    count = len(active_cats)

    for i, (cat_name, cat_title) in enumerate(active_cats):
        is_last_cat = i == count - 1
        items = classified_children[cat_name]

        connector = "└── " if is_last_cat else "├── "
        target_lines.append(f"{connector}{cat_title}")

        prefix = "    " if is_last_cat else "│   "

        if cat_name == "wrappers":
            _append_wrapper_group(target_lines, items, prefix)
        else:
            _append_children_lines(target_lines, items, prefix)


def _render_direct(
    target_lines: list[str], items: list[tuple[str, Any, _RenderResult]]
) -> None:
    """
    Strategy: Render children linearly.
    """
    _append_children_lines(target_lines, items, prefix="")


def _append_children_lines(
    target_lines: list[str], items: list[tuple[str, Any, _RenderResult]], prefix: str
) -> None:
    count = len(items)
    for i, (key_str, child, child_res) in enumerate(items):
        is_last = i == count - 1
        connector = "└── " if is_last else "├── "

        child_header = _get_node_header(child)
        target_lines.append(f"{prefix}{connector}{key_str} {child_header}")

        if child_res.lines:
            child_prefix = prefix + ("    " if is_last else "│   ")
            for line in child_res.lines:
                target_lines.append(f"{child_prefix}{line}")


def _append_wrapper_group(
    target_lines: list[str], items: list[tuple[str, Any, _RenderResult]], prefix: str
) -> None:
    """Special handling for wrappers to avoid showing container indices."""
    for _, _, child_res in items:
        for line in child_res.lines:
            target_lines.append(f"{prefix}{line}")
