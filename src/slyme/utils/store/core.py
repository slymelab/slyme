import types
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


class _StoreElement:
    """Inner store element.
    NOTE: `_StoreElement` can only be modified through `Store` for consistency.
    """

    def __init__(self, /, **data: Any) -> None:
        self._data: dict[str, Any] = data

    def __repr__(self) -> str:
        name = type(self).__name__
        if not self._data:
            return f"{name}()"
        lines = [f"{name}({StoreConfig.repr_newline}"]
        count = len(self._data)
        for i, (key, value) in enumerate(self._data.items()):
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

    def copy(self) -> Self:
        return type(self)(
            **{
                k: (v.copy() if isinstance(v, _StoreElement) else v)
                for k, v in self._data.items()
            }
        )


@dataclass(frozen=True)
class _DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class Store(_StoreElement):
    """Dotted-attribute-style nested store."""

    def __init__(self, /, **data: Any) -> None:
        super().__init__(**data)
        self.hook: Union[StoreHook, None] = None

    def __getitem__(self, ref: Ref[_T]) -> _T:
        # Result can be a _StoreElement (subtree) or a raw leaf value.
        value = ref.resolve(self._resolve(ref.parts))
        if self.hook is not None:
            # Call hook
            self.hook.on_getitem(self, ref, value)
        return value

    @overload
    def get(self, ref: Ref[_T], default: None = None) -> Union[_T, None]: ...
    @overload
    def get(self, ref: Ref[_T], default: _T2) -> Union[_T, _T2]: ...
    def get(self, ref: Ref[_T], default: Optional[_T2] = None) -> Union[_T, _T2, None]:
        try:
            return self[ref]
        except _StorePathError:
            return default

    def __contains__(self, ref: Ref[_T]) -> bool:
        try:
            self._resolve(ref.parts)
        except _StorePathError:
            return False
        else:
            return True

    def _resolve(self, parts: Iterable[str]) -> Any:
        """Resolve the path parts and get the final element or value."""
        element: Any = self
        for p in parts:
            if not isinstance(element, _StoreElement):
                # If we encounter a leaf value mid-path, it's a path error
                # (blocking the traversal).
                raise _StorePathError(f"Path {parts} blocked by leaf value at {p!r}")
            try:
                element = element._data[p]
            except KeyError:
                raise _StorePathError(p) from None
        return element

    def __setitem__(self, ref: Ref[_T], value: _T) -> None:
        *dirs, last = ref.parts
        element = self._touch(dirs)

        if self.hook is not None:
            # Get the old value first
            old_value = element._data.get(last, MISSING)
            element._data[last] = value
            # Call hook
            self.hook.on_setitem(self, ref, old_value, value)
        else:
            element._data[last] = value

    def __delitem__(self, ref: Ref[_T]) -> None:
        *dirs, last = ref.parts
        parent = self._resolve(dirs)
        if not isinstance(parent, _StoreElement):
            raise _StorePathError(f"Parent path not found for {ref!r}")

        if self.hook is not None:
            # Get the old value first
            old_value = parent._data.get(last, MISSING)
            del parent._data[last]
            # Call hook
            self.hook.on_delitem(self, ref, old_value)
        else:
            del parent._data[last]

    def _touch(self, parts: Iterable[str]) -> _StoreElement:
        """Recursively resolve the store elements along the path, and create a
        new element if the element not exists."""
        element: _StoreElement = self
        for p in parts:
            nxt: Any = element._data.get(p, MISSING)
            if nxt is MISSING:
                nxt = _StoreElement()
                element._data[p] = nxt
            elif not isinstance(nxt, _StoreElement):
                # Conflict: path segment exists but is a leaf value
                raise _StorePathError(f"Conflict: {p!r} is already a leaf value.")
            element = nxt
        return element

    @contextmanager
    def with_hook(self, hook: StoreHook):
        prev_hook = self.hook
        self.hook = hook
        try:
            yield
        finally:
            self.hook = prev_hook

    def diff(self, other: "Store", strategy: Literal["is", "eq"] = "is") -> _DiffResult:
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
        # PyTreeEngine ensures we only traverse _StoreElement structures;
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
            dot_path = ".".join(cast("MappingKey", k).key for k in path)  # type: ignore
            leaves[dot_path] = value
        return leaves


# --- PyTreeEngine Configuration ---

STORE_PYTREE_ENGINE = PyTreeEngine("store_engine", register_defaults=False)
PYTREE_ENGINE_REGISTRY.register(STORE_PYTREE_ENGINE, key="store_engine")


def _flatten_store_element(element: _StoreElement) -> tuple[Iterable[Any], PyTreeAux]:
    """
    Flatten handler for _StoreElement.
    Exposes keys in PyTreeAux for path tracking and values as children.
    """
    keys = tuple(element._data.keys())
    children = tuple(element._data.values())
    # Wrap keys in MappingKey for semantic path tracking (similar to dict)
    rich_keys = tuple(MappingKey(k) for k in keys)

    return children, PyTreeAux(keys=rich_keys)


def _unflatten_store_element(children: Iterable[Any], aux: PyTreeAux) -> Any:
    """
    Unflatten handler to reconstruct _StoreElement.
    """
    cls = aux.cls if aux.cls is not None else _StoreElement

    if aux.keys is None:
        raise ValueError("Missing keys in PyTreeAux for Store unflattening.")

    # Extract keys
    keys = [cast("MappingKey", k).key for k in aux.keys]  # type: ignore

    # Reconstruct instance
    # Bypass __init__ to handle subclasses (like Store) generically if needed,
    # or just use constructor if safe. Here we mimic generic pytree reconstruction.
    element = object.__new__(cls)
    # Restore internal data
    element._data = dict(zip(keys, children))

    # Init hooks if it's a Store (default to None)
    if isinstance(element, Store):
        element.hook = None

    return element


# Register _StoreElement (and Store via inheritance)
STORE_PYTREE_ENGINE.register(
    _StoreElement, _flatten_store_element, _unflatten_store_element
)
