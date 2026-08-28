# Copyright 2026 The SlymeLab Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Node validation module, including structure consistency checking.
"""

from typing import Any
from collections.abc import Callable
from slyme.utils.registry import TypeRegistry
from slyme.utils.pytree import AttributeKey, PyTreeKey
from slyme.utils.exception import enrich_exception
from .core import (
    BaseNode,
    BaseWrapper,
    NodeElement,
    Node,
    Wrapper,
    AsyncNode,
    AsyncWrapper,
)
from .tree import NODE_ENGINE

__all__ = [
    "NodeStructureError",
    "check_node_structure",
]


# --- Node Structure Consistency Check ---
class NodeStructureError(TypeError):
    """Raised when the Node structure violates consistency rules."""

    pass


# Type definition for validation functions
# Args: obj (container), key (PyTreeKey), leaves
ValidatorFunc = Callable[[NodeElement, PyTreeKey, list[Any]], None]
VALIDATION_REGISTRY: TypeRegistry[Any, ValidatorFunc] = TypeRegistry("node_validation")


@VALIDATION_REGISTRY.register(key=Node)
def _validate_node_structure(obj: Node, key: PyTreeKey, leaves: list[Any]) -> None:
    """Validator for Node instances."""
    type_name = obj.type_repr()
    path_info = key.codify(type_name)

    with enrich_exception(f"at {path_info}"):
        # Rule: Wrapper placement
        # Wrappers are ONLY allowed in the 'wrappers' attribute.
        is_wrappers_attr = isinstance(key, AttributeKey) and key.name == "wrappers"

        for leaf in leaves:
            if isinstance(leaf, AsyncWrapper):
                raise NodeStructureError(
                    f"Cannot use AsyncWrapper in synchronous Node {type_name}."
                )
            if isinstance(leaf, Wrapper) != is_wrappers_attr:
                raise NodeStructureError(
                    f"Invalid wrapper placement: Wrappers must be in {type_name}.wrappers."
                )


@VALIDATION_REGISTRY.register(key=Wrapper)
def _validate_wrapper_structure(
    obj: Wrapper,
    key: PyTreeKey,
    leaves: list[Any],
) -> None:
    """Validator for Wrapper."""
    type_name = obj.type_repr()
    path_info = key.codify(type_name)

    with enrich_exception(f"at {path_info}"):
        # Rule: Downward closure
        for leaf in leaves:
            if isinstance(leaf, BaseNode):
                raise NodeStructureError(
                    f"Invalid containment: {type_name} cannot hold Node."
                )
            if isinstance(leaf, BaseWrapper):
                raise NodeStructureError(
                    f"Invalid containment: {type_name} cannot hold Wrapper."
                )


@VALIDATION_REGISTRY.register(key=AsyncNode)
def _validate_async_node_structure(
    obj: AsyncNode, key: PyTreeKey, leaves: list[Any]
) -> None:
    """Validator for AsyncNode instances."""
    type_name = obj.type_repr()
    path_info = key.codify(type_name)

    with enrich_exception(f"at {path_info}"):
        # Rule: Wrapper placement
        is_wrappers_attr = isinstance(key, AttributeKey) and key.name == "wrappers"

        for leaf in leaves:
            if isinstance(leaf, Wrapper):
                raise NodeStructureError(
                    f"Cannot use synchronous Wrapper in AsyncNode {type_name}."
                )
            if isinstance(leaf, AsyncWrapper) != is_wrappers_attr:
                raise NodeStructureError(
                    f"Invalid wrapper placement: Wrappers must be in {type_name}.wrappers."
                )


@VALIDATION_REGISTRY.register(key=AsyncWrapper)
def _validate_async_wrapper_structure(
    obj: AsyncWrapper,
    key: PyTreeKey,
    leaves: list[Any],
) -> None:
    """Validator for AsyncWrapper."""
    type_name = obj.type_repr()
    path_info = key.codify(type_name)

    with enrich_exception(f"at {path_info}"):
        # Rule: Downward closure
        for leaf in leaves:
            if isinstance(leaf, BaseNode):
                raise NodeStructureError(
                    f"Invalid containment: {type_name} cannot hold Node."
                )
            if isinstance(leaf, BaseWrapper):
                raise NodeStructureError(
                    f"Invalid containment: {type_name} cannot hold Wrapper."
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
        direct_attrs_with_key_path = [
            (p, c)
            for p, c in NODE_ENGINE.iter_with_key_path(
                obj, is_leaf=lambda x, _: x is not obj
            )
            if p
        ]
        for key_path, attr_value in direct_attrs_with_key_path:
            # key_path is tuple[PyTreeKey, ...]
            # Since we iterate immediate children (is_leaf stops at obj's children),
            # key_path length is guaranteed to be 1.
            key = key_path[0]
            # Gather all children in this attribute structure.
            leaves = list(
                NODE_ENGINE.iter(
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
