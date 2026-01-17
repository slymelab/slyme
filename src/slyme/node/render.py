from typing import Any, Optional
from collections import defaultdict
from dataclasses import dataclass
from slyme.utils.registry import TypeRegistry
from slyme.node.base import NodeElement, Node, NodeExpression
from slyme.node.wrapper import NodeWrapper
from slyme.node.pytree import NODE_PYTREE_ENGINE

__all__ = ["get_render_string"]

# =============================================================================
# Configuration & Registry
# =============================================================================

# 1. Category Registry (Determines "What is this object?")
RENDER_TYPE_REGISTRY = TypeRegistry("render_category")
RENDER_TYPE_REGISTRY.register("nodes", key=Node)
RENDER_TYPE_REGISTRY.register("expressions", key=NodeExpression)
RENDER_TYPE_REGISTRY.register("wrappers", key=NodeWrapper)

# 2. Render Strategy (Determines "How do we display its children?")
#    True  = Grouped Rendering (Categorized by type, e.g. Node)
#    False = Direct Rendering (Attribute/Key based, e.g. Expression)
#    Default is False (Direct) for maximum flexibility.
GROUPED_RENDER_TYPES = (Node,) 

# 3. Category Config (Defines display order and titles for Grouped Rendering)
#    (Category Name, Display Title)
TYPE_CONFIG = [
    ("wrappers", "<wrappers>"),
    ("nodes", "(nodes)"),
    ("expressions", "(expressions)")
]

@dataclass
class RenderResult:
    lines: list[str]
    category: Optional[str]


# =============================================================================
# Main Logic
# =============================================================================

def get_render_string(obj: Any) -> str:
    header = _get_node_header(obj)
    res = _build_lines(obj)
    final_lines = [header] + res.lines
    return "\n".join(final_lines)

def _get_node_header(obj: Any) -> str:
    type_name = type(obj).__name__
    extra = obj.extra_repr() if hasattr(obj, "extra_repr") else ""
    return f"{type_name}({extra})" if extra else type_name

def _build_lines(obj: Any) -> RenderResult:
    # 1. Determine Category
    my_category = RENDER_TYPE_REGISTRY.lookup(type(obj), default=None)

    # 2. Get Children
    try:
        children_with_path, _ = NODE_PYTREE_ENGINE.flatten_with_path(
            obj, 
            is_leaf=lambda x, _: x is not obj
        )
    except Exception:
        return RenderResult([], my_category)

    # 3. Process Children Recursively
    #    We collect BOTH classified dict (for Grouped) and flat list (for Direct)
    #    This incurs no extra cost as we iterate once.
    classified = defaultdict(list)
    processed_children = []
    has_valid_children = False

    for path, child in children_with_path:
        child_res = _build_lines(child)
        
        if child_res.category is not None:
            has_valid_children = True
            key_str = path[-1].codify("")
            item = (key_str, child, child_res)
            
            classified[child_res.category].append(item)
            processed_children.append(item)

    # 4. Propagate Category (for Containers like list/dict)
    if my_category is None and has_valid_children:
        # Containers default to the category of their first content
        # or fallback to a general category from config
        for cat, _ in TYPE_CONFIG:
            if cat in classified:
                my_category = cat
                break
        if my_category is None:
            my_category = list(classified.keys())[0]

    if my_category is None:
        return RenderResult([], None)

    # 5. Dispatch Rendering Strategy
    #    If it is a Node (or configured as Grouped), use Grouped Rendering.
    #    Otherwise (Expression, Wrapper, List, Dict...), use Direct Rendering.
    lines = []
    if isinstance(obj, GROUPED_RENDER_TYPES):
        _render_grouped(lines, classified)
    else:
        _render_direct(lines, processed_children)

    return RenderResult(lines, my_category)


# =============================================================================
# Render Strategies
# =============================================================================

def _render_grouped(
    target_lines: list[str], 
    classified: dict[str, list]
):
    """
    Strategy A: Group children by their category (nodes, wrappers, etc.).
    Used for 'Bag-like' objects (e.g., Node).
    """
    active_configs = [(c, title) for c, title in TYPE_CONFIG if classified[c]]
    count_cats = len(active_configs)

    for i, (cat_name, cat_title) in enumerate(active_configs):
        is_last_cat = (i == count_cats - 1)
        cat_items = classified[cat_name]

        connector = "└── " if is_last_cat else "├── "
        target_lines.append(f"{connector}{cat_title}")
        
        cat_prefix = "    " if is_last_cat else "│   "

        if cat_name == "wrappers":
            _append_wrapper_group(target_lines, cat_items, cat_prefix)
        else:
            _append_children_lines(target_lines, cat_items, cat_prefix)


def _render_direct(
    target_lines: list[str], 
    items: list[tuple[str, Any, RenderResult]]
):
    """
    Strategy B: Render children linearly preserving attribute keys.
    Used for 'Structure-like' objects (e.g., NodeExpression, Wrapper) and Containers.
    """
    _append_children_lines(target_lines, items, prefix="")


# =============================================================================
# Helpers
# =============================================================================

def _append_children_lines(
    target_lines: list[str],
    items: list[tuple[str, Any, RenderResult]],
    prefix: str
):
    count = len(items)
    for i, (key_str, child, child_res) in enumerate(items):
        is_last = (i == count - 1)
        connector = "└── " if is_last else "├── "
        
        child_header = _get_node_header(child)
        target_lines.append(f"{prefix}{connector}{key_str} {child_header}")

        if child_res.lines:
            child_prefix = prefix + ("    " if is_last else "│   ")
            for line in child_res.lines:
                target_lines.append(f"{child_prefix}{line}")


def _append_wrapper_group(
    target_lines: list[str],
    items: list[tuple[str, Any, RenderResult]],
    prefix: str
):
    """Flatten wrappers group to avoid showing container list indices."""
    all_wrapper_lines = []
    for _, _, child_res in items:
        all_wrapper_lines.extend(child_res.lines)
        
    for line in all_wrapper_lines:
        target_lines.append(f"{prefix}{line}")
