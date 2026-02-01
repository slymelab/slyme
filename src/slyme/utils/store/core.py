import types
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from collections.abc import Iterable, Mapping
from contextlib import contextmanager
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
    """Immutable dotted ref with cached hash and split parts."""

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
        self, path: str, /, *, lens: KeyPath = (), metadata: Optional[Mapping[str, Any]] = None
    ) -> None:
        if not path:
            raise ValueError("Empty ref path")
        parts = tuple(path.split("."))
        if any(not p for p in parts):
            raise ValueError(f"Invalid ref path: {path!r}")
        self.__dict__["path"] = path
        self.__dict__["parts"] = parts
        self.__dict__["lens"] = lens
        self.__dict__["hash"] = hash((parts, lens))  # NOTE: hash both parts and lens
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


class _StorePathError(KeyError):
    """Internal exception raised when a path cannot be resolved in the store."""

    pass


class _InternalDict(dict):
    """
    Internal dictionary implementation used to distinguish structural elements
    from user-provided dictionary values.
    """

    __slots__ = ()


@dataclass(frozen=True)
class _DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class StoreElement(ABC):
    """Abstract base class for store elements."""

    @abstractmethod
    def get(
        self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING
    ) -> Union[_T, _T2]:
        """Get a value from the store."""
        pass

    @abstractmethod
    def set(self, ref: Ref[_T], value: _T) -> None:
        """Set a value in the store."""
        pass

    @abstractmethod
    def delete(self, ref: Ref[_T]) -> None:
        """Delete a value from the store."""
        pass

    @abstractmethod
    def exists(self, ref: Ref[_T]) -> bool:
        """Check if a reference exists in the store."""
        pass

    @abstractmethod
    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        """Return the keys of the element (or sub-element via ref)."""
        pass

    def to_dict(self) -> dict[str, Any]:
        """Convert the store element to a dictionary recursively."""
        data = {}
        for key in self.keys():
            value = self.get(Ref(key))
            if isinstance(value, StoreElement):
                data[key] = value.to_dict()
            else:
                data[key] = value
        return data

    def __repr__(self) -> str:
        name = type(self).__name__
        keys = list(self.keys())
        if not keys:
            return f"{name}()"

        lines = [f"{name}({StoreConfig.repr_newline}"]
        count = len(keys)
        for i, key in enumerate(keys):
            # Use get to retrieve value (which might be StoreView or leaf)
            value = self.get(Ref(key))
            value_lines = repr(value).splitlines(keepends=True)
            head = (
                value_lines[0] if value_lines else repr("")
            )  # NOTE: repr(value) may return ""
            lines.append(f"{StoreConfig.repr_indent}{key}={head}")
            for line in value_lines[1:]:
                lines.append(f"{StoreConfig.repr_indent}{line}")
            if i == count - 1:
                lines.append(
                    f"{StoreConfig.repr_last_suffix}{StoreConfig.repr_newline}"
                )
            else:
                lines.append(f"{StoreConfig.repr_suffix}{StoreConfig.repr_newline}")
        lines.append(")")
        return "".join(lines)

    def diff(self, other: "StoreElement", strategy: Literal["is", "eq"] = "is") -> _DiffResult:
        """Compares this StoreElement with another using PyTreeEngine."""
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

        return _DiffResult(added, removed, modified)

    def collect_leaves(self) -> dict[str, Any]:
        """
        Recursively find all leaf values using STORE_PYTREE_ENGINE.
        Returns a dictionary mapping dotted paths to values.
        """
        leaves = {}
        for path, value in STORE_PYTREE_ENGINE.iter_with_path(self):
            dot_path = ".".join(cast("MappingKey", k).key for k in path)  # type: ignore
            leaves[dot_path] = value
        return leaves


class Store(StoreElement):
    """Dotted-attribute-style nested store."""

    def __init__(self, /, **data: Any) -> None:
        self._data: _InternalDict = _InternalDict(data)
        self.hook: Union[StoreHook, None] = None

    @overload
    def get(self, ref: Ref[_T]) -> _T: ...
    @overload
    def get(self, ref: Ref[_T], default: _T2) -> Union[_T, _T2]: ...
    def get(
        self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING
    ) -> Union[_T, _T2]:
        try:
            # 1. Resolve the element corresponding to ref.parts
            element = self._resolve_element(ref.parts)
            # 2. Resolve the lens within the element (usually identity for simple Refs)
            value = ref.resolve(element)

            if self.hook is not None:
                self.hook.on_getitem(self, ref, value)
            return value
        except _StorePathError:
            if default is MISSING:
                raise
            return default

    def exists(self, ref: Ref[_T]) -> bool:
        try:
            self._resolve_element(ref.parts)
        except _StorePathError:
            return False
        else:
            return True

    def set(self, ref: Ref[_T], value: _T) -> None:
        if isinstance(value, StoreElement):
            value = self._to_internal(value)

        *dirs, last = ref.parts
        element = self._touch(dirs)

        if self.hook is not None:
            # Get old value, checking if it is structure or leaf
            raw_old = element.get(last, MISSING)
            if isinstance(raw_old, _InternalDict):
                # If old value is structural, wrap in StoreView for the hook
                # to maintain "Element" abstraction
                old_value = StoreView(self, tuple(dirs) + (last,))
            else:
                old_value = raw_old

            element[last] = value
            self.hook.on_setitem(self, ref, old_value, value)
        else:
            element[last] = value

    def delete(self, ref: Ref[_T]) -> None:
        *dirs, last = ref.parts
        parent = self._resolve_internal(dirs)

        if self.hook is not None:
            raw_old = parent.get(last, MISSING)
            if raw_old is MISSING:
                raise _StorePathError(last)

            if isinstance(raw_old, _InternalDict):
                old_value = StoreView(self, tuple(dirs) + (last,))
            else:
                old_value = raw_old

            del parent[last]
            self.hook.on_delitem(self, ref, old_value)
        else:
            del parent[last]

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        if ref is None:
            return self._data.keys()
        
        # Directly resolve internal element to avoid creating intermediate StoreView
        element = self._resolve_internal(ref.parts)
        return element.keys()

    @contextmanager
    def with_hook(self, hook: StoreHook):
        prev_hook = self.hook
        self.hook = hook
        try:
            yield
        finally:
            self.hook = prev_hook

    def _resolve_element(self, parts: Iterable[str]) -> Any:
        """
        Resolve path parts.
        - If resolves to _InternalDict (nested structure), returns StoreView.
        - If resolves to leaf, returns leaf value.
        """
        current: Any = self._data
        path_acc = []

        for p in parts:
            if not isinstance(current, _InternalDict):
                raise _StorePathError(f"Path blocked by leaf value.")
            try:
                current = current[p]
                path_acc.append(p)
            except KeyError:
                raise _StorePathError(p) from None

        if isinstance(current, _InternalDict):
            return StoreView(self, tuple(path_acc))
        return current

    def _resolve_internal(self, parts: Iterable[str]) -> _InternalDict:
        """Resolve path strictly expecting internal dicts."""
        current: Any = self._data
        for p in parts:
            if not isinstance(current, _InternalDict):
                 raise _StorePathError(f"Path blocked by leaf value.")
            try:
                current = current[p]
            except KeyError:
                raise _StorePathError(p) from None

        if not isinstance(current, _InternalDict):
             # Should be covered by loop check, but for end result:
             raise _StorePathError("Path resolved to a leaf, expected internal element.")
        return current

    def _touch(self, parts: Iterable[str]) -> _InternalDict:
        element: _InternalDict = self._data
        for p in parts:
            nxt = element.get(p, MISSING)
            if nxt is MISSING:
                nxt = _InternalDict()
                element[p] = nxt
            elif not isinstance(nxt, _InternalDict):
                raise _StorePathError(f"Conflict: {p!r} is already a leaf value.")
            element = nxt
        return element

    def _to_internal(self, element: StoreElement) -> Any:
        """Helper to convert StoreElement back to _InternalDict structure."""
        if isinstance(element, StoreView):
            # Optimization: directly copy the internal dict from the view's source
            # We access the internal dict via resolution to ensure freshness
            return self._deep_copy_internal(element._store._resolve_internal(element._parts))
        elif isinstance(element, Store):
            return self._deep_copy_internal(element._data)
        else:
            # Fallback for generic StoreElement
            return self._dict_to_internal(element.to_dict())

    def _deep_copy_internal(self, obj: Any) -> Any:
        if isinstance(obj, _InternalDict):
            return _InternalDict({k: self._deep_copy_internal(v) for k, v in obj.items()})
        return obj

    def _dict_to_internal(self, d: dict) -> _InternalDict:
        res = _InternalDict()
        for k, v in d.items():
            if isinstance(v, dict):
                res[k] = self._dict_to_internal(v)
            else:
                res[k] = v
        return res


class StoreView(StoreElement):
    """
    A proxy view for a subtree within a Store.
    Prevents hook escape by delegating operations back to the root Store.
    """

    def __init__(self, store: Store, parts: tuple[str, ...]):
        self._store = store
        self._parts = parts

    def _adjust_ref(self, ref: Optional[Ref[_T]]) -> Ref[_T]:
        if ref is None:
            # Point to self
            path = ".".join(self._parts)
            return Ref(path)
            
        new_parts = self._parts + ref.parts
        new_path = ".".join(new_parts)
        return type(ref)(new_path, lens=ref.lens, metadata=ref.metadata)

    def get(self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING) -> Union[_T, _T2]:
        return self._store.get(self._adjust_ref(ref), default)

    def set(self, ref: Ref[_T], value: _T) -> None:
        self._store.set(self._adjust_ref(ref), value)

    def delete(self, ref: Ref[_T]) -> None:
        self._store.delete(self._adjust_ref(ref))

    def exists(self, ref: Ref[_T]) -> bool:
        return self._store.exists(self._adjust_ref(ref))

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        # Delegate to store using adjusted ref.
        # This avoids StoreView needing to fetch internal dict itself.
        return self._store.keys(self._adjust_ref(ref))


# --- PyTreeEngine Configuration ---

STORE_PYTREE_ENGINE = PyTreeEngine("store_engine", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(STORE_PYTREE_ENGINE, key="store_engine")


def _flatten_store_element(element: StoreElement) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Flatten handler for StoreElement (Store and StoreView).
    """
    keys = tuple(element.keys())
    # Retrieve children using public API to ensure StoreViews are created for nested structures
    children = [element.get(Ref(k)) for k in keys]
    rich_keys = tuple(MappingKey(k) for k in keys)

    return children, PyTreeAux(keys=rich_keys)


def _unflatten_store_element(children: Iterable[Any], aux: PyTreeAux) -> Any:
    """
    Unflatten handler to reconstruct StoreElement.
    Always reconstructs as a root Store.
    """
    if aux.keys is None:
        raise ValueError("Missing keys in PyTreeAux for Store unflattening.")

    keys = [cast("MappingKey", k).key for k in aux.keys]

    s = Store()
    for k, child in zip(keys, children):
        s.set(Ref(k), child)

    return s


# Register StoreElement (covers Store and StoreView)
STORE_PYTREE_ENGINE.register(
    StoreElement, _flatten_store_element, _unflatten_store_element
)
