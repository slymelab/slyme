from dataclasses import dataclass
from collections.abc import Iterable
from contextlib import contextmanager
from typing import (
    Any,
    Generic,
    TypeVar,
    Union,
    Literal,
    cast,
)
from typing_extensions import Self
from slyme.utils.constant import MISSING
from slyme.utils.pytree import PyTreeEngine, PyTreeAux, MappingKey, PYTREE_ENGINE_REGISTRY
from .hook import StoreHook

_T = TypeVar("_T")


class Ref(Generic[_T]):
    """Immutable dotted key with cached hash and split parts."""

    @property
    def path(self) -> str:
        return self._path

    @property
    def parts(self) -> tuple[str, ...]:
        return self._parts

    @property
    def hash(self) -> int:
        return self._hash

    def __init__(self, path: str) -> None:
        if not path:
            raise ValueError("Empty key path")
        parts = tuple(path.split("."))
        if any(not p for p in parts):
            raise ValueError(f"Invalid key path: {path!r}")
        self._path = path
        self._parts = parts
        self._hash = hash(parts)

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Ref) and self.parts == other.parts

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.extra_repr()})"

    def extra_repr(self) -> str:
        return f"path={self.path!r}"


class _StoreEntry:
    """Inner store entry.
    NOTE: `_StoreEntry` can only be modified through `Store` for consistency.
    """

    @property
    def value(self) -> Self:
        return self

    def __init__(
        self, data: Union[dict[str, Any], None] = None
    ) -> None:
        self._data: dict[str, Any] = (
            data if data is not None else {}
        )

    def __getitem__(self, key: Union[Ref[_T], str]) -> _T:
        if isinstance(key, str):
            key = Ref(key)
        # Result can be a _StoreEntry (subtree) or a raw leaf value.
        result: Any = self._resolve(key.parts)
        return result

    def _resolve(self, parts: Iterable[str]) -> Any:
        """Resolve the path parts and get the final entry or value."""
        entry: Any = self
        for p in parts:
            if not isinstance(entry, _StoreEntry):
                # If we encounter a leaf value mid-path, it's a path error
                # (blocking the traversal).
                raise KeyError(f"Path {parts} blocked by leaf value at {p!r}")
            entry = entry._data[p]
        return entry

    def __repr__(self) -> str:
        sep = ", "
        data_str = sep.join([f"{k}={v!r}" for k, v in self._data.items()])
        return f"{type(self).__name__}({data_str})"

    def copy(self) -> Self:
        return type(self)(
            data={
                k: (v.copy() if isinstance(v, _StoreEntry) else v)
                for k, v in self._data.items()
            }
        )


@dataclass(frozen=True)
class _DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class Store(_StoreEntry):
    """Dotted-attribute-style nested store."""

    def __init__(
        self,
        hook: Union[StoreHook, None] = None,
        data: Union[dict[str, Any], None] = None,
    ) -> None:
        super().__init__(data=data)
        self.hook = hook

    def __getitem__(self, key: Union[Ref[_T], str]) -> _T:
        value = super().__getitem__(key)
        if self.hook is not None:
            # Call hook
            self.hook.on_getitem(self, key, value)
        return value

    def __setitem__(self, key: Union[Ref[_T], str], value: _T) -> None:
        if isinstance(key, str):
            key = Ref(key)
        *dirs, last = key.parts
        entry = self._touch(dirs)
        
        if self.hook is not None:
            # Get the old value first
            old_value = entry._data.get(last, MISSING)
            entry._data[last] = value
            # Call hook
            self.hook.on_setitem(self, key, old_value, value)
        else:
            entry._data[last] = value

    def __delitem__(self, key: Union[Ref[_T], str]) -> None:
        if isinstance(key, str):
            key = Ref(key)
        *dirs, last = key.parts
        parent = self._resolve(dirs)
        if not isinstance(parent, _StoreEntry):
            raise KeyError(f"Parent path not found for {key!r}")
        
        if self.hook is not None:
            # Get the old value first
            old_value = parent._data.get(last, MISSING)
            del parent._data[last]
            # Call hook
            self.hook.on_delitem(self, key, old_value)
        else:
            del parent._data[last]

    def _touch(self, parts: Iterable[str]) -> _StoreEntry:
        """Recursively resolve the store entries along the path, and create a
        new entry if the entry not exists."""
        entry: _StoreEntry = self
        for p in parts:
            nxt: Any = entry._data.get(p)
            if nxt is None:
                nxt = _StoreEntry()
                entry._data[p] = nxt
            elif not isinstance(nxt, _StoreEntry):
                # Conflict: path segment exists but is a leaf value
                raise KeyError(f"Conflict: {p!r} is already a leaf value.")
            entry = nxt
        return entry

    @contextmanager
    def with_hook(self, hook: StoreHook):
        prev_hook = self.hook
        self.hook = hook
        try:
            yield
        finally:
            self.hook = prev_hook

    def diff(
        self, other: "Store", strategy: Literal["is", "eq"] = "is"
    ) -> _DiffResult:
        """Compares this Store with another using PyTreeEngine.

        Args:
            other: The other Store instance to compare against.
            strategy: The comparison strategy for leaf values.
                - "is": Identity comparison (`is`).
                - "eq": Equality comparison (`==`).
        """
        if strategy not in ("is", "eq"):
            raise ValueError(f"Unknown diff strategy: {strategy!r}")

        # Flatten both stores into {dot_path: value}
        # PyTreeEngine ensures we only traverse _StoreEntry structures; 
        # anything else is treated as a leaf.
        leaves_self = self.collect_leaves()
        leaves_other = other.collect_leaves()

        added: dict[str, Any] = {}
        removed: dict[str, Any] = {}
        modified: dict[str, tuple[Any, Any]] = {}

        keys_self = set(leaves_self.keys())
        keys_other = set(leaves_other.keys())

        # 1. Removed: In self but not in other
        for k in keys_self - keys_other:
            removed[k] = leaves_self[k]

        # 2. Added: In other but not in self
        for k in keys_other - keys_self:
            added[k] = leaves_other[k]

        # 3. Modified: In both, check values
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

        return _DiffResult(added, removed, modified)

    def collect_leaves(self) -> dict[str, Any]:
        """
        Recursively find all leaf values using STORE_PYTREE_ENGINE.
        Returns a dictionary mapping dotted paths to values.
        """
        leaves = {}
        # PyTreeEngine yields (KeyPath, leaf_value) tuples.
        for path, value in STORE_PYTREE_ENGINE.iter_with_path(self):
            # Convert KeyPath (tuple of MappingKey) to dotted string
            dot_path = ".".join(cast("MappingKey", k).key for k in path) # type: ignore
            leaves[dot_path] = value
        return leaves


# --- PyTreeEngine Configuration ---

STORE_PYTREE_ENGINE = PyTreeEngine("store_engine", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(STORE_PYTREE_ENGINE, key="store_engine")


def _flatten_store_entry(entry: _StoreEntry) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Flatten handler for _StoreEntry.
    Exposes keys in PyTreeAux for path tracking and values as children.
    """
    keys = tuple(entry._data.keys())
    children = tuple(entry._data.values())
    # Wrap keys in MappingKey for semantic path tracking (similar to dict)
    rich_keys = tuple(MappingKey(k) for k in keys)
    
    return children, PyTreeAux(keys=rich_keys)


def _unflatten_store_entry(children: Iterable[Any], aux: PyTreeAux) -> Any:
    """
    Unflatten handler to reconstruct _StoreEntry.
    """
    cls = aux.cls if aux.cls is not None else _StoreEntry
    
    if aux.keys is None:
         raise ValueError("Missing keys in PyTreeAux for Store unflattening.")
    
    # Extract keys
    keys = [cast("MappingKey", k).key for k in aux.keys] # type: ignore
    
    # Reconstruct instance
    # Bypass __init__ to handle subclasses (like Store) generically if needed,
    # or just use constructor if safe. Here we mimic generic pytree reconstruction.
    obj = object.__new__(cls)
    # Restore internal data
    obj._data = dict(zip(keys, children))
    
    # Init hooks if it's a Store (default to None)
    if isinstance(obj, Store):
        obj.hook = None
        
    return obj


# Register _StoreEntry (and Store via inheritance)
STORE_PYTREE_ENGINE.register(
    _StoreEntry, 
    _flatten_store_entry, 
    _unflatten_store_entry
)
