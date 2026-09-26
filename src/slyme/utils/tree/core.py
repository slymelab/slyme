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

Traversal functions consume explicit, immutable rules. Type handlers match
exactly; unregistered objects remain leaves unless a resolver supplies a handler.
Traversal context is created only for path output or a resolver with takes_aux=True.
"""

from __future__ import annotations

import builtins
import types
from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from typing import (
    Any,
    Protocol,
    cast,
)

_EMPTY_MAPPING: Mapping[str, Any] = types.MappingProxyType({})


@dataclass(frozen=True, slots=True)
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


@dataclass(frozen=True, slots=True)
class SequenceKey(TreeKey):
    """Represents an index in a sequence (list, tuple)."""

    index: int

    def resolve(self, element: Any) -> Any:
        return element[self.index]

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{self.index}]"


@dataclass(frozen=True, slots=True)
class MappingKey(TreeKey):
    """Represents a key in a mapping (dict)."""

    key: Hashable

    def resolve(self, element: Any) -> Any:
        return element[self.key]

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{repr(self.key)}]"


@dataclass(frozen=True, slots=True)
class AttributeKey(TreeKey):
    """Represents an attribute name (object)."""

    name: str

    def resolve(self, element: Any) -> Any:
        return getattr(element, self.name)

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.{self.name}"


# Type Alias for Path
KeyPath = tuple[TreeKey, ...]


@dataclass(frozen=True, slots=True)
class TreeAux:
    """
    Auxiliary data required to reconstruct a container and track paths.

    Attributes:
        metadata: Custom data needed for unflattening (e.g., specific flags).
        children_keys: Keys corresponding to children, required when tracking paths.
        cls: Optional handler-provided type, preserved unchanged.
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


@dataclass(frozen=True, slots=True)
class TreeHandler:
    """Container expansion and optional reconstruction functions."""

    flatten: FlattenFunc
    unflatten: UnflattenFunc | None


@dataclass(frozen=True, slots=True)
class TraverseAux:
    """
    Auxiliary data during traversal.
    """

    parent: Any
    key_path: KeyPath


_ResolverFunc = Callable[[Any], TreeHandler | bool]
_AuxResolverFunc = Callable[[Any, TraverseAux], TreeHandler | bool]


@dataclass(frozen=True, slots=True)
class TreeResolver:
    """Override dispatch for one traversal.

    Return True for a leaf, False for exact-type lookup, or a TreeHandler.
    With takes_aux=True, func receives (element, aux), including the parent
    and full key path; otherwise it receives only element.
    """

    func: _ResolverFunc | _AuxResolverFunc
    takes_aux: bool = False


@dataclass(frozen=True, slots=True)
class TreeRules:
    """A shallow immutable snapshot of exact-type handlers."""

    handlers: Mapping[type, TreeHandler] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "handlers", types.MappingProxyType(dict(self.handlers))
        )

    @staticmethod
    def merge(values: tuple[TreeRules, ...]) -> TreeRules:
        """Keep the first handler for each type."""
        handlers: dict[type, TreeHandler] = {}
        for rules in values:
            for cls, handler in rules.handlers.items():
                handlers.setdefault(cls, handler)
        return TreeRules(handlers)


@dataclass(frozen=True, slots=True)
class _Container:
    cls: type
    tree_aux: TreeAux
    child_count: int
    unflatten_func: UnflattenFunc | None = field(compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class TreeDef:
    """Immutable flat reconstruction program returned by flattening."""

    _operations: tuple[_Container | None, ...]

    def unflatten(self, leaves: Iterable[Any]) -> Any:
        """
        Public API: Reconstruct the object from this structure and leaves.
        """
        leaves_iter = builtins.iter(leaves)
        stack: list[Any] = []
        for operation in self._operations:
            if operation is None:
                try:
                    stack.append(next(leaves_iter))
                except StopIteration:
                    raise ValueError(
                        "Too few leaves provided for this tree structure."
                    ) from None
                continue
            if operation.unflatten_func is None:
                raise TypeError(
                    f"{operation.cls.__name__} is registered for traversal only; "
                    "unflatten_func is None."
                )
            count = operation.child_count
            if count:
                children = tuple(stack[-count:])
                del stack[-count:]
            else:
                children = ()
            stack.append(operation.unflatten_func(children, operation.tree_aux))

        try:
            next(leaves_iter)
            raise ValueError("Too many leaves provided for this tree structure.")
        except StopIteration:
            pass

        return stack[0]


def flatten(
    tree: Any,
    *,
    rules: TreeRules,
    resolver: TreeResolver | None = None,
) -> tuple[list[Any], TreeDef]:
    """
    Flatten a tree into a list of leaves and a structure definition.
    """
    return _flatten(tree, rules, resolver, with_key_path=False)


def flatten_with_key_path(
    tree: Any,
    *,
    rules: TreeRules,
    resolver: TreeResolver | None = None,
) -> tuple[list[tuple[KeyPath, Any]], TreeDef]:
    """
    Flatten a tree into a list of (key_path, leaf) tuples and a structure definition.
    """
    return _flatten(tree, rules, resolver, with_key_path=True)


def iter(
    tree: Any,
    *,
    rules: TreeRules,
    resolver: TreeResolver | None = None,
) -> Iterator[Any]:
    """
    Iterate over leaves of a tree without creating a TreeDef.
    """
    yield from _iter(tree, rules, resolver, with_key_path=False)


def iter_with_key_path(
    tree: Any,
    *,
    rules: TreeRules,
    resolver: TreeResolver | None = None,
) -> Iterator[tuple[KeyPath, Any]]:
    """
    Iterate over (key_path, leaf) tuples of a tree without creating a TreeDef.
    """
    yield from _iter(tree, rules, resolver, with_key_path=True)


def _prepare_element(
    element: Any,
    traverse_aux: TraverseAux | None,
    resolver: TreeResolver | None,
    rules: TreeRules,
) -> tuple[TreeHandler, Iterable[Any], TreeAux] | None:
    """Return container traversal data, or None for a leaf."""
    decision: TreeHandler | bool = False
    if resolver is not None:
        if resolver.takes_aux:
            decision = cast(_AuxResolverFunc, resolver.func)(
                element, cast(TraverseAux, traverse_aux)
            )
        else:
            decision = cast(_ResolverFunc, resolver.func)(element)
    if decision is True:
        return None
    handler = rules.handlers.get(type(element)) if decision is False else decision
    if handler is None:
        return None
    children_iter, tree_aux = handler.flatten(element)

    return handler, children_iter, tree_aux


def _child_aux(
    parent: Any,
    aux: TraverseAux | None,
    data: TreeAux,
    index: int,
) -> TraverseAux | None:
    if aux is None:
        return None
    keys = data.children_keys
    if keys is None:
        raise ValueError("TreeAux.children_keys is required for key path traversal.")
    if index >= len(keys):
        raise ValueError("Not enough keys provided in TreeAux for container.")
    return TraverseAux(parent=parent, key_path=aux.key_path + (keys[index],))


def _flatten(
    tree: Any,
    rules: TreeRules,
    resolver: TreeResolver | None,
    with_key_path: bool,
) -> tuple[list[Any], TreeDef]:
    leaves: list[Any] = []
    operations: list[_Container | None] = []
    aux = (
        TraverseAux(parent=None, key_path=())
        if with_key_path or (resolver is not None and resolver.takes_aux)
        else None
    )
    prepared = _prepare_element(tree, aux, resolver, rules)
    if prepared is None:
        leaves.append(((), tree) if with_key_path else tree)
        operations.append(None)
    else:
        _flatten_container(
            tree, prepared, aux, leaves, operations, resolver, rules, with_key_path
        )
    return leaves, TreeDef(tuple(operations))


def _flatten_container(
    element: Any,
    prepared: tuple[TreeHandler, Iterable[Any], TreeAux],
    traverse_aux: TraverseAux | None,
    leaves: list[Any],
    operations: list[_Container | None],
    resolver: TreeResolver | None,
    rules: TreeRules,
    with_key_path: bool,
) -> None:
    """Shared traversal for all flatten modes; recurse only into containers."""
    handler, children, tree_aux = prepared
    count = 0
    for count, child in enumerate(children, 1):
        child_aux = _child_aux(element, traverse_aux, tree_aux, count - 1)
        child_prepared = _prepare_element(child, child_aux, resolver, rules)
        if child_prepared is None:
            leaves.append(
                (cast(TraverseAux, child_aux).key_path, child)
                if with_key_path
                else child
            )
            operations.append(None)
        else:
            _flatten_container(
                child,
                child_prepared,
                child_aux,
                leaves,
                operations,
                resolver,
                rules,
                with_key_path,
            )
    operations.append(_Container(type(element), tree_aux, count, handler.unflatten))


def _iter(
    tree: Any,
    rules: TreeRules,
    resolver: TreeResolver | None,
    with_key_path: bool,
) -> Iterator[Any]:
    aux = (
        TraverseAux(parent=None, key_path=())
        if with_key_path or (resolver is not None and resolver.takes_aux)
        else None
    )
    prepared = _prepare_element(tree, aux, resolver, rules)
    if prepared is None:
        yield ((), tree) if with_key_path else tree
    else:
        yield from _iter_container(tree, prepared, aux, resolver, rules, with_key_path)


def _iter_container(
    element: Any,
    prepared: tuple[TreeHandler, Iterable[Any], TreeAux],
    traverse_aux: TraverseAux | None,
    resolver: TreeResolver | None,
    rules: TreeRules,
    with_key_path: bool,
) -> Iterator[Any]:
    """Stream leaves directly, recursing only into child containers."""
    _, children, tree_aux = prepared
    for index, child in enumerate(children):
        child_aux = _child_aux(element, traverse_aux, tree_aux, index)
        child_prepared = _prepare_element(child, child_aux, resolver, rules)
        if child_prepared is None:
            yield (
                (cast(TraverseAux, child_aux).key_path, child)
                if with_key_path
                else child
            )
        else:
            yield from _iter_container(
                child,
                child_prepared,
                child_aux,
                resolver,
                rules,
                with_key_path,
            )


def map(
    func: Callable[..., Any],
    tree: Any,
    *,
    rules: TreeRules,
    resolver: TreeResolver | None = None,
) -> Any:
    """Apply func to every leaf in the tree."""
    leaves, treedef = flatten(tree, rules=rules, resolver=resolver)
    new_leaves = [func(leaf) for leaf in leaves]
    return treedef.unflatten(new_leaves)


def get_element(tree: Any, key_path: KeyPath) -> Any:
    """
    Retrieve an element from the tree using a specific key_path (Runtime KeyPath Resolution).
    """
    current = tree
    for key in key_path:
        current = key.resolve(current)
    return current


def codify_key_path(key_path: KeyPath, root_name: str = "$") -> str:
    """
    Generate the Python code string corresponding to the key_path.
    """
    expr = root_name
    for key in key_path:
        expr = key.codify(expr)
    return expr
