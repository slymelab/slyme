"""
Tree structure utilities for slyme (PyTree-like).

Designed to be lightweight, explicit, and instance-isolated.
"""

import types
from dataclasses import dataclass, field, replace
from collections.abc import Iterable, Callable, Iterator, Hashable, Mapping
from itertools import count
from typing import (
    Any,
    Union,
    Protocol,
    Optional,
    Literal,
    cast,
)
from slyme.utils.registry import Registry, TypeRegistry

_EMPTY_MAPPING = types.MappingProxyType({})


@dataclass(frozen=True)
class PyTreeKey:
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
class SequenceKey(PyTreeKey):
    """Represents an index in a sequence (list, tuple)."""

    index: int

    def resolve(self, element: Any) -> Any:
        return element[self.index]

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{self.index}]"


@dataclass(frozen=True)
class MappingKey(PyTreeKey):
    """Represents a key in a mapping (dict)."""

    key: Hashable

    def resolve(self, element: Any) -> Any:
        return element[self.key]

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{repr(self.key)}]"


@dataclass(frozen=True)
class AttributeKey(PyTreeKey):
    """Represents an attribute name (object)."""

    name: str

    def resolve(self, element: Any) -> Any:
        return getattr(element, self.name)

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.{self.name}"


@dataclass(frozen=True)
class CallKey(PyTreeKey):
    """
    Represents a function call.

    If the function arguments are not hashable, this key falls back to identity semantics
    (object.__hash__ and self is other).
    """

    args: tuple[Any, ...] = field(default_factory=tuple)
    kwargs: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    hash: int = field(init=False, repr=False)
    is_hashable: bool = field(init=False, repr=False)

    def __post_init__(self):
        # Convert kwargs to MappingProxyType if needed
        if not isinstance(self.kwargs, types.MappingProxyType):
            object.__setattr__(self, "kwargs", types.MappingProxyType(self.kwargs))

        try:
            # Sort kwargs for deterministic hashing
            # Assuming keys are strings as per standard kwargs
            kwargs_tuple = tuple(sorted(self.kwargs.items()))
            computed_hash = hash((self.args, kwargs_tuple))
            is_hashable = True
        except TypeError:
            computed_hash = object.__hash__(self)
            is_hashable = False

        object.__setattr__(self, "hash", computed_hash)
        object.__setattr__(self, "is_hashable", is_hashable)

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, CallKey):
            return False

        # If either is not hashable, fall back to identity
        if not self.is_hashable or not other.is_hashable:
            return self is other

        return self.args == other.args and self.kwargs == other.kwargs

    def resolve(self, element: Any) -> Any:
        return element(*self.args, **self.kwargs)

    def codify(self, parent_expr: str) -> str:
        args_str = [repr(arg) for arg in self.args]
        kwargs_str = [f"{k}={repr(v)}" for k, v in self.kwargs.items()]
        combined = ", ".join(args_str + kwargs_str)
        return f"{parent_expr}({combined})"


class KeyPathExpr:
    """
    A proxy object to build KeyPaths using natural syntax.

    Examples:
        P = KeyPathExpr()
        path = tuple(P.foo["bar"][0](1, a=2))
        # path is a tuple of (AttributeKey, MappingKey, MappingKey, CallKey)
    """

    __slots__ = ("_keys",)

    def __init__(self, keys: tuple[PyTreeKey, ...] = ()):
        object.__setattr__(self, "_keys", keys)

    def _raise_immutable(self, *args, **kwargs):
        raise TypeError(f"{type(self).__name__} is immutable.")

    __setattr__ = _raise_immutable
    __delattr__ = _raise_immutable

    def __getattribute__(self, name: str) -> Any:
        # Everything is treated as an AttributeKey.
        # Use object.__getattribute__ explicitly to get internal state when needed.
        current_keys = object.__getattribute__(self, "_keys")
        new_key = AttributeKey(name)
        return KeyPathExpr(current_keys + (new_key,))

    def __getitem__(self, key: Any) -> "KeyPathExpr":
        # Unified MappingKey for both sequence index and mapping key
        current_keys = object.__getattribute__(self, "_keys")
        new_key = MappingKey(key)
        return KeyPathExpr(current_keys + (new_key,))

    def __call__(self, *args: Any, **kwargs: Any) -> "KeyPathExpr":
        current_keys = object.__getattribute__(self, "_keys")
        new_key = CallKey(args=args, kwargs=kwargs)
        return KeyPathExpr(current_keys + (new_key,))

    def __iter__(self) -> Iterator[PyTreeKey]:
        return iter(object.__getattribute__(self, "_keys"))

    def __repr__(self) -> str:
        keys = object.__getattribute__(self, "_keys")
        return f"KeyPathExpr(keys={keys!r})"


# Global proxy object
P = KeyPathExpr()
# Type Alias for Path
KeyPath = tuple[PyTreeKey, ...]


@dataclass(frozen=True)
class PyTreeAux:
    """
    Auxiliary data required to reconstruct a container and track paths.

    Attributes:
        metadata: Custom data needed for unflattening (e.g., specific flags).
        children_keys: Optional tuple of keys corresponding to the children.
    """

    metadata: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    children_keys: Optional[tuple[PyTreeKey, ...]] = None
    cls: Optional[type] = None

    def __post_init__(self):
        if not isinstance(self.metadata, types.MappingProxyType):
            object.__setattr__(self, "metadata", types.MappingProxyType(self.metadata))


class _FlattenFunc(Protocol):
    """
    Protocol for flattening a container.

    Returns:
        A tuple of (children_iterable, aux_data).
        - children_iterable: MUST yield pure child values.
        - aux_data: Contains metadata and optional keys for path tracking.
    """

    def __call__(self, element: Any, /) -> tuple[Iterable[Any], PyTreeAux]: ...


class _UnflattenFunc(Protocol):
    """
    Protocol for unflattening a container.
    """

    def __call__(self, children: Iterable[Any], tree_aux: PyTreeAux, /) -> Any: ...


@dataclass(frozen=True)
class _PyTreeHandler:
    flatten: _FlattenFunc
    unflatten: _UnflattenFunc


@dataclass(frozen=True)
class TraverseAux:
    """
    Auxiliary data during traversal.
    """

    parent: Any
    key_path: KeyPath


class _IsLeafFunc(Protocol):
    """
    Protocol for functions that determine if an element is a leaf.
    """

    def __call__(self, element: Any, traverse_aux: TraverseAux, /) -> bool: ...


class _ResolverFunc(Protocol):
    """
    Protocol for dynamic handler resolution.
    Accepts an auxiliary object.
    """

    def __call__(
        self, element: Any, traverse_aux: TraverseAux, /
    ) -> Union[_PyTreeHandler, None]: ...


class _LeafSinkFunc(Protocol):
    """Internal protocol for collecting leaves."""

    def __call__(self, leaf: Any, traverse_aux: TraverseAux, /) -> None: ...


@dataclass(frozen=True)
class PyTreeDef:
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
class LeafDef(PyTreeDef):
    def _build(self, leaves_iter: Iterator[Any]) -> Any:
        try:
            return next(leaves_iter)
        except StopIteration:
            raise ValueError("Too few leaves provided for this tree structure.")


@dataclass(frozen=True)
class ContainerDef(PyTreeDef):
    cls: type
    tree_aux: PyTreeAux
    children_defs: tuple[PyTreeDef, ...]
    unflatten_func: _UnflattenFunc = field(compare=False, repr=False)

    def _build(self, leaves_iter: Iterator[Any]) -> Any:
        children = [child._build(leaves_iter) for child in self.children_defs]
        return self.unflatten_func(children, self.tree_aux)


class PyTreeEngine:
    """
    A PyTree Engine that defines PyTree Operations.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        strict_registration: bool = True,
        allow_inheritance: bool = True,
        register_defaults: bool = True,
    ) -> None:
        self.name = repr(self) if name is None else name
        self.allow_inheritance = allow_inheritance

        # 1. Type-based Registry (O(1) lookup, Middle Priority)
        self._registry: TypeRegistry[Any, _PyTreeHandler] = TypeRegistry(
            f"PyTreeTypeRegistry<{self.name}>", strict=strict_registration
        )

        # 2. Dynamic Resolvers
        # Pre-resolvers: Checked BEFORE Type Registry (High Priority)
        self._pre_resolvers: list[_ResolverFunc] = []
        # Post-resolvers: Checked AFTER Type Registry (Low Priority)
        self._post_resolvers: list[_ResolverFunc] = []

        if register_defaults:
            self._register_defaults()

    def register(
        self,
        cls: type,
        flatten_func: _FlattenFunc,
        unflatten_func: _UnflattenFunc,
        strict: bool = True,
    ) -> None:
        """
        Register a custom type handler into the core TypeRegistry.
        This follows the explicit-is-better-than-implicit philosophy.
        """
        handler = _PyTreeHandler(flatten=flatten_func, unflatten=unflatten_func)
        self._registry.register(handler, key=cls, strict=strict)

    def register_resolver(
        self, resolver: _ResolverFunc, *, priority: Literal["pre", "post"] = "post"
    ) -> None:
        """
        Register a dynamic resolver function.

        Args:
            resolver: A function taking an element and returning a Handler or None.
            priority:
                - 'pre': Checked BEFORE the core TypeRegistry. Used to override
                  default behaviors or intercept specific instances.
                - 'post': Checked AFTER the core TypeRegistry. Used for generic
                  fallbacks (e.g., Dataclasses, Protocol checks).
        """
        if priority == "pre":
            self._pre_resolvers.append(resolver)
        elif priority == "post":
            self._post_resolvers.append(resolver)
        else:
            raise ValueError(f"Invalid priority: {priority}. Must be 'pre' or 'post'.")

    def _register_defaults(self) -> None:
        """Register standard python containers."""
        # Tuple
        self.register(
            tuple,
            lambda x: (iter(x), PyTreeAux()),
            lambda children, _: tuple(children),
        )
        # List
        self.register(
            list,
            lambda x: (iter(x), PyTreeAux()),
            lambda children, _: list(children),
        )

        # Dict
        def _flatten_dict(data: dict) -> tuple[Iterable[Any], PyTreeAux]:
            keys = tuple(data.keys())
            # Wrap keys in DictKey for path tracking.
            rich_keys = tuple(MappingKey(k) for k in keys)
            # Yield values as children.
            children = (data[k] for k in keys)
            return children, PyTreeAux(children_keys=rich_keys)

        def _unflatten_dict(children: Iterable[Any], tree_aux: PyTreeAux) -> dict:
            if tree_aux.children_keys is None:
                raise ValueError("Missing keys in TreeAux for dict unflattening.")
            # Unwrap DictKey to get raw keys.
            raw_keys = [k.key for k in cast("Iterable[MappingKey]", tree_aux.children_keys)]
            return dict(zip(raw_keys, children))

        self.register(dict, _flatten_dict, _unflatten_dict)

    def _lookup_handler(
        self, element: Any, traverse_aux: TraverseAux
    ) -> Union[_PyTreeHandler, None]:
        """
        Resolve handler via:
        1. Pre-resolvers (High Priority)
        2. Type Registry (Core Priority)
        3. Post-resolvers (Fallback Priority)
        """
        # 1. Try Pre-resolvers
        for resolver in self._pre_resolvers:
            handler = resolver(element, traverse_aux)
            if handler is not None:
                return handler

        # 2. Try Type Registry
        cls = type(element)
        handler = None
        if self.allow_inheritance:
            handler = self._registry.lookup(cls, default=None)
        else:
            handler = self._registry.get(cls, None)

        if handler is not None:
            return handler

        # 3. Try Post-resolvers
        for resolver in self._post_resolvers:
            handler = resolver(element, traverse_aux)
            if handler is not None:
                return handler

        return None

    def flatten(
        self,
        tree: Any,
        *,
        is_leaf: Optional[_IsLeafFunc] = None,
    ) -> tuple[list[Any], "PyTreeDef"]:
        """
        Flatten a tree into a list of leaves and a structure definition.
        """
        leaves: list[Any] = []

        def _sink(leaf: Any, traverse_aux: TraverseAux) -> None:
            leaves.append(leaf)

        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        treedef = self._traverse(tree, initial_traverse_aux, _sink, is_leaf)
        return leaves, treedef

    def flatten_with_key_path(
        self,
        tree: Any,
        *,
        is_leaf: Optional[_IsLeafFunc] = None,
    ) -> tuple[list[tuple[KeyPath, Any]], "PyTreeDef"]:
        """
        Flatten a tree into a list of (key_path, leaf) tuples and a structure definition.
        """
        leaves_with_path: list[tuple[KeyPath, Any]] = []

        def _sink(leaf: Any, traverse_aux: TraverseAux) -> None:
            leaves_with_path.append((traverse_aux.key_path, leaf))

        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        treedef = self._traverse(tree, initial_traverse_aux, _sink, is_leaf)
        return leaves_with_path, treedef

    def iter(
        self,
        tree: Any,
        *,
        is_leaf: Optional[_IsLeafFunc] = None,
    ) -> Iterator[Any]:
        """
        Iterate over leaves of a tree without creating a PyTreeDef.
        """
        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        yield from self._traverse_iter(
            tree, initial_traverse_aux, is_leaf, with_key_path=False
        )

    def iter_with_key_path(
        self,
        tree: Any,
        *,
        is_leaf: Optional[_IsLeafFunc] = None,
    ) -> Iterator[tuple[KeyPath, Any]]:
        """
        Iterate over (key_path, leaf) tuples of a tree without creating a PyTreeDef.
        """
        initial_traverse_aux = TraverseAux(parent=None, key_path=())
        yield from self._traverse_iter(
            tree, initial_traverse_aux, is_leaf, with_key_path=True
        )

    def _prepare_element(
        self,
        element: Any,
        traverse_aux: TraverseAux,
        is_leaf: Optional[_IsLeafFunc],
    ) -> tuple[bool, Optional[_PyTreeHandler], Iterable[Any], Iterator[Any], PyTreeAux]:
        """
        Helper to check if an element should be flattened and prepare iterators.
        Returns: (should_flatten, handler, children_iter, keys_iter, tree_aux)
        """
        # should_flatten check
        should_flatten = is_leaf is None or not is_leaf(element, traverse_aux)
        handler = None
        if should_flatten:
            handler = self._lookup_handler(element, traverse_aux)
            should_flatten = handler is not None

        if not should_flatten:
            # Return defaults for non-flattenable
            return False, None, [], iter([]), PyTreeAux()

        children_iter, tree_aux = handler.flatten(element)

        # Auto fill tree_aux info
        # TODO: Maybe refactor this into a function when the
        # auto fill logic grows.
        if tree_aux.cls is None:
            tree_aux = replace(tree_aux, cls=type(element))

        # Resolve Keys for Path Tracking.
        if tree_aux.children_keys is not None:
            keys_iter = iter(tree_aux.children_keys)
        else:
            # Default fallback: Generate SequenceKey for indices.
            keys_iter = (SequenceKey(i) for i in count())

        return True, handler, children_iter, keys_iter, tree_aux

    def _traverse(
        self,
        element: Any,
        traverse_aux: TraverseAux,
        leaf_sink: _LeafSinkFunc,
        is_leaf: Optional[_IsLeafFunc],
    ) -> PyTreeDef:
        """Recursive core for traversal."""
        should_flatten, handler, children_iter, keys_iter, tree_aux = (
            self._prepare_element(element, traverse_aux, is_leaf)
        )

        if should_flatten:
            # Recursively map children.
            child_defs = []
            for child in children_iter:
                try:
                    key = next(keys_iter)
                except StopIteration:
                    # Should not happen if aux.key_path matches children length.
                    raise ValueError(
                        f"Not enough keys provided in TreeAux for container {type(element)}"
                    )

                child_traverse_aux = TraverseAux(
                    parent=element, key_path=traverse_aux.key_path + (key,)
                )
                child_def = self._traverse(
                    child,
                    child_traverse_aux,
                    leaf_sink,
                    is_leaf,
                )
                child_defs.append(child_def)

            return ContainerDef(
                type(element), tree_aux, tuple(child_defs), handler.unflatten
            )
        else:
            # Leaf.
            leaf_sink(element, traverse_aux)
            return LeafDef()

    def _traverse_iter(
        self,
        element: Any,
        traverse_aux: TraverseAux,
        is_leaf: Optional[_IsLeafFunc],
        with_key_path: bool,
    ) -> Iterator[Any]:
        """Recursive core for iterator traversal."""
        should_flatten, _, children_iter, keys_iter, _ = self._prepare_element(
            element, traverse_aux, is_leaf
        )

        if should_flatten:
            for child in children_iter:
                try:
                    key = next(keys_iter)
                except StopIteration:
                    # Should not happen if aux.key_path matches children length.
                    raise ValueError(
                        f"Not enough keys provided in TreeAux for container {type(element)}"
                    )

                child_traverse_aux = TraverseAux(
                    parent=element, key_path=traverse_aux.key_path + (key,)
                )
                yield from self._traverse_iter(
                    child,
                    child_traverse_aux,
                    is_leaf,
                    with_key_path,
                )
        else:
            # Leaf.
            if with_key_path:
                yield (traverse_aux.key_path, element)
            else:
                yield element

    @staticmethod
    def unflatten(treedef: "PyTreeDef", leaves: Iterable[Any]) -> Any:
        """
        Reconstruct the tree from a structure definition and a list of leaves.
        """
        return treedef.unflatten(leaves)

    def map(
        self,
        func: Callable[..., Any],
        tree: Any,
        *,
        is_leaf: Optional[_IsLeafFunc] = None,
    ) -> Any:
        """Apply func to every leaf in the tree."""
        leaves, treedef = self.flatten(tree, is_leaf=is_leaf)
        new_leaves = [func(leaf) for leaf in leaves]
        return self.unflatten(treedef, new_leaves)

    @staticmethod
    def get_element(tree: Any, key_path: KeyPath) -> Any:
        """
        Retrieve an element from the tree using a specific key_path (Runtime KeyPath Resolution).
        """
        current = tree
        for i, key in enumerate(key_path):
            if not isinstance(key, PyTreeKey):
                raise TypeError(
                    f"Invalid key_path key at index {i}: expected PyTreeKey, got {type(key)}."
                )
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


# Global registry to manage Tree instances.
PYTREE_ENGINE_REGISTRY: Registry[PyTreeEngine] = Registry("pytree_engine")
