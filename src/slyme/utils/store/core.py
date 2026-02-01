import types
from enum import Enum
from dataclasses import dataclass
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from typing import (
    Any,
    Generic,
    TypeVar,
    Union,
    Literal,
    cast,
    Optional,
)
from typing_extensions import Self
from slyme.utils.pytree import (
    PyTreeEngine,
    PyTreeAux,
    MappingKey,
    PYTREE_ENGINE_REGISTRY,
    KeyPath,
)
from .hook import StoreHook

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_EMPTY_METADATA = types.MappingProxyType({})
Missing = Enum("Missing", ["MARK"])
MISSING = Missing.MARK


class StoreConfig:
    repr_indent: str = "    "
    repr_newline: str = "\n"
    repr_suffix: str = ","
    repr_last_suffix: str = ","

    @classmethod
    def set_compact_repr(cls):
        cls.repr_indent = ""
        cls.repr_newline = ""
        cls.repr_suffix = ", "
        cls.repr_last_suffix = ""

    @classmethod
    def set_pretty_repr(cls):
        cls.repr_indent = "    "
        cls.repr_newline = "\n"
        cls.repr_suffix = ","
        cls.repr_last_suffix = ","


class Ref(Generic[_T]):
    """
    Immutable dotted ref with cached hash and split parts.
    
    Supports empty path ("") to represent the root/self.
    """

    @property
    def path(self) -> str:
        return self.__dict__["path"]

    @property
    def parts(self) -> tuple[str, ...]:
        return self.__dict__["parts"]

    @property
    def lens(self) -> KeyPath:
        return self.__dict__["lens"]

    @property
    def hash(self) -> int:
        return self.__dict__["hash"]

    @property
    def metadata(self) -> Mapping[str, Any]:
        return self.__dict__["metadata"]

    def __init__(
        self,
        path: str,
        /,
        *,
        lens: KeyPath = (),
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> None:
        if path == "":
            parts = ()
        else:
            parts = tuple(path.split("."))
            if any(not p for p in parts):
                raise ValueError(f"Invalid ref path: {path!r}")

        self.__dict__["path"] = path
        self.__dict__["parts"] = parts
        self.__dict__["lens"] = lens
        self.__dict__["hash"] = hash((parts, lens))
        self.__dict__["metadata"] = (
            types.MappingProxyType(metadata)
            if metadata is not None
            else _EMPTY_METADATA
        )

    def resolve(self, pytree) -> _T:
        return PyTreeEngine.get_element(pytree, self.lens)

    def update_metadata(self, metadata: Mapping[str, Any]) -> Self:
        """Returns a new Ref with updated metadata (merging with existing)."""
        new_metadata = dict(self.metadata)
        new_metadata.update(metadata)
        return type(self)(self.path, lens=self.lens, metadata=new_metadata)

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
        return f"path={self.path!r}, lens_expr={PyTreeEngine.codify_path(self.lens)}, metadata={self.metadata!r}"


class StorePathError(KeyError):
    """Internal exception raised when a path cannot be resolved in the store."""
    pass


@dataclass(frozen=True)
class _DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class StoreElement:
    """
    Base class for all store elements.
    
    It defines a minimal, explicit interface for data access.
    Magic methods (dict mixins) are intentionally avoided to ensure forward
    compatibility with future Async/IO-bound implementations.
    """

    # --- Core Interface ---

    def get(
        self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING
    ) -> Union[_T, _T2]:
        """Get value or view at ref."""
        raise NotImplementedError

    def set(self, ref: Ref[_T], value: _T) -> None:
        """Set value at ref."""
        raise NotImplementedError

    def delete(self, ref: Ref[_T]) -> None:
        """Delete value at ref."""
        raise NotImplementedError

    def exists(self, ref: Ref[_T]) -> bool:
        """Check if ref exists."""
        raise NotImplementedError

    def is_leaf(self, ref: Ref[_T]) -> bool:
        """Check if ref points to a user data leaf (not a container/view)."""
        raise NotImplementedError

    def iter_keys(self, ref: Ref) -> Iterator[str]:
        """
        Iterate over the keys of the children at the given ref.
        If ref is root (empty), iterate over own children.
        """
        raise NotImplementedError
    
    @classmethod
    def from_children(cls, children: dict[str, Any]) -> Self:
        """
        Factory method to reconstruct the element from a dictionary of children.
        Used by PyTreeEngine for snapshot restoration.
        """
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:
        """
        Recursive conversion to dict using PyTreeEngine.
        This effectively snapshots the store content.
        """
        leaves = {}
        for path, value in STORE_PYTREE_ENGINE.iter_with_path(self):
            dot_path = ".".join(cast("MappingKey", k).key for k in path)
            leaves[dot_path] = value
        return leaves

    def diff(self, other: "StoreElement", strategy: Literal["is", "eq"] = "is") -> _DiffResult:
        """Compares this StoreElement with another using PyTreeEngine.

        Args:
            other: The other StoreElement instance to compare against.
            strategy: The comparison strategy for leaf values.
                - "is": Identity comparison (`is`).
                - "eq": Equality comparison (`==`).
        """
        if strategy not in ("is", "eq"):
            raise ValueError(f"Unknown diff strategy: {strategy!r}")

        leaves_self = self.to_dict()
        leaves_other = other.to_dict()

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

    def __repr__(self) -> str:
        """Generic tree representation using iter_keys and get."""
        try:
            # Manually fetch immediate children for display
            keys = list(self.iter_keys(Ref("")))
            children = {k: self.get(Ref(k)) for k in keys}
        except Exception:
            return f"{type(self).__name__}(<repr failed>)"

        name = type(self).__name__
        if not children:
            return f"{name}()"

        lines = [f"{name}({StoreConfig.repr_newline}"]
        count = len(children)
        for i, (key, value) in enumerate(children.items()):
            value_lines = repr(value).splitlines(keepends=True)
            head = value_lines[0] if value_lines else repr("")
            lines.append(f"{StoreConfig.repr_indent}{key}={head}")
            for line in value_lines[1:]:
                lines.append(f"{StoreConfig.repr_indent}{line}")

            suffix = StoreConfig.repr_last_suffix if i == count - 1 else StoreConfig.repr_suffix
            lines.append(f"{suffix}{StoreConfig.repr_newline}")
        lines.append(")")
        return "".join(lines)


class StoreBackend(StoreElement):
    """Base class for storage backends."""
    pass


class MemoryBackend(StoreBackend):
    """Backend implementation using a standard dictionary."""

    def __init__(self, data: Optional[dict[str, Any]] = None):
        self._data = data if data is not None else {}

    def _resolve_container(self, parts: tuple[str, ...], create: bool = False) -> Any:
        curr = self._data
        for i, part in enumerate(parts):
            if part not in curr:
                if create:
                    curr[part] = {}
                else:
                    raise StorePathError(part)
            curr = curr[part]
            # Ensure we don't traverse into a leaf value as if it were a container
            if not isinstance(curr, dict) and i < len(parts) - 1:
                 raise StorePathError(f"Segment {part} is a leaf, cannot traverse into.")
        return curr

    def get(self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING) -> Union[_T, _T2]:
        try:
            if not ref.parts:
                return self # type: ignore
            
            parent = self._resolve_container(ref.parts[:-1])
            key = ref.parts[-1]
            
            if key not in parent:
                raise StorePathError(key)
            return parent[key]
        except StorePathError:
            if default is MISSING:
                raise
            return default

    def set(self, ref: Ref[_T], value: _T) -> None:
        if not ref.parts:
            if isinstance(value, MemoryBackend):
                self._data = value._data
                return
            elif isinstance(value, dict):
                self._data = value
                return
            raise ValueError("Cannot set root of backend to a non-dict value directly.")

        parent = self._resolve_container(ref.parts[:-1], create=True)
        parent[ref.parts[-1]] = value

    def delete(self, ref: Ref[_T]) -> None:
        if not ref.parts:
            self._data.clear()
            return
        parent = self._resolve_container(ref.parts[:-1])
        key = ref.parts[-1]
        if key not in parent:
             raise StorePathError(key)
        del parent[key]

    def exists(self, ref: Ref[_T]) -> bool:
        try:
            self.get(ref)
            return True
        except StorePathError:
            return False

    def is_leaf(self, ref: Ref[_T]) -> bool:
        try:
            val = self.get(ref)
            return not isinstance(val, dict)
        except StorePathError:
            return False

    def iter_keys(self, ref: Ref) -> Iterator[str]:
        try:
            val = self.get(ref)
            if isinstance(val, dict):
                return iter(val.keys())
            if isinstance(val, MemoryBackend):
                return iter(val._data.keys())
            return iter([]) 
        except StorePathError:
            return iter([])

    @classmethod
    def from_children(cls, children: dict[str, Any]) -> Self:
        return cls(children)


class StoreView(StoreElement):
    """
    A lightweight proxy view into the Store.
    Delegates all operations to the root Store to ensure Hooks are triggered.
    """

    def __init__(self, root: "Store", path: str):
        self._root = root
        self._path = path

    def _make_abs_ref(self, rel_ref: Ref) -> Ref:
        prefix = self._path
        if not rel_ref.path:
            return Ref(prefix, lens=rel_ref.lens, metadata=rel_ref.metadata)
        abs_path = f"{prefix}.{rel_ref.path}" if prefix else rel_ref.path
        return Ref(abs_path, lens=rel_ref.lens, metadata=rel_ref.metadata)

    def get(self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING) -> Union[_T, _T2]:
        return self._root.get(self._make_abs_ref(ref), default)

    def set(self, ref: Ref[_T], value: _T) -> None:
        self._root.set(self._make_abs_ref(ref), value)

    def delete(self, ref: Ref[_T]) -> None:
        self._root.delete(self._make_abs_ref(ref))

    def exists(self, ref: Ref[_T]) -> bool:
        return self._root.exists(self._make_abs_ref(ref))

    def is_leaf(self, ref: Ref[_T]) -> bool:
        return self._root.is_leaf(self._make_abs_ref(ref))

    def iter_keys(self, ref: Ref) -> Iterator[str]:
        return self._root.iter_keys(self._make_abs_ref(ref))

    @classmethod
    def from_children(cls, children: dict[str, Any]) -> StoreElement:
        """
        Materializes the View into a MemoryBackend when unflattened.
        """
        return MemoryBackend(children)


class Store(StoreElement):
    """
    The root Store object.
    Manages backends routing and the global hook.
    """
    
    def __init__(self, hook: Optional[StoreHook] = None):
        self._backends: dict[str, StoreBackend] = {}
        self.hook: Optional[StoreHook] = hook

    def mount(self, prefix: str, backend: StoreBackend) -> None:
        if "." in prefix or not prefix:
            raise ValueError("Backends must be mounted at the top level (e.g. 'mnt').")
        self._backends[prefix] = backend

    def _get_backend(self, part: str) -> tuple[StoreBackend, bool]:
        if part in self._backends:
            return self._backends[part], True
        # Lazy creation of MemoryBackend
        backend = MemoryBackend()
        self._backends[part] = backend
        return backend, True

    def get(self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING) -> Union[_T, _T2]:
        try:
            if not ref.parts:
                return self # type: ignore

            root_key = ref.parts[0]
            if root_key not in self._backends:
                 raise StorePathError(root_key)
            
            backend = self._backends[root_key]
            backend_path = ".".join(ref.parts[1:])
            
            if not backend_path:
                value = backend
                is_structure = True 
            else:
                backend_ref = Ref(backend_path)
                value = backend.get(backend_ref)
                is_structure = not backend.is_leaf(backend_ref)

            if self.hook is not None:
                self.hook.on_getitem(self, ref, value)

            if is_structure:
                return cast(_T, StoreView(self, ref.path))
            
            return value

        except StorePathError:
            if default is MISSING:
                raise
            return default

    def set(self, ref: Ref[_T], value: _T) -> None:
        if not ref.parts:
             raise ValueError("Cannot overwrite the entire Store root.")
        
        root_key = ref.parts[0]
        backend, _ = self._get_backend(root_key)
        backend_path = ".".join(ref.parts[1:])
        
        # NOTE: Skipping old_value fetch for now to avoid overhead
        old_value = MISSING 
        
        if not backend_path:
             raise ValueError(f"Cannot overwrite the root of backend '{root_key}'.")
        
        backend.set(Ref(backend_path), value)
        
        if self.hook is not None:
            self.hook.on_setitem(self, ref, old_value, value)

    def delete(self, ref: Ref[_T]) -> None:
        if not ref.parts:
            raise ValueError("Cannot delete Store root.")
        
        root_key = ref.parts[0]
        if root_key not in self._backends:
            raise StorePathError(root_key)
            
        backend = self._backends[root_key]
        backend_path = ".".join(ref.parts[1:])
        
        if not backend_path:
            del self._backends[root_key]
        else:
            backend.delete(Ref(backend_path))
            
        if self.hook is not None:
            self.hook.on_delitem(self, ref, MISSING)

    def exists(self, ref: Ref[_T]) -> bool:
        if not ref.parts: return True
        root_key = ref.parts[0]
        if root_key not in self._backends: return False
        backend = self._backends[root_key]
        backend_path = ".".join(ref.parts[1:])
        if not backend_path: return True
        return backend.exists(Ref(backend_path))

    def is_leaf(self, ref: Ref[_T]) -> bool:
        if not ref.parts: return False
        root_key = ref.parts[0]
        if root_key not in self._backends: return False
        backend = self._backends[root_key]
        backend_path = ".".join(ref.parts[1:])
        if not backend_path: return False
        return backend.is_leaf(Ref(backend_path))
    
    def iter_keys(self, ref: Ref) -> Iterator[str]:
        if not ref.parts:
            return iter(self._backends.keys())
        
        root_key = ref.parts[0]
        if root_key not in self._backends:
            return iter([])
        
        backend = self._backends[root_key]
        backend_path = ".".join(ref.parts[1:])
        return backend.iter_keys(Ref(backend_path))

    @classmethod
    def from_children(cls, children: dict[str, Any]) -> Self:
        store = cls()
        for k, v in children.items():
            if isinstance(v, StoreBackend):
                store.mount(k, v)
            elif isinstance(v, dict):
                store.mount(k, MemoryBackend(v))
        return store

    @contextmanager
    def with_hook(self, hook: StoreHook):
        prev_hook = self.hook
        self.hook = hook
        try:
            yield
        finally:
            self.hook = prev_hook


# --- PyTreeEngine Configuration ---

STORE_PYTREE_ENGINE = PyTreeEngine("store_engine", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(STORE_PYTREE_ENGINE, key="store_engine")


def _flatten_store_element(element: StoreElement) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Generic flattener using StoreElement's iter_keys/get interface.
    """
    try:
        keys = tuple(element.iter_keys(Ref("")))
    except NotImplementedError:
        keys = ()
        
    children = tuple(element.get(Ref(k)) for k in keys)
    rich_keys = tuple(MappingKey(k) for k in keys)
    return children, PyTreeAux(keys=rich_keys, cls=type(element))


def _unflatten_store_element(children: Iterable[Any], aux: PyTreeAux) -> Any:
    """
    Generic unflattener using StoreElement's factory interface.
    """
    if aux.cls is None or not issubclass(aux.cls, StoreElement):
        raise ValueError("Invalid class in PyTreeAux for StoreElement unflattening.")
    
    if aux.keys is None:
        return aux.cls.from_children({})

    keys = [cast("MappingKey", k).key for k in aux.keys]
    children_dict = dict(zip(keys, children))
    return aux.cls.from_children(children_dict)


# Register ONCE for the base class, and all subclasses inherit this logic.
STORE_PYTREE_ENGINE.register(
    StoreElement, _flatten_store_element, _unflatten_store_element
)
