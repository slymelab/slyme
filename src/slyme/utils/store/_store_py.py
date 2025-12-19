from dataclasses import dataclass
from collections import deque
from contextlib import AbstractContextManager
from slyme.utils.typing import (
    Any,
    Generic,
    TypeVar,
    Union,
    Self,
    Literal,
    Iterable,
)
from slyme.utils.constant import MISSING
from slyme.utils.inspect import resolve_instance_classname
from slyme.utils.common import dict_to_key_value_str
from slyme.utils.scoped import ScopedAttrAssign
from .hook import StoreHook

_T = TypeVar("_T")


class Key(Generic[_T]):
    """Immutable dotted key with cached hash and split parts."""

    __slots__ = ("_path", "_parts", "_hash")

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
        return f"{resolve_instance_classname(self)}({self.path!r})"


class _Ref(Generic[_T]):
    """Reference node to distinguish any value."""

    __slots__ = ("_value",)

    @property
    def value(self) -> _T:
        return self._value

    def __init__(self, value: _T):
        super().__init__()
        self._value = value

    def __repr__(self) -> str:
        return f"{resolve_instance_classname(self)}(value={self.value!r})"


class _StoreNode:
    """Inner store node.
    NOTE: `_StoreNode` can only be modified through `Store` for consistency.
    """

    __slots__ = ("_data",)

    @property
    def value(self) -> Self:
        return self

    def __init__(
        self, data: Union[dict[str, Union["_StoreNode", _Ref]], None] = None
    ) -> None:
        self._data: dict[str, Union[_StoreNode, _Ref]] = (
            data if data is not None else {}
        )

    def __getitem__(self, key: Union[Key[_T], str]) -> _T:
        if isinstance(key, str):
            key = Key(key)
        return self._getitem(key)

    def _getitem(self, key: Key[_T]) -> _T:
        # NOTE: Annotate to `Any` to pass the type checker.
        result: Any = self._resolve(key.parts).value
        return result

    def _resolve(self, parts: Iterable[str]) -> Union["_StoreNode", _Ref]:
        """Resolve the path parts and get the final node."""
        node: Union[_StoreNode, _Ref] = self
        for p in parts:
            if not isinstance(node, _StoreNode):
                raise KeyError(f"Path {parts} not found")
            node = node._data[p]
        return node

    def __repr__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}"
            f"({dict_to_key_value_str({k: v.value for k, v in self._data.items()})})"
        )

    def copy(self) -> Self:
        return type(self)(
            data={
                k: (v.copy() if isinstance(v, _StoreNode) else v)
                for k, v in self._data.items()
            }
        )


@dataclass(frozen=True)
class _DiffResult:
    __slots__ = ("added", "removed", "modified")
    added: dict[str, Any]  # {key: other_value}
    removed: dict[str, Any]  # {key: self_value}
    modified: dict[str, tuple[Any, Any]]  # {key: (self_value, other_value)}


class Store(_StoreNode):
    """Dotted-attribute-style nested store."""

    __slots__ = ("hook",)

    def __init__(
        self,
        hook: Union[StoreHook, None] = None,
        data: Union[dict[str, Union[_StoreNode, _Ref]], None] = None,
    ) -> None:
        super().__init__(data=data)
        self.hook = hook

    def __getitem__(self, key: Union[Key[_T], str]) -> _T:
        if isinstance(key, str):
            key = Key(key)
        value = self._getitem(key)
        if self.hook is not None:
            # Call hook
            self.hook.getitem(self, key, value)
        return value

    def __setitem__(self, key: Union[Key[_T], str], value: _T) -> None:
        if isinstance(key, str):
            key = Key(key)
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

    def __delitem__(self, key: Union[Key[_T], str]) -> None:
        if isinstance(key, str):
            key = Key(key)
        *dirs, last = key.parts
        parent = self._resolve(dirs)
        if not isinstance(parent, _StoreNode):
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

    def _touch(self, parts: Iterable[str]) -> _StoreNode:
        """Recursively resolve the store nodes along the path, and create a
        new node if the node not exists."""
        node: _StoreNode = self
        for p in parts:
            nxt: Union[_StoreNode, _Ref, None] = node._data.get(p)
            if nxt is None:
                nxt = _StoreNode()
                node._data[p] = nxt
            elif not isinstance(nxt, _StoreNode):
                raise KeyError(f"Conflict: {p!r} is already a leaf value.")
            node = nxt
        return node

    def with_hook(self, hook: StoreHook) -> AbstractContextManager:
        return ScopedAttrAssign({"hook": hook}).enter_scope(self)

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
        # The stack holds tuples of (node_from_self, node_from_other, current_path_tuple)
        stack: deque[tuple[_StoreNode, _StoreNode, tuple[str, ...]]] = deque(
            [(self, other, ())]
        )

        while stack:
            node_self, node_other, path_parts = stack.pop()

            keys_self = set(node_self._data.keys())
            keys_other = set(node_other._data.keys())

            # Find removed keys (in self but not in other)
            for key_str in keys_self - keys_other:
                # The entire subtree under this key was removed.
                removed_node = node_self._data[key_str]
                removed.update(
                    self._collect_leaves(removed_node, path_parts + (key_str,))
                )

            # Find added keys (in other but not in self)
            for key_str in keys_other - keys_self:
                # The entire subtree under this key was added.
                added_node = node_other._data[key_str]
                added.update(self._collect_leaves(added_node, path_parts + (key_str,)))

            # Find common keys to compare their children
            for key_str in keys_self & keys_other:
                child_self = node_self._data[key_str]
                child_other = node_other._data[key_str]
                current_path = path_parts + (key_str,)

                # Both are internal nodes, so we continue traversing.
                if isinstance(child_self, _StoreNode) and isinstance(
                    child_other, _StoreNode
                ):
                    stack.append((child_self, child_other, current_path))
                    continue

                # Both are leaf nodes, so we compare their values.
                if isinstance(child_self, _Ref) and isinstance(child_other, _Ref):
                    val_self = child_self.value
                    val_other = child_other.value

                    # Comparison logic
                    if strategy == "ref":
                        is_different = child_self is not child_other
                    elif strategy == "is":
                        is_different = val_self is not val_other
                    elif strategy == "eq":
                        is_different = val_self != val_other

                    if is_different:
                        modified[".".join(current_path)] = (val_self, val_other)
                    continue

                # Mismatch in node types. Treat as a removal of the old and an
                # addition of the new.
                removed.update(self._collect_leaves(child_self, current_path))
                added.update(self._collect_leaves(child_other, current_path))

        return _DiffResult(added, removed, modified)

    def _collect_leaves(
        self,
        start_node: Union[_StoreNode, _Ref],
        path_prefix: tuple[str, ...],
    ) -> dict[str, Any]:
        """Helper to recursively find all leaf values from a starting node."""
        leaves = {}
        stack: deque[tuple[Union[_StoreNode, _Ref], tuple[str, ...]]] = deque(
            [(start_node, path_prefix)]
        )

        while stack:
            node, current_path_parts = stack.pop()
            if isinstance(node, _Ref):
                # It's a leaf node, add it to our collection
                leaves[".".join(current_path_parts)] = node.value
            elif isinstance(node, _StoreNode):
                # It's an internal node, add its children to the stack
                for name, child in node._data.items():
                    stack.append((child, current_path_parts + (name,)))
        return leaves

    def collect_leaves(self) -> dict[str, Any]:
        return self._collect_leaves(self, ())
