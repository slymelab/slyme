"""
Tree structure utilities for slyme (PyTree-like).

Designed to be lightweight, explicit, and instance-isolated.
"""

from dataclasses import dataclass, field
from collections.abc import Iterable, Callable, Iterator, Hashable
from itertools import count
from typing import (
    Any,
    Union,
    Protocol,
    Optional,
    Literal,
)
from slyme.utils.registry import Registry, TypeRegistry


@dataclass(frozen=True)
class PyTreeKey:
    """Base class for path entries."""

    key: Any = 0

    def resolve(self, obj: Any) -> Any:
        """
        Resolve the key against the given object to retrieve the child.
        Acts as the 'Getter' logic in Lens.
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

    key: int

    def resolve(self, obj: Any) -> Any:
        try:
            return obj[self.key]
        except (IndexError, TypeError) as e:
            raise KeyError(
                f"Cannot access index {self.key} from object of type {type(obj).__name__}"
            ) from e

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{self.key}]"


@dataclass(frozen=True)
class MappingKey(PyTreeKey):
    """Represents a key in a mapping (dict)."""

    key: Hashable

    def resolve(self, obj: Any) -> Any:
        try:
            return obj[self.key]
        except (KeyError, TypeError) as e:
            raise KeyError(
                f"Cannot access key {self.key!r} from object of type {type(obj).__name__}"
            ) from e

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}[{repr(self.key)}]"


@dataclass(frozen=True)
class AttributeKey(PyTreeKey):
    """Represents an attribute name (object)."""

    key: str

    def resolve(self, obj: Any) -> Any:
        try:
            return getattr(obj, self.key)
        except AttributeError as e:
            raise KeyError(
                f"Cannot access attribute {self.key!r} from object of type {type(obj).__name__}"
            ) from e

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.{self.key}"


# Type Alias for Path
KeyPath = tuple[PyTreeKey, ...]


@dataclass(frozen=True)
class PyTreeAux:
    """
    Auxiliary data required to reconstruct a container and track paths.

    Attributes:
        metadata: Custom data needed for unflattening (e.g., specific flags).
        keys: Optional sequence of keys corresponding to the children.

              Used for path tracking during flattening and potentially for structure
              reconstruction during unflattening.

              WARNING: Must be a reusable iterable (e.g., tuple, list). Do NOT use
              a one-time iterator (like a generator), as it may be iterated over
              multiple times (once during flatten, and again during unflatten).

              If None, defaults to SequenceKey(0), SequenceKey(1), ...
    """

    metadata: dict[str, Any] = field(default_factory=dict)
    keys: Optional[Iterable[PyTreeKey]] = None


class _FlattenFunc(Protocol):
    """
    Protocol for flattening a container.

    Returns:
        A tuple of (children_iterable, aux_data).
        - children_iterable: MUST yield pure child values.
        - aux_data: Contains metadata and optional keys for path tracking.
    """

    def __call__(self, obj: Any, /) -> tuple[Iterable[Any], PyTreeAux]: ...


class _UnflattenFunc(Protocol):
    """
    Protocol for unflattening a container.
    """

    def __call__(self, aux: PyTreeAux, children: Iterable[Any], /) -> Any: ...


@dataclass(frozen=True)
class _PyTreeHandler:
    flatten: _FlattenFunc
    unflatten: _UnflattenFunc


class _ResolverFunc(Protocol):
    """
    Protocol for dynamic handler resolution.
    Returns a handler if the object matches the criteria, else None.
    """

    def __call__(self, obj: Any, /) -> Union[_PyTreeHandler, None]: ...


@dataclass(frozen=True)
class PyTreeDef:
    """Base class for tree definitions."""

    def unflatten(self, leaves: Iterable[Any]) -> Any:
        """
        Public API: Reconstruct the object from this structure and leaves.
        """
        leaves_iter = iter(leaves)
        obj = self._build(leaves_iter)

        try:
            next(leaves_iter)
            raise ValueError("Too many leaves provided for this tree structure.")
        except StopIteration:
            pass

        return obj

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
    aux: PyTreeAux
    children_defs: tuple[PyTreeDef, ...]
    unflatten_func: _UnflattenFunc = field(compare=False, repr=False)

    def _build(self, leaves_iter: Iterator[Any]) -> Any:
        children = [child._build(leaves_iter) for child in self.children_defs]
        return self.unflatten_func(self.aux, children)


class PyTreeEngine:
    """
    A PyTree Engine that defines PyTree Operations.
    """

    def __init__(
        self,
        name: Optional[str] = None,
        strict_registration: bool = True,
        allow_inheritance: bool = True,
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
            resolver: A function taking an object and returning a Handler or None.
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
            lambda _, children: tuple(children),
        )
        # List
        self.register(
            list,
            lambda x: (iter(x), PyTreeAux()),
            lambda _, children: list(children),
        )

        # Dict
        def _flatten_dict(data: dict) -> tuple[Iterable[Any], PyTreeAux]:
            keys = tuple(data.keys())
            # Wrap keys in DictKey for path tracking.
            rich_keys = tuple(MappingKey(k) for k in keys)
            # Yield values as children.
            children = (data[k] for k in keys)
            return children, PyTreeAux(keys=rich_keys)

        def _unflatten_dict(aux: PyTreeAux, children: Iterable[Any]) -> dict:
            if aux.keys is None:
                raise ValueError("Missing keys in TreeAux for dict unflattening.")
            # Unwrap DictKey to get raw keys.
            raw_keys = [k.key for k in aux.keys]
            return dict(zip(raw_keys, children))

        self.register(dict, _flatten_dict, _unflatten_dict)

    def _lookup_handler(self, obj: Any) -> Union[_PyTreeHandler, None]:
        """
        Resolve handler via:
        1. Pre-resolvers (High Priority)
        2. Type Registry (Core Priority)
        3. Post-resolvers (Fallback Priority)
        """
        # 1. Try Pre-resolvers
        for resolver in self._pre_resolvers:
            handler = resolver(obj)
            if handler is not None:
                return handler

        # 2. Try Type Registry
        cls = type(obj)
        handler = None
        if self.allow_inheritance:
            handler = self._registry.lookup(cls, default=None)
        else:
            handler = self._registry.get(cls)

        if handler is not None:
            return handler

        # 3. Try Post-resolvers
        for resolver in self._post_resolvers:
            handler = resolver(obj)
            if handler is not None:
                return handler

        return None

    def flatten(
        self,
        tree: Any,
        *,
        is_leaf: Optional[Callable[[Any], bool]] = None,
    ) -> tuple[list[Any], "PyTreeDef"]:
        """
        Flatten a tree into a list of leaves and a structure definition.
        """
        leaves: list[Any] = []

        def _sink(path: KeyPath, leaf: Any) -> None:
            leaves.append(leaf)

        treedef = self._traverse(tree, _sink, (), is_leaf)
        return leaves, treedef

    def flatten_with_path(
        self,
        tree: Any,
        *,
        is_leaf: Optional[Callable[[Any], bool]] = None,
    ) -> tuple[list[tuple[KeyPath, Any]], "PyTreeDef"]:
        """
        Flatten a tree into a list of (path, leaf) tuples and a structure definition.
        """
        leaves_with_path: list[tuple[KeyPath, Any]] = []

        def _sink(path: KeyPath, leaf: Any) -> None:
            leaves_with_path.append((path, leaf))

        treedef = self._traverse(tree, _sink, (), is_leaf)
        return leaves_with_path, treedef

    def _traverse(
        self,
        entry: Any,
        leaf_sink: Callable[[KeyPath, Any], None],
        current_path: KeyPath,
        is_leaf: Optional[Callable[[Any], bool]],
    ) -> PyTreeDef:
        """Recursive core for traversal."""
        # should_flatten check
        handler = None
        should_flatten = is_leaf is None or not is_leaf(entry)
        if should_flatten:
            handler = self._lookup_handler(entry)
            should_flatten = handler is not None

        if should_flatten:
            children_iter, aux = handler.flatten(entry)

            # 3. Resolve Keys for Path Tracking.
            if aux.keys is not None:
                keys_iter = iter(aux.keys)
            else:
                # Default fallback: Generate SequenceKey for indices.
                keys_iter = (SequenceKey(i) for i in count())

            # Recursively map children.
            child_defs = []
            for child in children_iter:
                try:
                    key = next(keys_iter)
                except StopIteration:
                    # Should not happen if aux.keys matches children length.
                    raise ValueError(
                        f"Not enough keys provided in TreeAux for container {type(entry)}"
                    )

                child_def = self._traverse(
                    child,
                    leaf_sink,
                    current_path + (key,),
                    is_leaf,
                )
                child_defs.append(child_def)

            return ContainerDef(type(entry), aux, tuple(child_defs), handler.unflatten)
        else:
            # Leaf.
            leaf_sink(current_path, entry)
            return LeafDef()

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
        is_leaf: Optional[Callable[[Any], bool]] = None,
    ) -> Any:
        """Apply func to every leaf in the tree."""
        leaves, treedef = self.flatten(tree, is_leaf=is_leaf)
        new_leaves = [func(leaf) for leaf in leaves]
        return self.unflatten(treedef, new_leaves)

    @staticmethod
    def get_entry(tree: Any, path: KeyPath) -> Any:
        """
        Retrieve an entry from the tree using a specific path (Runtime Lens).
        """
        current = tree
        for i, key in enumerate(path):
            if not isinstance(key, PyTreeKey):
                raise TypeError(
                    f"Invalid path key at index {i}: expected PyTreeKey, got {type(key)}."
                )
            current = key.resolve(current)
        return current

    @staticmethod
    def codify_path(path: KeyPath, root_name: str = "tree") -> str:
        """
        Generate the Python code string corresponding to the path.
        """
        expr = root_name
        for key in path:
            expr = key.codify(expr)
        return expr


# Register the default pytree engine.
default_pytree_engine = PyTreeEngine("default")
# Global registry to manage Tree instances.
PYTREE_ENGINE_REGISTRY: Registry[PyTreeEngine] = Registry("pytree_engine")
PYTREE_ENGINE_REGISTRY.register(default_pytree_engine, key="default")
