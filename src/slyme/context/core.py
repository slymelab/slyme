import types
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass, field, InitVar
from collections import defaultdict
from collections.abc import Iterable, Mapping, Callable
from typing import (
    Any,
    Generic,
    TypeVar,
    Union,
    Literal,
    cast,
    Optional,
    overload,
)
from typing_extensions import Self
from slyme.utils.pytree import (
    PyTreeEngine,
    PyTreeAux,
    MappingKey,
    PYTREE_ENGINE_REGISTRY,
    KeyPath,
)

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_EMPTY_METADATA = types.MappingProxyType({})
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


class Config:
    repr_indent: str
    repr_newline: str
    repr_suffix: str
    repr_last_suffix: str
    leaf_formatter: Callable[[Any], str]

    @classmethod
    def set_compact_repr(cls) -> type[Self]:
        cls.repr_indent = ""
        cls.repr_newline = ""
        cls.repr_suffix = ", "
        cls.repr_last_suffix = ""
        return cls

    @classmethod
    def set_pretty_repr(cls) -> type[Self]:
        cls.repr_indent = "    "
        cls.repr_newline = "\n"
        cls.repr_suffix = ","
        cls.repr_last_suffix = ","
        return cls

    @classmethod
    def set_truncated_repr(cls, max_len: int = 100) -> type[Self]:
        def _truncated(obj: Any) -> str:
            s = repr(obj)
            return s if len(s) <= max_len else s[:max_len] + "..."

        cls.leaf_formatter = _truncated
        return cls


Config.set_pretty_repr().set_truncated_repr(max_len=100)


@dataclass(frozen=True, repr=False, eq=False)
class Ref(Generic[_T]):
    """Immutable dotted ref with cached hash and split parts."""

    path: str
    lens: KeyPath = ()
    metadata: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_METADATA)
    parts: tuple[str, ...] = field(init=False)
    hash: int = field(init=False)

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Empty ref path")
        parts = tuple(self.path.split("."))
        if any(not p for p in parts):
            raise ValueError(f"Invalid ref path: {self.path!r}")

        # Bypass frozen=True to set calculated fields
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "hash", hash((parts, self.lens)))
        if not isinstance(self.metadata, types.MappingProxyType):
            object.__setattr__(self, "metadata", types.MappingProxyType(self.metadata))

    def resolve(self, pytree) -> _T:
        return PyTreeEngine.get_element(pytree, self.lens)

    def update_metadata(self, metadata: Mapping[str, Any]) -> "Ref[_T]":
        """Returns a new Ref with updated metadata (merging with existing)."""
        new_metadata = dict(self.metadata)
        new_metadata.update(metadata)
        return Ref(self.path, lens=self.lens, metadata=new_metadata)

    def at(
        self,
        subpath: str,
        lens: Union[KeyPath, _Missing] = _MISSING,
        metadata: Union[Optional[Mapping[str, Any]], _Missing] = _MISSING,
    ) -> "Ref":
        """
        Create a new Ref at a subpath relative to this Ref.

        Does NOT inherit lens or metadata from the parent Ref by default.
        """
        new_path = f"{self.path}.{subpath}" if self.path else subpath
        kwargs = {}
        if lens is not _MISSING:
            kwargs["lens"] = lens
        if metadata is not _MISSING:
            kwargs["metadata"] = metadata
        return Ref(new_path, **kwargs)

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, Ref)
            and self.parts == other.parts
            and self.lens == other.lens
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.extra_repr()})"

    def extra_repr(self) -> str:
        repr_items = [f"path={self.path!r}"]
        if self.lens:
            repr_items.append(f"lens_expr={PyTreeEngine.codify_path(self.lens)}")
        if self.metadata:
            repr_items.append(f"metadata={self.metadata!r}")
        return ", ".join(repr_items)


class ContextPathError(KeyError):
    """Internal exception raised when a path cannot be resolved in the context."""

    pass


class ContextData(dict[str, Any]):
    """
    Internal dictionary implementation used to distinguish structural elements
    from user-provided dictionary values.

    Acts as the core "Smart Node" for the Context, handling recursive Copy-On-Write logic.
    """

    __slots__ = ()

    def _raise_immutable(self, *args, **kwargs):
        raise TypeError(
            f"{type(self).__name__} is immutable. "
            "Use functional modifications (e.g. context.mutate/update/drop) to create a new instance."
        )

    # Disable all mutable methods via assignment to minimize boilerplate
    __setitem__ = _raise_immutable
    __delitem__ = _raise_immutable
    pop = _raise_immutable
    popitem = _raise_immutable
    clear = _raise_immutable
    update = _raise_immutable
    setdefault = _raise_immutable
    # Disable in-place operators
    __ior__ = _raise_immutable

    def mutate(
        self,
        updates: dict[tuple[str, ...], Any],
        drops: set[tuple[str, ...]],
    ) -> "ContextData":
        """
        Core recursive Copy-On-Write (COW) algorithm for simultaneous updates and drops.

        Semantics:
        - Updates overwrite everything (Priority 1).
        - Drop at root + Updates at children = Reset & Apply (Drop clears existing, Updates build new).
        - Drop at root + No Updates = Delete.
        """
        # 1. Base Cases
        if () in updates:
            return updates[()]

        # Determine Base State (Reset vs Copy)
        if () in drops:
            # Reset: Start from empty. Existing data is discarded.
            if not updates:
                return _MISSING
            new_data = {}
        else:
            # Copy: Start from existing.
            if not updates and not drops:
                return self
            new_data = dict(self)

        # 2. Group Operations
        grouped_ops = defaultdict(lambda: ({}, set()))

        for path, val in updates.items():
            if not path:
                continue  # Handled above
            head, *tail = path
            grouped_ops[head][0][tuple(tail)] = val

        for path in drops:
            if not path:
                continue  # Handled above
            head, *tail = path
            grouped_ops[head][1].add(tuple(tail))

        # 3. Recursive Application
        for head, (sub_updates, sub_drops) in grouped_ops.items():
            # Optimization: Exact overwrite
            if () in sub_updates:
                new_data[head] = sub_updates.pop(())
                # If no other updates/drops for this head, we are done
                if not sub_updates and not sub_drops:
                    continue

            # Get existing child or MISSING (if Reset, it's always MISSING)
            child = new_data.get(head, _MISSING)

            # Structure Validation / Auto-Vivification
            if not isinstance(child, ContextData):
                if child is _MISSING:
                    # Create new container for updates
                    if not sub_updates:
                        continue
                    child = ContextData()
                else:
                    # Conflict: Path blocked by leaf
                    raise ContextPathError(
                        f"Path '{head}' blocked by leaf value during mutation."
                    )

            try:
                new_child = child.mutate(sub_updates, sub_drops)
                if new_child is _MISSING:
                    new_data.pop(head, None)
                else:
                    new_data[head] = new_child
            except ContextPathError:
                raise ContextPathError(head) from None

        return ContextData(new_data)


@dataclass(frozen=True)
class DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class ContextElement(ABC):
    """
    Abstract base class for context-related entities (Context, ContextView).
    """

    __slots__ = ()

    @abstractmethod
    def get(
        self, ref: Ref[_T], default: Union[_T2, _Missing] = _MISSING
    ) -> Union[_T, _T2]:
        pass

    @abstractmethod
    def exists(self, ref: Ref[_T]) -> bool:
        pass

    @abstractmethod
    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        pass

    @abstractmethod
    def to_context_data(self, ref: Optional[Ref[_T]] = None) -> ContextData:
        pass

    def to_dict(self, ref: Optional[Ref[_T]] = None) -> dict[str, Any]:
        """Convert to standard python dictionary recursively."""

        def _recursive_to_dict(data: Any) -> Any:
            if isinstance(data, ContextData):
                return {k: _recursive_to_dict(v) for k, v in data.items()}
            return data

        root = self.to_context_data(ref)
        return _recursive_to_dict(root)

    def type_repr(self) -> str:
        return type(self).__name__

    def __repr__(self) -> str:
        name = self.type_repr()
        try:
            keys = list(self.keys())
        except ContextPathError:
            return f"{name}(<invalid path>)"

        if not keys:
            return f"{name}()"

        newline = Config.repr_newline
        indent = Config.repr_indent
        formatter = Config.leaf_formatter

        item_blocks = []
        for key in keys:
            val = self.get(Ref(key))
            v_str = repr(val) if isinstance(val, ContextElement) else formatter(val)

            if newline:
                v_lines = v_str.split(newline)
                if len(v_lines) > 1 and not v_lines[-1]:
                    v_lines.pop()
            else:
                v_lines = [v_str]

            block = [f"{indent}{key!r}: {v_lines[0]}"]
            block.extend(f"{indent}{line}" for line in v_lines[1:])
            item_blocks.append(block)

        body_lines = []
        count = len(item_blocks)
        for i, block in enumerate(item_blocks):
            suffix = Config.repr_last_suffix if i == count - 1 else Config.repr_suffix
            block[-1] += suffix
            body_lines.extend(block)

        return f"{name}({{{newline}{newline.join(body_lines)}{newline}}})"

    def diff(
        self, other: "ContextElement", strategy: Literal["is", "eq"] = "is"
    ) -> DiffResult:
        if strategy not in ("is", "eq"):
            raise ValueError(f"Unknown diff strategy: {strategy!r}")

        leaves_self = self.collect_leaves()
        leaves_other = other.collect_leaves()

        added: dict[str, Any] = {}
        removed: dict[str, Any] = {}
        modified: dict[str, tuple[Any, Any]] = {}

        keys_self = set(leaves_self.keys())
        keys_other = set(leaves_other.keys())

        for k in keys_self - keys_other:
            removed[k] = leaves_self[k]
        for k in keys_other - keys_self:
            added[k] = leaves_other[k]

        for k in keys_self & keys_other:
            val_self = leaves_self[k]
            val_other = leaves_other[k]
            is_different = False
            if strategy == "is":
                is_different = val_self is not val_other
            elif strategy == "eq":
                is_different = val_self != val_other

            if is_different:
                modified[k] = (val_self, val_other)

        return DiffResult(added, removed, modified)

    def collect_leaves(self) -> dict[str, Any]:
        # CONTEXT_PYTREE_ENGINE is configured to only traverse Context/ContextData structure.
        # So it treats user dicts as leaves automatically.
        # Use iter_with_path for memory efficiency (generator based)
        iterator = CONTEXT_PYTREE_ENGINE.iter_with_path(self.to_context_data())

        leaves = {}
        for path, leaf in iterator:
            # We know ContextData keys are MappingKeys wrapping strings
            parts = [str(cast(MappingKey, k).key) for k in path]
            leaves[".".join(parts)] = leaf
        return leaves


@dataclass(frozen=True, repr=False)
class Context(ContextElement):
    """
    Immutable Context implementation with efficient Copy-On-Write (COW) updates.
    Wraps a root `ContextData`.

    The main **runtime** context container for the node execution.
    """

    _root: ContextData = field(init=False)
    data: InitVar[Optional[Mapping[str, Any]]] = None

    def __post_init__(self, data: Optional[Mapping[str, Any]] = None) -> None:
        if data is None:
            root = ContextData()
        elif isinstance(data, ContextData):
            root = data
        else:
            # Shallow conversion strictly for the top level.
            # Trusts user input for deep structure.
            root = ContextData(data)
        object.__setattr__(self, "_root", root)

    @classmethod
    def _from_context_data(cls, root: ContextData) -> "Context":
        obj = object.__new__(cls)
        object.__setattr__(obj, "_root", root)
        return obj

    # --- Read Operations ---
    @overload
    def get(self, ref: Ref[_T]) -> _T: ...
    @overload
    def get(self, ref: Ref[_T], default: _T2) -> Union[_T, _T2]: ...
    def get(
        self, ref: Ref[_T], default: Union[_T2, _Missing] = _MISSING
    ) -> Union[_T, _T2]:
        try:
            val = self._resolve(ref.parts)
            if isinstance(val, ContextData):
                return ContextView(self, ref.parts)
            return ref.resolve(val)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: Ref[_T]) -> bool:
        try:
            self._resolve(ref.parts)
            return True
        except ContextPathError:
            return False

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        if ref is None:
            return self._root.keys()
        element = self._resolve(ref.parts)
        if isinstance(element, ContextData):
            return element.keys()
        raise ContextPathError("Cannot list keys of a leaf value.")

    def to_context_data(self, ref: Optional[Ref[_T]] = None) -> ContextData:
        if ref is None:
            return self._root
        val = self._resolve(ref.parts)
        if isinstance(val, ContextData):
            return val
        raise ContextPathError("Target is not a ContextData (container).")

    def _resolve(self, parts: Iterable[str]) -> Any:
        current: Any = self._root
        for p in parts:
            if not isinstance(current, ContextData):
                raise ContextPathError(f"Path blocked by leaf value.")
            try:
                current = current[p]
            except KeyError:
                raise ContextPathError(p) from None
        return current

    # --- Unified Modification Interface ---
    def mutate(
        self,
        *,
        updates: Optional[Mapping[Ref, Any]] = None,
        drops: Optional[Iterable[Ref]] = None,
    ) -> "Context":
        """
        Apply a transaction-like set of modifications (updates and drops) atomically.

        Args:
            updates: A mapping of References to new values.
            drops: An iterable of References to remove.

        Returns:
            A new Context instance with the changes applied.
        """
        if not updates and not drops:
            return self
        raw_updates = {r.parts: v for r, v in updates.items()} if updates else {}
        raw_drops = {r.parts for r in drops} if drops else set()
        # Delegate to the root ContextData
        new_root = self._root.mutate(raw_updates, raw_drops)
        # Edge Case: If the root itself resulted in MISSING (dropped), we reset to empty.
        if new_root is _MISSING:
            new_root = ContextData()
        return self._from_context_data(new_root)

    # --- Convenience Interfaces ---
    def update(self, updates: Mapping[Ref, Any]) -> "Context":
        """Batch update convenience interface."""
        return self.mutate(updates=updates)

    def drop(self, refs: Iterable[Ref]) -> "Context":
        """Batch delete convenience interface."""
        return self.mutate(drops=refs)

    def set(self, ref: Ref[_T], value: _T) -> "Context":
        """Single set convenience interface."""
        return self.mutate(updates={ref: value})

    def clear(self, ref: Ref[_T]) -> "Context":
        """
        Clear all contents under a reference but keep the path.
        Raises ContextPathError if the target is not a container (ContextData).
        """
        # 1. Validate target is a container
        val = self._resolve(ref.parts)
        if not isinstance(val, ContextData):
            raise ContextPathError(
                f"Cannot clear '{ref.path}': not a container (ContextData)."
            )

        # 2. Update with empty ContextData
        return self.mutate(updates={ref: ContextData()})

    def delete(self, ref: Ref[_T]) -> "Context":
        """Single delete convenience interface."""
        return self.mutate(drops=[ref])


@dataclass(frozen=True, repr=False)
class ContextView(ContextElement):
    """
    Read-only view of a subtree within a Context.
    """

    _context: Context
    _parts: tuple[str, ...]

    def _adjust_ref(self, ref: Optional[Ref[_T]]) -> Ref[_T]:
        if ref is None:
            path = ".".join(self._parts)
            return Ref(path)
        new_parts = self._parts + ref.parts
        new_path = ".".join(new_parts)
        return type(ref)(new_path, lens=ref.lens, metadata=ref.metadata)

    def get(
        self, ref: Ref[_T], default: Union[_T2, _Missing] = _MISSING
    ) -> Union[_T, _T2]:
        return self._context.get(self._adjust_ref(ref), default)

    def exists(self, ref: Ref[_T]) -> bool:
        return self._context.exists(self._adjust_ref(ref))

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        return self._context.keys(self._adjust_ref(ref))

    def to_context_data(self, ref: Optional[Ref[_T]] = None) -> ContextData:
        return self._context.to_context_data(self._adjust_ref(ref))


# --- PyTreeEngine Configuration ---
CONTEXT_PYTREE_ENGINE = PyTreeEngine("context_engine", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(CONTEXT_PYTREE_ENGINE, key="context_engine")


def _flatten_context_data(data: ContextData) -> tuple[Iterable[Any], PyTreeAux]:
    """Flatten ContextData."""
    keys = tuple(data.keys())
    rich_keys = tuple(MappingKey(k) for k in keys)
    children = (data[k] for k in keys)
    return children, PyTreeAux(keys=rich_keys)


def _unflatten_context_data(children: Iterable[Any], aux: PyTreeAux) -> ContextData:
    """Unflatten to ContextData."""
    if aux.keys is None:
        raise ValueError("Missing keys for ContextData unflattening.")
    raw_keys = [k.key for k in cast("Iterable[MappingKey]", aux.keys)]
    return ContextData(zip(raw_keys, children))


def _flatten_context(context: Context) -> tuple[Iterable[Any], PyTreeAux]:
    """Flatten Context -> (root_context_data, )."""
    return (context._root,), PyTreeAux()


def _unflatten_context(children: Iterable[Any], aux: PyTreeAux) -> Context:
    """Unflatten Context."""
    (root,) = children
    return Context._from_context_data(root)


# Register
CONTEXT_PYTREE_ENGINE.register(
    ContextData, _flatten_context_data, _unflatten_context_data
)
CONTEXT_PYTREE_ENGINE.register(Context, _flatten_context, _unflatten_context)
