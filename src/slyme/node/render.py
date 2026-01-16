"""
slyme node render utility.
"""

from typing import Any, Tuple, List, Dict
from collections.abc import Sequence, Mapping
from slyme.node.base import NodeElement, Node, NodeExpression
from slyme.node.wrapper import NodeWrapper, NodeWrapperList
from slyme.node.pytree import NODE_PYTREE_ENGINE, AttributeKey

__all__ = ["get_render_string"]


def get_render_string(obj: Any) -> str:
    """
    Return the string representation of the node hierarchy.
    """
    lines = _build_lines(obj, prefix="", is_root=True)
    return "\n".join(lines)


def _get_node_header(obj: Any) -> str:
    """
    Generate the header string for a node, e.g., 'MyNode(name="foo")'.
    Uses 'extra_repr' if available.
    """
    type_name = type(obj).__name__
    extra = ""
    # 优先使用 extra_repr
    if hasattr(obj, "extra_repr"):
        extra = obj.extra_repr()
    # 为容器提供简单的视觉提示
    elif isinstance(obj, (list, tuple, NodeWrapperList)):
        return "[]" if isinstance(obj, (list, NodeWrapperList)) else "()"
    elif isinstance(obj, Mapping):
        return "{}"
    
    return f"{type_name}({extra})" if extra else type_name


def _classify_children(
    children_with_path: List[Tuple[Any, Any]]
) -> Dict[str, List[Tuple[Any, Any]]]:
    """
    Classify children into 'wrappers', 'nodes', and 'expressions'.
    Returns a dict of {category_name: list_of_(key, child)}.
    """
    categories = {
        "wrappers": [],
        "nodes": [],
        "expressions": []
    }

    for path, child in children_with_path:
        key = path[0] 
        
        # 1. Wrappers (Special handling for 'node_wrappers' attribute)
        if (
            isinstance(key, AttributeKey) 
            and key.key == "node_wrappers" 
            and isinstance(child, NodeWrapperList)
        ):
            if len(child) > 0:
                categories["wrappers"].append((key, child))
            continue

        # 2. Check content type for Nodes and Expressions
        content_type = _get_content_type(child)
        
        if content_type == "node":
            categories["nodes"].append((key, child))
        elif content_type == "expression":
            categories["expressions"].append((key, child))
        # else: ignore ordinary attributes

    return categories


def _get_content_type(obj: Any) -> str:
    """
    Determine if the object is or holds Nodes or Expressions.
    Returns 'node', 'expression', or 'other'.
    """
    if isinstance(obj, Node):
        return "node"
    if isinstance(obj, NodeExpression):
        return "expression"
    
    # Recursive check for containers
    if isinstance(obj, (list, tuple, NodeWrapperList)):
        for item in obj:
            t = _get_content_type(item)
            if t in ("node", "expression"):
                return t
        return "other"
    
    if isinstance(obj, Mapping):
        for item in obj.values():
            t = _get_content_type(item)
            if t in ("node", "expression"):
                return t
        return "other"
        
    return "other"


def _build_lines(obj: Any, prefix: str, is_root: bool = False) -> List[str]:
    lines = []
    
    if is_root:
        lines.append(_get_node_header(obj))

    try:
        children_with_path, _ = NODE_PYTREE_ENGINE.flatten_with_path(
            obj, 
            is_leaf=lambda x, _: x is not obj
        )
    except Exception:
        return lines

    # Case A: It is a Node -> Apply Categorization
    if isinstance(obj, Node):
        classified = _classify_children(children_with_path)
        
        # Order of categories to display
        category_order = ["wrappers", "nodes", "expressions"]
        
        # Display styles map
        category_styles = {
            "wrappers": "<wrappers>",       # AOP/Stack style
            "nodes": "(nodes)",             # Structural style
            "expressions": "(expressions)"  # Functional style
        }
        
        active_categories = [c for c in category_order if classified[c]]
        count_cats = len(active_categories)

        for i, cat_name in enumerate(active_categories):
            is_last_cat = (i == count_cats - 1)
            cat_items = classified[cat_name]
            
            # Render Category Header
            connector = "└── " if is_last_cat else "├── "
            header_str = category_styles.get(cat_name, f"({cat_name})")
            lines.append(f"{prefix}{connector}{header_str}")
            
            cat_prefix = prefix + ("    " if is_last_cat else "│   ")
            
            # --- Special Handling for Wrappers ---
            if cat_name == "wrappers":
                # Iterate over the NodeWrapperLists found (usually just one: .node_wrappers)
                for _, wrapper_list in cat_items:
                    # Manually flatten the list to get indices
                    wrappers_iter, _ = NODE_PYTREE_ENGINE.flatten_with_path(
                        wrapper_list, is_leaf=lambda x, _: x is not wrapper_list
                    )
                    wrappers = list(wrappers_iter)
                    w_count = len(wrappers)
                    
                    for j, (w_key, w_child) in enumerate(wrappers):
                        is_last_w = (j == w_count - 1)
                        w_connector = "└── " if is_last_w else "├── "
                        # [NEW] Restore Index: e.g. "[0]"
                        w_key_str = w_key[-1].codify("") 
                        w_header = _get_node_header(w_child)
                        
                        lines.append(f"{cat_prefix}{w_connector}{w_key_str} {w_header}")
            
            # --- Standard Handling for Nodes/Exprs ---
            else:
                item_count = len(cat_items)
                for k, (k_key, k_child) in enumerate(cat_items):
                    is_last_item = (k == item_count - 1)
                    item_connector = "└── " if is_last_item else "├── "
                    
                    key_str = k_key.codify("")
                    child_header = _get_node_header(k_child)
                    
                    lines.append(f"{cat_prefix}{item_connector}{key_str} {child_header}")
                    
                    # Recurse if needed
                    if isinstance(k_child, (Node, list, tuple, dict, NodeWrapperList)):
                        child_prefix = cat_prefix + ("    " if is_last_item else "│   ")
                        lines.extend(_build_lines(k_child, child_prefix, is_root=False))

    # Case B: Container inside a Node
    elif isinstance(obj, (list, tuple, dict, NodeWrapperList)):
        filtered_children = []
        for path, child in children_with_path:
            if not path: continue
            # Keep items if they are Nodes/Exprs OR Wrappers (in case of nested wrapper lists)
            if _get_content_type(child) != "other" or isinstance(child, (NodeWrapper, NodeWrapperList)):
                 filtered_children.append((path[0], child))
        
        count = len(filtered_children)
        for i, (key, child) in enumerate(filtered_children):
            is_last = (i == count - 1)
            connector = "└── " if is_last else "├── "
            
            key_str = key.codify("")
            child_header = _get_node_header(child)
            
            lines.append(f"{prefix}{connector}{key_str} {child_header}")
            
            if isinstance(child, (Node, list, tuple, dict, NodeWrapperList)):
                child_prefix = prefix + ("    " if is_last else "│   ")
                lines.extend(_build_lines(child, child_prefix, is_root=False))

    return lines
