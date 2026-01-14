from dataclasses import dataclass
from collections import deque
from collections.abc import Iterable
from contextlib import contextmanager
from typing import (
    Any,
    Generic,
    TypeVar,
    Union,
    Literal,
)
from typing_extensions import Self
from slyme.utils.constant import MISSING
from .hook import StoreHook

_T = TypeVar("_T")


class StoreKey(Generic[_T]):
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
        return type(self) is type(other) and self.parts == other.parts

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.path!r})"


class _Ref(Generic[_T]):
    """Reference node to distinguish any value."""

    @property
    def value(self) -> _T:
        return self._value

    def __init__(self, value: _T):
        super().__init__()
        self._value = value

    def __repr__(self) -> str:
        return f"{type(self).__name__}(value={self.value!r})"


class _StoreContainer:
    """Inner store node.
    NOTE: `_StoreContainer` can only be modified through `Store` for consistency.
    """

    @property
    def value(self) -> Self:
        return self

    def __init__(
        self, data: Union[dict[str, Union["_StoreContainer", _Ref]], None] = None
    ) -> None:
        self._data: dict[str, Union[_StoreContainer, _Ref]] = (
            data if data is not None else {}
        )

    def __getitem__(self, key: Union[StoreKey[_T], str]) -> _T:
        if isinstance(key, str):
            key = StoreKey(key)
        return self._getitem(key)

    def _getitem(self, key: StoreKey[_T]) -> _T:
        # NOTE: Annotate to `Any` to pass the type checker.
        result: Any = self._resolve(key.parts).value
        return result

    def _resolve(self, parts: Iterable[str]) -> Union["_StoreContainer", _Ref]:
        """Resolve the path parts and get the final node."""
        node: Union[_StoreContainer, _Ref] = self
        for p in parts:
            if not isinstance(node, _StoreContainer):
                raise KeyError(f"Path {parts} not found")
            node = node._data[p]
        return node

    def __repr__(self) -> str:
        sep = ", "
        data_str = sep.join([f"{k}={v.value!r}" for k, v in self._data.items()])
        return f"{type(self).__name__}({data_str})"

    def copy(self) -> Self:
        return type(self)(
            data={
                k: (v.copy() if isinstance(v, _StoreContainer) else v)
                for k, v in self._data.items()
            }
        )


@dataclass(frozen=True)
class _DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class Store(_StoreContainer):
    """Dotted-attribute-style nested store."""

    def __init__(
        self,
        hook: Union[StoreHook, None] = None,
        data: Union[dict[str, Union[_StoreContainer, _Ref]], None] = None,
    ) -> None:
        super().__init__(data=data)
        self.hook = hook

    def __getitem__(self, key: Union[StoreKey[_T], str]) -> _T:
        if isinstance(key, str):
            key = StoreKey(key)
        value = self._getitem(key)
        if self.hook is not None:
            # Call hook
            self.hook.getitem(self, key, value)
        return value

    def __setitem__(self, key: Union[StoreKey[_T], str], value: _T) -> None:
        if isinstance(key, str):
            key = StoreKey(key)
        *dirs, last = key.parts
        node = self._touch(dirs)
        if self.hook is not None:
            # Get the old value first
            old_value = (
                old.value
                if (old := node._data.get(last, None)) is not None
                else MISSING
            )
            node._data[last] = _Ref(value)
            # Call hook
            self.hook.setitem(self, key, old_value, value)
        else:
            # Directly set
            node._data[last] = _Ref(value)

    def __delitem__(self, key: Union[StoreKey[_T], str]) -> None:
        if isinstance(key, str):
            key = StoreKey(key)
        *dirs, last = key.parts
        parent = self._resolve(dirs)
        if not isinstance(parent, _StoreContainer):
            raise KeyError(f"Parent path not found for {key!r}")
        if self.hook is not None:
            # Get the old value first
            old_value = (
                old.value
                if (old := parent._data.get(last, None)) is not None
                else MISSING
            )
            del parent._data[last]
            # Call hook
            self.hook.delitem(self, key, old_value)
        else:
            # Directly delete
            del parent._data[last]

    def _touch(self, parts: Iterable[str]) -> _StoreContainer:
        """Recursively resolve the store nodes along the path, and create a
        new node if the node not exists."""
        node: _StoreContainer = self
        for p in parts:
            nxt: Union[_StoreContainer, _Ref, None] = node._data.get(p)
            if nxt is None:
                nxt = _StoreContainer()
                node._data[p] = nxt
            elif not isinstance(nxt, _StoreContainer):
                raise KeyError(f"Conflict: {p!r} is already a leaf value.")
            node = nxt
        return node

    @contextmanager
    def with_hook(self, hook: StoreHook):
        prev_hook = self.hook
        self.hook = hook
        try:
            yield
        finally:
            self.hook = prev_hook

    def diff(
        self, other: "Store", strategy: Literal["ref", "is", "eq"] = "ref"
    ) -> _DiffResult:
        """Compares this Store with another, identifying added, removed, and modified items.
        This implementation uses a non-recursive, stack-based traversal algorithm.

        Args:
            other: The other Store instance to compare against.
            strategy: The comparison strategy for leaf values (_Ref.value).
                - "ref": Strict `_Ref` object identity (`is`).
                - "is": Identity comparison of `_Ref.value` (`is`).
                - "eq": Equality comparison of `_Ref.value` (`==`).

        Returns:
            `_DiffResult` instance containing three dictionaries:
            - added: Items in `other` but not in `self`. (key -> other_value)
            - removed: Items in `self` but not in `other`. (key -> self_value)
            - modified: Items in both but with different values. (key -> (self_value, other_value))
        """
        if strategy not in ("ref", "is", "eq"):
            raise ValueError(f"Unknown diff strategy: {strategy!r}")

        added: dict[str, Any] = {}
        removed: dict[str, Any] = {}
        modified: dict[str, tuple[Any, Any]] = {}

        def _recursive_diff(
            node_self: Union["_StoreContainer", "_Ref"],
            node_other: Union["_StoreContainer", "_Ref"],
            path_parts: tuple[str, ...],
        ) -> None:
            # Case 1: Both are internal nodes (Containers)
            if isinstance(node_self, _StoreContainer) and isinstance(
                node_other, _StoreContainer
            ):
                keys_self = set(node_self._data.keys())
                keys_other = set(node_other._data.keys())
                # 1. Removed: Keys in self but not in other
                for key in keys_self - keys_other:
                    removed.update(
                        self._collect_leaves(node_self._data[key], path_parts + (key,))
                    )
                # 2. Added: Keys in other but not in self
                for key in keys_other - keys_self:
                    added.update(
                        self._collect_leaves(node_other._data[key], path_parts + (key,))
                    )
                # 3. Common: Recurse on shared keys
                for key in keys_self & keys_other:
                    _recursive_diff(
                        node_self._data[key], node_other._data[key], path_parts + (key,)
                    )
            # Case 2: Both are leaf nodes (Refs)
            elif isinstance(node_self, _Ref) and isinstance(node_other, _Ref):
                val_self = node_self.value
                val_other = node_other.value
                is_different = False
                if strategy == "ref":
                    is_different = node_self is not node_other
                elif strategy == "is":
                    is_different = val_self is not val_other
                elif strategy == "eq":
                    is_different = val_self != val_other
                if is_different:
                    modified[".".join(path_parts)] = (val_self, val_other)
            # Case 3: Type Mismatch (One is Container, one is Ref)
            # Treat as "remove old tree" and "add new tree"
            else:
                removed.update(self._collect_leaves(node_self, path_parts))
                added.update(self._collect_leaves(node_other, path_parts))

        _recursive_diff(self, other, ())
        return _DiffResult(added, removed, modified)

    def _collect_leaves(
        self,
        node: Union["_StoreContainer", "_Ref"],
        path_prefix: tuple[str, ...],
    ) -> dict[str, Any]:
        """Helper to recursively find all leaf values from a starting node."""
        leaves: dict[str, Any] = {}

        def _traverse(
            node: Union["_StoreContainer", "_Ref"], current_path: tuple[str, ...]
        ) -> None:
            if isinstance(node, _Ref):
                leaves[".".join(current_path)] = node.value
            elif isinstance(node, _StoreContainer):
                for name, child in node._data.items():
                    _traverse(child, current_path + (name,))

        _traverse(node, path_prefix)
        return leaves

    def collect_leaves(self) -> dict[str, Any]:
        return self._collect_leaves(self, ())
