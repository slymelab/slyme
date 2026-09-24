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
Tree structure utilities for slyme.

Traversal algorithms consume explicit, immutable rules.
"""

import types
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field, replace
from itertools import count
from typing import (
    Any,
    Protocol,
)

_EMPTY_MAPPING: Mapping[str, Any] = types.MappingProxyType({})


@dataclass(frozen=True)
class TreeKey:
    """Base class for path elements."""

    def resolve(self, element: Any) -> Any:
        """
        Resolve the key against the given element to retrieve the child.
        Acts as the 'Getter' logic in KeyPath resolution.
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement resolve.")

    def codify(self, parent_expr: str) -> str:
        """
        Generate the Python code string to access this key from the parent expression.

        Args:
            parent_expr: The code expression of the parent container (e.g., 'tree', 'tree[0]').

        Returns:
            The new code expression (e.g., 'tree[0].value').
        """
        raise NotImplementedError(f"{type(self).__name__} does not implement codify.")


@dataclass(frozen=True)
class SequenceKey(TreeKey):
    """Represents an index in a sequence (list, tuple)."""

    index: int

    def resolve(self, element: Any) -> Any:
        return element[self.index]

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{self.index}]"


@dataclass(frozen=True)
class MappingKey(TreeKey):
    """Represents a key in a mapping (dict)."""

    key: Hashable

    def resolve(self, element: Any) -> Any:
        return element[self.key]

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{repr(self.key)}]"


@dataclass(frozen=True)
class AttributeKey(TreeKey):
    """Represents an attribute name (object)."""

    name: str

    def resolve(self, element: Any) -> Any:
        return getattr(element, self.name)

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.{self.name}"


# Type Alias for Path
KeyPath = tuple[TreeKey, ...]


@dataclass(frozen=True)
class TreeAux:
    """
    Auxiliary data required to reconstruct a container and track paths.

    Attributes:
        metadata: Custom data needed for unflattening (e.g., specific flags).
        children_keys: Optional tuple of keys corresponding to the children.
    """

    metadata: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    children_keys: tuple[TreeKey, ...] | None = None
    cls: type | None = None

    def __post_init__(self):
        if not isinstance(self.metadata, types.MappingProxyType):
            object.__setattr__(self, "metadata", types.MappingProxyType(self.metadata))


class FlattenFunc(Protocol):
    """
    Protocol for flattening a container.

    Returns:
        A tuple of (children_iterable, aux_data).
        - children_iterable: MUST yield pure child values.
        - aux_data: Contains metadata and optional keys for path tracking.
    """

    def __call__(self, element: Any, /) -> tuple[Iterable[Any], TreeAux]: ...


class UnflattenFunc(Protocol):
    """
    Protocol for unflattening a container.
    """

    def __call__(self, children: Iterable[Any], tree_aux: TreeAux, /) -> Any: ...


@dataclass(frozen=True)
class TreeHandler:
    """Container expansion and optional reconstruction functions."""

    flatten: FlattenFunc
    unflatten: UnflattenFunc | None


@dataclass(frozen=True)
class TraverseAux:
    """
    Auxiliary data during traversal.
    """

    parent: Any
    key_path: KeyPath


class IsLeafFunc(Protocol):
    """
    Protocol for functions that determine if an element is a leaf.
    """

    def __call__(self, element: Any, traverse_aux: TraverseAux, /) -> bool: ...


class TreeResolver(Protocol):
    """
    Protocol for dynamic handler resolution.
    Accepts an auxiliary object.
    """

    def __call__(
        self, element: Any, traverse_aux: TraverseAux, /
    ) -> TreeHandler | None: ...


@dataclass(frozen=True)
class TreeRules:
    """A shallow immutable snapshot of exact-type handlers and ordered resolvers."""

    handlers: Mapping[type, TreeHandler] = field(default_factory=dict)
    pre_resolvers: tuple[TreeResolver, ...] = ()
    post_resolvers: tuple[TreeResolver, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "handlers", types.MappingProxyType(dict(self.handlers))
        )

    @staticmethod
    def merge(values: tuple["TreeRules", ...]) -> "TreeRules":
        """Keep the first handler for each type and concatenate each resolver phase."""
        handlers: dict[type, TreeHandler] = {}
        for rules in values:
            for cls, handler in rules.handlers.items():
                handlers.setdefault(cls, handler)
        return TreeRules(
            handlers,
            tuple(resolver for rules in values for resolver in rules.pre_resolvers),
            tuple(resolver for rules in values for resolver in rules.post_resolvers),
        )


class _LeafSinkFunc(Protocol):
    """Internal protocol for collecting leaves."""

    def __call__(self, leaf: Any, traverse_aux: TraverseAux, /) -> None: ...


@dataclass(frozen=True)
class TreeDef:
    """Base class for tree definitions."""

    def unflatten(self, leaves: Iterable[Any]) -> Any:
        """
        Public API: Reconstruct the object from this structure and leaves.
        """
        leaves_iter = iter(leaves)
        element = self._build(leaves_iter)

        try:
            next(leaves_iter)
            raise ValueError("Too many leaves provided for this tree structure.")
        except StopIteration:
            pass

        return element

    def _build(self, leaves_iter: Iterator[Any]) -> Any:
        """Internal recursive driver."""
        raise NotImplementedError


@dataclass(frozen=True)
class LeafDef(TreeDef):
    def _build(self, leaves_iter: Iterator[Any]) -> Any:
        try:
            return next(leaves_iter)
        except StopIteration:
            raise ValueError(
                "Too few leaves provided for this tree structure."
            ) from None


@dataclass(frozen=True)
class ContainerDef(TreeDef):
    cls: type
    tree_aux: TreeAux
    children_defs: tuple[TreeDef, ...]
    unflatten_func: UnflattenFunc | None = field(compare=False, repr=False)

    def _build(self, leaves_iter: Iterator[Any]) -> Any:
        if self.unflatten_func is None:
            raise TypeError(
                f"{self.cls.__name__} is registered for traversal only; "
                "unflatten_func is None."
            )
        children = tuple(child._build(leaves_iter) for child in self.children_defs)
        return self.unflatten_func(children, self.tree_aux)


class TreeEngine:
    """Stateless traversal and reconstruction using caller-supplied rules.

    Type handlers match exactly. Unregistered objects remain leaves unless
    an explicitly installed resolver supplies a handler.
    """

    @staticmethod
    def _lookup_handler(
        element: Any, traverse_aux: TraverseAux, rules: TreeRules
    ) -> TreeHandler | None:
        """Resolve pre-handlers, the exact type, then post-handlers."""
        for resolver in rules.pre_resolvers:
            handler = resolver(element, traverse_aux)
            if handler is not None:
                return handler

        handler = rules.handlers.get(type(element))
        if handler is not None:
            return handler

        for resolver in rules.post_resolvers:
            handler = resolver(element, traverse_aux)
            if handler is not None:
                return handler

        return None

    @staticmethod
    def flatten(
        tree: Any,
        *,
        rules: TreeRules,
        is_leaf: IsLeafFunc | None = None,
    ) -> tuple[list[Any], "TreeDef"]:
        """
        Flatten a tree into a list of leaves and a structure definition.
        """
        leaves: list[Any] = []

        def _sink(leaf: Any, traverse_aux: TraverseAux) -> None:
            leaves.append(leaf)

        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        treedef = TreeEngine._traverse(
            tree, initial_traverse_aux, _sink, is_leaf, rules
        )
        return leaves, treedef

    @staticmethod
    def flatten_with_key_path(
        tree: Any,
        *,
        rules: TreeRules,
        is_leaf: IsLeafFunc | None = None,
    ) -> tuple[list[tuple[KeyPath, Any]], "TreeDef"]:
        """
        Flatten a tree into a list of (key_path, leaf) tuples and a structure definition.
        """
        leaves_with_path: list[tuple[KeyPath, Any]] = []

        def _sink(leaf: Any, traverse_aux: TraverseAux) -> None:
            leaves_with_path.append((traverse_aux.key_path, leaf))

        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        treedef = TreeEngine._traverse(
            tree, initial_traverse_aux, _sink, is_leaf, rules
        )
        return leaves_with_path, treedef

    @staticmethod
    def iter(
        tree: Any,
        *,
        rules: TreeRules,
        is_leaf: IsLeafFunc | None = None,
    ) -> Iterator[Any]:
        """
        Iterate over leaves of a tree without creating a TreeDef.
        """
        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        yield from TreeEngine._traverse_iter(
            tree, initial_traverse_aux, is_leaf, rules, with_key_path=False
        )

    @staticmethod
    def iter_with_key_path(
        tree: Any,
        *,
        rules: TreeRules,
        is_leaf: IsLeafFunc | None = None,
    ) -> Iterator[tuple[KeyPath, Any]]:
        """
        Iterate over (key_path, leaf) tuples of a tree without creating a TreeDef.
        """
        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        yield from TreeEngine._traverse_iter(
            tree, initial_traverse_aux, is_leaf, rules, with_key_path=True
        )

    @staticmethod
    def _prepare_element(
        element: Any,
        traverse_aux: TraverseAux,
        is_leaf: IsLeafFunc | None,
        rules: TreeRules,
    ) -> tuple[TreeHandler, Iterable[Any], Iterator[TreeKey], TreeAux] | None:
        """Return container traversal data, or None for a leaf."""
        if is_leaf is not None and is_leaf(element, traverse_aux):
            return None
        handler = TreeEngine._lookup_handler(element, traverse_aux, rules)
        if handler is None:
            return None
        children_iter, tree_aux = handler.flatten(element)

        if tree_aux.cls is None:
            tree_aux = replace(tree_aux, cls=type(element))

        if tree_aux.children_keys is not None:
            keys_iter = iter(tree_aux.children_keys)
        else:
            keys_iter = (SequenceKey(i) for i in count())

        return handler, children_iter, keys_iter, tree_aux

    @staticmethod
    def _traverse(
        element: Any,
        traverse_aux: TraverseAux,
        leaf_sink: _LeafSinkFunc,
        is_leaf: IsLeafFunc | None,
        rules: TreeRules,
    ) -> TreeDef:
        """Recursive core for traversal."""
        prepared = TreeEngine._prepare_element(element, traverse_aux, is_leaf, rules)

        if prepared is not None:
            handler, children_iter, keys_iter, tree_aux = prepared
            # Recursively map children.
            child_defs = []
            for child in children_iter:
                try:
                    key = next(keys_iter)
                except StopIteration:
                    # Should not happen if aux.key_path matches children length.
                    raise ValueError(
                        f"Not enough keys provided in TreeAux for container {type(element)}"
                    ) from None

                child_traverse_aux = TraverseAux(
                    parent=element, key_path=traverse_aux.key_path + (key,)
                )
                child_def = TreeEngine._traverse(
                    child,
                    child_traverse_aux,
                    leaf_sink,
                    is_leaf,
                    rules,
                )
                child_defs.append(child_def)

            return ContainerDef(
                type(element), tree_aux, tuple(child_defs), handler.unflatten
            )
        else:
            # Leaf.
            leaf_sink(element, traverse_aux)
            return LeafDef()

    @staticmethod
    def _traverse_iter(
        element: Any,
        traverse_aux: TraverseAux,
        is_leaf: IsLeafFunc | None,
        rules: TreeRules,
        with_key_path: bool,
    ) -> Iterator[Any]:
        """Recursive core for iterator traversal."""
        prepared = TreeEngine._prepare_element(element, traverse_aux, is_leaf, rules)

        if prepared is not None:
            _, children_iter, keys_iter, _ = prepared
            for child in children_iter:
                try:
                    key = next(keys_iter)
                except StopIteration:
                    # Should not happen if aux.key_path matches children length.
                    raise ValueError(
                        f"Not enough keys provided in TreeAux for container {type(element)}"
                    ) from None

                child_traverse_aux = TraverseAux(
                    parent=element, key_path=traverse_aux.key_path + (key,)
                )
                yield from TreeEngine._traverse_iter(
                    child,
                    child_traverse_aux,
                    is_leaf,
                    rules,
                    with_key_path,
                )
        else:
            # Leaf.
            if with_key_path:
                yield (traverse_aux.key_path, element)
            else:
                yield element

    @staticmethod
    def unflatten(treedef: "TreeDef", leaves: Iterable[Any]) -> Any:
        """
        Reconstruct the tree from a structure definition and a list of leaves.
        """
        return treedef.unflatten(leaves)

    @staticmethod
    def map(
        func: Callable[..., Any],
        tree: Any,
        *,
        rules: TreeRules,
        is_leaf: IsLeafFunc | None = None,
    ) -> Any:
        """Apply func to every leaf in the tree."""
        leaves, treedef = TreeEngine.flatten(tree, rules=rules, is_leaf=is_leaf)
        new_leaves = [func(leaf) for leaf in leaves]
        return TreeEngine.unflatten(treedef, new_leaves)

    @staticmethod
    def get_element(tree: Any, key_path: KeyPath) -> Any:
        """
        Retrieve an element from the tree using a specific key_path (Runtime KeyPath Resolution).
        """
        current = tree
        for key in key_path:
            current = key.resolve(current)
        return current

    @staticmethod
    def codify_key_path(key_path: KeyPath, root_name: str = "$") -> str:
        """
        Generate the Python code string corresponding to the key_path.
        """
        expr = root_name
        for key in key_path:
            expr = key.codify(expr)
        return expr
