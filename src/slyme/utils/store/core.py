import types
from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass, field
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
    PyTreeKey,
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


class StoreDict(dict):
    """
    Internal dictionary implementation used to distinguish structural elements
    from user-provided dictionary values.
    """

    __slots__ = ()


@dataclass(frozen=True)
class DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class StoreElement(ABC):
    """Abstract base class for store elements."""

    @classmethod
    @abstractmethod
    def _from_children(cls, keys: Iterable[str], children: Iterable[Any]) -> Any:
        """
        Internal factory to reconstruct the element from keys and children.
        Used by PyTree unflattening logic.
        """
        pass

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

    def type_repr(self) -> str:
        return type(self).__name__

    def __repr__(self) -> str:
        name = self.type_repr()
        keys = list(self.keys())
        if not keys:
            return f"{name}()"

        lines = [f"{name}({{{StoreConfig.repr_newline}"]
        count = len(keys)
        for i, key in enumerate(keys):
            # Use get to retrieve value (which might be StoreView or leaf)
            value = self.get(Ref(key))
            value_lines = repr(value).splitlines(keepends=True)
            head = (
                value_lines[0] if value_lines else repr("")
            )  # NOTE: repr(value) may return ""
            lines.append(f"{StoreConfig.repr_indent}{key!r}: {head}")
            for line in value_lines[1:]:
                lines.append(f"{StoreConfig.repr_indent}{line}")
            if i == count - 1:
                lines.append(
                    f"{StoreConfig.repr_last_suffix}{StoreConfig.repr_newline}"
                )
            else:
                lines.append(f"{StoreConfig.repr_suffix}{StoreConfig.repr_newline}")
        lines.append("})")
        return "".join(lines)

    def diff(
        self, other: "StoreElement", strategy: Literal["is", "eq"] = "is"
    ) -> DiffResult:
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

        return DiffResult(added, removed, modified)

    def collect_leaves(self) -> dict[str, Any]:
        """
        Recursively find all leaf values using STORE_PYTREE_ENGINE.
        Returns a dictionary mapping dotted paths to values.
        """
        leaves = {}
        for path, value in STORE_PYTREE_ENGINE.iter_with_path(self):
            dot_path = ".".join(cast("StoreKey", k).key for k in path)
            leaves[dot_path] = value
        return leaves


class BaseStore(StoreElement):
    """
    Base implementation of the Store logic (Storage and Structure).
    Provides the core data manipulation capabilities without the Hook mechanism.
    Use this class to represent a pure data container or a snapshot.
    """

    def __init__(
        self, data: Optional[Union[dict[str, Any], StoreElement]] = None
    ) -> None:
        self._data = StoreDict()
        if data is not None:
            if isinstance(data, StoreElement):
                # Absorb structure from StoreElement
                self._data = self._process_data(data)
            elif isinstance(data, Mapping):
                # Treat dict keys as structure, but process values recursively.
                # NOTE: _process_data will NOT convert leaf dicts to StoreDicts.
                for k, v in data.items():
                    self._data[k] = self._process_data(v)
            else:
                # Should not happen based on type hint, but safe fallback
                pass

    @classmethod
    def _from_children(cls, keys: Iterable[str], children: Iterable[Any]) -> Any:
        """
        Reconstruct the Store from keys and children.
        """
        data = dict(zip(keys, children))
        # Note: We still pass through __init__ logic
        return cls(data)

    def _process_data(self, data: Any) -> Any:
        """
        Recursively process input data to ensure internal consistency.
        Converts StoreElements to StoreDicts (merging structure), but leaves
        plain dicts as-is (treating them as leaf data).
        """
        if isinstance(data, StoreDict):
            return data
        elif isinstance(data, StoreElement):
            # Manually reconstruct structure from StoreElement to avoid to_dict()
            # erasing the distinction between Structure and Leaf Dicts.
            res = StoreDict()
            for k in data.keys():
                res[k] = self._process_data(data.get(Ref(k)))
            return res
        else:
            # Treat dict, list, int, etc. as leaf values.
            return data

    @overload
    def get(self, ref: Ref[_T]) -> _T: ...
    @overload
    def get(self, ref: Ref[_T], default: _T2) -> Union[_T, _T2]: ...
    def get(
        self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING
    ) -> Union[_T, _T2]:
        try:
            element = self._resolve_element(ref.parts)
            return ref.resolve(element)
        except StorePathError:
            if default is MISSING:
                raise
            return default

    def exists(self, ref: Ref[_T]) -> bool:
        try:
            self._resolve_element(ref.parts)
        except StorePathError:
            return False
        else:
            return True

    def set(self, ref: Ref[_T], value: _T) -> None:
        if isinstance(value, (dict, StoreElement)):
            value = self._process_data(value)

        *dirs, last = ref.parts
        element = self._touch(dirs)
        element[last] = value

    def delete(self, ref: Ref[_T]) -> None:
        *dirs, last = ref.parts
        parent = self._resolve_internal(dirs)
        del parent[last]

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        if ref is None:
            return self._data.keys()
        element = self._resolve_internal(ref.parts)
        return element.keys()

    def _resolve_element(self, parts: Iterable[str]) -> Any:
        current: Any = self._data
        path_acc = []
        for p in parts:
            if not isinstance(current, StoreDict):
                raise StorePathError(f"Path blocked by leaf value.")
            try:
                current = current[p]
                path_acc.append(p)
            except KeyError:
                raise StorePathError(p) from None

        if isinstance(current, StoreDict):
            # Pass `self` (the BaseStore) to StoreView.
            # StoreView is compatible with BaseStore as it implements StoreElement.
            return StoreView(cast("Store", self), tuple(path_acc))
        return current

    def _resolve_internal(self, parts: Iterable[str]) -> StoreDict:
        current: Any = self._data
        for p in parts:
            if not isinstance(current, StoreDict):
                raise StorePathError(f"Path blocked by leaf value.")
            try:
                current = current[p]
            except KeyError:
                raise StorePathError(p) from None
        if not isinstance(current, StoreDict):
            raise StorePathError("Path resolved to a leaf, expected internal element.")
        return current

    def _touch(self, parts: Iterable[str]) -> StoreDict:
        element: StoreDict = self._data
        for p in parts:
            nxt = element.get(p, MISSING)
            if nxt is MISSING:
                nxt = StoreDict()
                element[p] = nxt
            elif not isinstance(nxt, StoreDict):
                raise StorePathError(f"Conflict: {p!r} is already a leaf value.")
            element = nxt
        return element


class Store(BaseStore):
    """
    Standard Store implementation with Hook support.
    """

    def __init__(
        self, data: Optional[Union[dict[str, Any], StoreElement]] = None
    ) -> None:
        super().__init__(data)
        self.hook: Union[StoreHook, None] = None

    def set(self, ref: Ref[_T], value: _T) -> None:
        # Optimization: If no hook, skip overhead and call super
        if self.hook is None:
            super().set(ref, value)
            return

        # Pre-process value to ensure structure consistency
        if isinstance(value, (dict, StoreElement)):
            value = self._process_data(value)

        *dirs, last = ref.parts
        # We can't easily use super().set() because we need to inspect the 'element'
        # to trigger the hook before/after setting.
        # So we reimplement the logic but reuse internal helpers.
        element = self._touch(dirs)

        raw_old = element.get(last, MISSING)
        if isinstance(raw_old, StoreDict):
            old_value = StoreView(self, tuple(dirs) + (last,))
        else:
            old_value = raw_old

        element[last] = value
        self.hook.on_setitem(self, ref, old_value, value)

    def delete(self, ref: Ref[_T]) -> None:
        if self.hook is None:
            super().delete(ref)
            return

        *dirs, last = ref.parts
        parent = self._resolve_internal(dirs)

        raw_old = parent.get(last, MISSING)
        if raw_old is MISSING:
            raise StorePathError(last)

        if isinstance(raw_old, StoreDict):
            old_value = StoreView(self, tuple(dirs) + (last,))
        else:
            old_value = raw_old

        del parent[last]
        self.hook.on_delitem(self, ref, old_value)

    def get(
        self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING
    ) -> Union[_T, _T2]:
        # We need to capture the return value for the hook
        try:
            val = super().get(ref, default)
            # If default was returned (and implies Missing), we might skip hook?
            # Standard pattern: hook triggers on successful retrieval.
            if self.hook is not None:
                self.hook.on_getitem(self, ref, val)
            return val
        except StorePathError:
            raise

    @contextmanager
    def with_hook(self, hook: StoreHook):
        prev_hook = self.hook
        self.hook = hook
        try:
            yield
        finally:
            self.hook = prev_hook


class StoreView(StoreElement):
    """
    A proxy view for a subtree within a Store.
    """

    def __init__(self, store: Store, parts: tuple[str, ...]):
        self._store = store
        self._parts = parts

    @classmethod
    def _from_children(cls, keys: Iterable[str], children: Iterable[Any]) -> Any:
        """
        Reconstructs as a BaseStore snapshot, detaching from the original StoreContext.
        """
        return BaseStore(dict(zip(keys, children)))

    def _adjust_ref(self, ref: Optional[Ref[_T]]) -> Ref[_T]:
        if ref is None:
            path = ".".join(self._parts)
            return Ref(path)
        new_parts = self._parts + ref.parts
        new_path = ".".join(new_parts)
        return type(ref)(new_path, lens=ref.lens, metadata=ref.metadata)

    def get(
        self, ref: Ref[_T], default: Union[_T2, Missing] = MISSING
    ) -> Union[_T, _T2]:
        return self._store.get(self._adjust_ref(ref), default)

    def set(self, ref: Ref[_T], value: _T) -> None:
        self._store.set(self._adjust_ref(ref), value)

    def delete(self, ref: Ref[_T]) -> None:
        self._store.delete(self._adjust_ref(ref))

    def exists(self, ref: Ref[_T]) -> bool:
        return self._store.exists(self._adjust_ref(ref))

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        return self._store.keys(self._adjust_ref(ref))

    def type_repr(self) -> str:
        # Masquerade as BaseStore so repr() strings are valid BaseStore constructors.
        return BaseStore.__name__


# --- PyTreeEngine Configuration ---

STORE_PYTREE_ENGINE = PyTreeEngine("store_engine", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(STORE_PYTREE_ENGINE, key="store_engine")


@dataclass(frozen=True)
class StoreKey(PyTreeKey):
    """
    Key for Store access. Resolves using `get(Ref(key))`.
    """

    key: str

    def resolve(self, element: Any) -> Any:
        return element.get(Ref(self.key))

    def codify(self, parent_expr: str) -> str:
        return f"{parent_expr}.get(Ref({self.key!r}))"


def _flatten_store_element(element: StoreElement) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Flatten handler for StoreElement (BaseStore, Store, StoreView).
    """
    keys = tuple(element.keys())
    children = [element.get(Ref(k)) for k in keys]
    rich_keys = tuple(StoreKey(k) for k in keys)

    # Just record the type. We rely on the class's _from_children to handle
    # the nuances of reconstruction (like StoreView degrading to BaseStore).
    return children, PyTreeAux(keys=rich_keys, cls=type(element))


def _unflatten_store_element(children: Iterable[Any], aux: PyTreeAux) -> Any:
    """
    Unflatten handler to reconstruct StoreElement.
    """
    if aux.keys is None:
        raise ValueError("Missing keys in PyTreeAux for Store unflattening.")

    # Determine class to reconstruct
    cls = aux.cls if aux.cls is not None else BaseStore
    keys = [cast("StoreKey", k).key for k in aux.keys]
    return cls._from_children(keys, children)


# Register StoreElement
STORE_PYTREE_ENGINE.register(
    StoreElement, _flatten_store_element, _unflatten_store_element
)
