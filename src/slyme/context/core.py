# Copyright 2026 The SlymeLab Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import types
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import InitVar, dataclass, field
from enum import Enum
from typing import (
    Any,
    Generic,
    Literal,
    Optional,
    TypeVar,
    Union,
    cast,
)

from typing_extensions import Self

from slyme.utils.exception import enrich_exception
from slyme.utils.pytree import MappingKey

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_EMPTY_MAPPING: Mapping[str, Any] = types.MappingProxyType({})
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
DiffMissing = Enum("DiffMissing", ["MARK"])
DIFF_MISSING = DiffMissing.MARK


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
    metadata: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    parts: tuple[str, ...] = field(init=False)
    hash: int = field(init=False)

    def __post_init__(self) -> None:
        if not self.path:
            raise ValueError("Empty ref path")
        parts = tuple(self.path.split("."))
        if any(not p for p in parts):
            raise ValueError(f"Invalid ref path: {self.path!r}")
        # Bypass frozen=True to set fields
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "hash", hash(parts))
        if not isinstance(self.metadata, types.MappingProxyType):
            object.__setattr__(self, "metadata", types.MappingProxyType(self.metadata))

    def update_metadata(self, metadata: Mapping[str, Any]) -> "Ref[_T]":
        """Returns a new Ref with updated metadata (merging with existing)."""
        new_metadata = dict(self.metadata)
        new_metadata.update(metadata)
        return Ref(self.path, metadata=new_metadata)

    def at(
        self,
        subpath: str,
        metadata: Union[Optional[Mapping[str, Any]], _Missing] = _MISSING,
    ) -> "Ref":
        """Create a new Ref at a subpath relative to this Ref."""
        new_path = f"{self.path}.{subpath}" if self.path else subpath
        if metadata is _MISSING or metadata is None:
            return Ref(new_path)
        return Ref(new_path, metadata=metadata)

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Ref) and self.parts == other.parts

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.extra_repr()})"

    def extra_repr(self) -> str:
        repr_items = [f"path={self.path!r}"]
        if self.metadata:
            repr_items.append(f"metadata={self.metadata!r}")
        return ", ".join(repr_items)


class RefFactory:
    """Immutable factory that records attribute access as a dotted path.

    R.x.y.z records ``"x.y.z"``. Calling ``R.x.y.z()`` creates ``Ref("x.y.z")``.
    ``R.x.y.z(metadata=...)`` passes metadata to ``Ref``.
    """

    __slots__ = ("__path",)

    def __init__(self, path: str = "") -> None:
        object.__setattr__(self, "_RefFactory__path", path)

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __getattr__(self, name: str) -> "RefFactory":
        path = object.__getattribute__(self, "_RefFactory__path")
        new_path = f"{path}.{name}" if path else name
        return RefFactory(new_path)

    def __call__(
        self,
        *,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> Ref:
        path = object.__getattribute__(self, "_RefFactory__path")
        kwargs: dict[str, Any] = {}
        if metadata is not None:
            kwargs["metadata"] = metadata
        return Ref(path, **kwargs)

    def __repr__(self) -> str:
        path = object.__getattribute__(self, "_RefFactory__path")
        return f"R.{path}" if path else "R"


R = RefFactory()

RefLike = Union[Ref, RefFactory]


def to_ref(ref_like: RefLike) -> Ref:
    """Normalize a :class:`Ref` or :class:`RefFactory` into a :class:`Ref` object."""
    if isinstance(ref_like, RefFactory):
        return ref_like()
    return ref_like


class ContextPathError(KeyError):
    """Internal exception raised when a path cannot be resolved in the context."""

    pass


class ContextData(dict[str, Any]):
    """
    Internal dictionary implementation used to distinguish structural elements
    from user-provided dictionary values.

    Acts as the mutable data node for Context while preserving the distinction
    between context structure and user-provided dictionary values.
    """

    __slots__ = ()

    def _mutated(
        self,
        updates: dict[tuple[str, ...], Any],
        drops: set[tuple[str, ...]],
    ) -> Union["ContextData", _Missing]:
        """
        Build the result of simultaneous updates and drops without modifying self.

        Semantics:
        - Logically, drops are executed first, then updates.
          However, for efficiency, they are processed in a single pass.
        - Updates have higher priority than drops if they target the same path or its children.
          (i.e., updating 'a' overrides dropping 'a' or dropping 'a.b').
        - Drop Conflicts:
          - Overlapping drops (e.g., drop 'a' and drop 'a.b') are handled implicitly.
            Dropping the parent 'a' removes the entire subtree, so dropping 'a.b' is redundant but valid.
        - Update Conflicts:
          - Overlapping updates (e.g., update 'a' and update 'a.b') raise a ValueError.
            We cannot determine whether 'a' should be a leaf (value) or a container (for 'b').

        Algorithm:
        1. Base Cases:
           - If root is updated, return new value immediately.
           - If root is dropped (and not updated), return _MISSING.
        2. Group Operations:
           - Group updates and drops by their head key (first path component).
        3. Recursive Application:
           - For each head, recursively call mutate on the child.
           - Handle leaf conflicts (path blocked by existing value).
           - Enhance exceptions with path context.
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
        grouped_ops: defaultdict[
            str,
            tuple[dict[tuple[str, ...], Any], set[tuple[str, ...]]],
        ] = defaultdict(lambda: ({}, set()))

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
                # Conflict: Cannot update both parent and child simultaneously
                if len(sub_updates) > 1:
                    raise ValueError(
                        f"Update conflict at '{head}': Cannot update both parent and child simultaneously."
                    )

                # Apply update
                new_data[head] = sub_updates[()]

                # If we are overwriting the head, any drops for this head or its children
                # are implicitly handled (superseded by the overwrite).
                # This satisfies "batch drops then batch updates" semantics.
                continue

            # Get existing child or MISSING (if Reset, it's always MISSING)
            child = new_data.get(head, _MISSING)

            # Structure Validation / Auto-Vivification
            if isinstance(child, ContextData):
                pass
            elif child is _MISSING:
                # Create new container for updates
                if not sub_updates:
                    continue
                child = ContextData()
            elif () in sub_drops:
                # Child is a leaf.
                # If we are dropping the leaf itself, allow it.
                if not sub_updates:
                    new_data.pop(head, None)
                    continue
                # If we also have updates, we start from a fresh container (Reset)
                child = ContextData()
            else:
                # Conflict: Path blocked by leaf value
                raise ContextPathError(
                    f"Path '{head}' blocked by leaf value during mutation."
                )

            # Enrich both ContextPathError (structural issues) and ValueError (update conflicts)
            with enrich_exception(head, exc_types=(ContextPathError, ValueError)):
                new_child = child._mutated(sub_updates, sub_drops)
                if new_child is _MISSING:
                    new_data.pop(head, None)
                else:
                    new_data[head] = new_child

        return ContextData(new_data)

    def mutate(
        self,
        updates: dict[tuple[str, ...], Any],
        drops: set[tuple[str, ...]],
    ) -> None:
        """Atomically apply structural updates and drops in place."""
        new_data = self._mutated(updates, drops)
        if new_data is self:
            return
        self.clear()
        if new_data is not _MISSING:
            self.update(new_data)


@dataclass(frozen=True)
class ContextDiff:
    """
    Recursive diff structure representing changes between two ContextElements.
    Supports nested diffs for containers and direct value changes for leaves.
    """

    added: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    removed: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    modified: Mapping[str, tuple[Any, Any]] = field(
        default_factory=lambda: _EMPTY_MAPPING
    )
    nested: Mapping[str, "ContextDiff"] = field(default_factory=lambda: _EMPTY_MAPPING)

    def __post_init__(self):
        for name in ("added", "removed", "modified", "nested"):
            val = getattr(self, name)
            if not isinstance(val, types.MappingProxyType):
                object.__setattr__(self, name, types.MappingProxyType(val))

    def __bool__(self) -> bool:
        return bool(self.added or self.removed or self.modified or self.nested)

    def __repr__(self) -> str:
        parts = []
        if self.added:
            parts.append(f"added={list(self.added.keys())}")
        if self.removed:
            parts.append(f"removed={list(self.removed.keys())}")
        if self.modified:
            parts.append(f"modified={list(self.modified.keys())}")
        if self.nested:
            parts.append(f"nested={list(self.nested.keys())}")
        if not parts:
            return "ContextDiff(no changes)"
        return f"ContextDiff({', '.join(parts)})"

    def flatten(self) -> dict[str, tuple[Any, Any]]:
        """
        Flatten the recursive diff into a single dictionary of changes.
        Returns a dict of {path: (old_value, new_value)}.
        Added values have old_value as DIFF_MISSING.
        Removed values have new_value as DIFF_MISSING.
        """
        changes: dict[str, tuple[Any, Any]] = {}

        for k, v in self.added.items():
            changes[k] = (DIFF_MISSING, v)
        for k, v in self.removed.items():
            changes[k] = (v, DIFF_MISSING)
        for k, v in self.modified.items():
            changes[k] = v

        for key, child_diff in self.nested.items():
            for child_path, (old, new) in child_diff.flatten().items():
                changes[f"{key}.{child_path}"] = (old, new)

        return changes


def diff_context_data(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    strategy: Literal["is", "eq"] = "is",
) -> ContextDiff:
    """
    Compute the recursive difference between two ContextData objects.

    Both inputs are live mutable mappings. Callers that need a before/after diff
    must retain an independent copy of the earlier state.
    """
    if left is right:
        return ContextDiff()

    added = {}
    removed = {}
    modified = {}
    nested = {}

    keys_left = set(left.keys())
    keys_right = set(right.keys())

    # Removed keys
    for k in keys_left - keys_right:
        removed[k] = left[k]

    # Added keys
    for k in keys_right - keys_left:
        added[k] = right[k]

    # Shared keys
    for k in keys_left & keys_right:
        val_left = left[k]
        val_right = right[k]

        # Shared objects necessarily expose the same current mutable state.
        if val_left is val_right:
            continue

        # Check for nested containers (ContextData)
        is_container_left = isinstance(val_left, ContextData)
        is_container_right = isinstance(val_right, ContextData)

        if is_container_left and is_container_right:
            child_diff = diff_context_data(val_left, val_right, strategy)
            if child_diff:
                nested[k] = child_diff
        else:
            # Leaf comparison
            is_different = False
            if strategy == "is":
                is_different = val_left is not val_right
            elif strategy == "eq":
                is_different = val_left != val_right

            if is_different:
                modified[k] = (val_left, val_right)

    return ContextDiff(added, removed, modified, nested)


class ContextElement(ABC):
    """
    Abstract base class for context-related entities (Context, ContextView).
    """

    __slots__ = ()

    @abstractmethod
    def extract(self, ref_tree: Any) -> Any:
        pass

    @abstractmethod
    def get(
        self,
        ref: RefLike,
        default: Union[_T2, _Missing] = _MISSING,
    ) -> Any:
        pass

    @abstractmethod
    def exists(self, ref: RefLike) -> bool:
        pass

    @abstractmethod
    def keys(self, ref: Optional[RefLike] = None) -> Iterable[str]:
        pass

    @abstractmethod
    def to_context_data(self, ref: Optional[RefLike] = None) -> ContextData:
        pass

    @abstractmethod
    def to_dict(self, ref: Optional[RefLike] = None) -> dict[str, Any]:
        """Convert to standard python dictionary recursively."""
        pass

    def type_repr(self) -> str:
        return type(self).__name__

    def clone(self) -> "Context":
        """Clone ContextData containers while preserving leaf identities."""
        root = cast(
            ContextData,
            CONTEXT_ENGINE.map(lambda leaf: leaf, self.to_context_data()),
        )
        return Context._from_context_data(root)

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

        item_blocks: list[list[str]] = []
        for key in keys:
            val: Any = self.get(Ref[Any](key))
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
    ) -> ContextDiff:
        """
        Compute the difference between this element and another.
        Returns a recursive ContextDiff structure.
        """
        if strategy not in ("is", "eq"):
            raise ValueError(f"Unknown diff strategy: {strategy!r}")

        # Convert both to ContextData for recursive comparison
        # This handles Context, ContextView, and any other ContextElement
        data_self = self.to_context_data()
        data_other = other.to_context_data()

        return diff_context_data(data_self, data_other, strategy)

    def collect_leaves(self) -> dict[str, Any]:
        # CONTEXT_PYTREE_ENGINE is configured to only traverse Context/ContextData structure.
        # So it treats user dicts as leaves automatically.
        # Use iter_with_key_path for memory efficiency (generator based)
        iterator = CONTEXT_ENGINE.iter_with_key_path(self.to_context_data())

        leaves = {}
        for key_path, leaf in iterator:
            # We know ContextData keys are MappingKeys wrapping strings
            parts = [str(cast(MappingKey, k).key) for k in key_path]
            leaves[".".join(parts)] = leaf
        return leaves


@dataclass(frozen=True, repr=False)
class Context(ContextElement):
    """
    Context with a frozen outer identity and mutable `ContextData` contents.

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

    def extract(self, ref_tree: Any) -> Any:
        ref_tree = CTX_EVAL_ENGINE.map(to_ref, ref_tree)
        refs, treedef = CTX_EVAL_ENGINE.flatten(ref_tree)
        values = tuple(self._resolve(ref.parts) for ref in refs)

        values = tuple(
            ContextView(self, ref.parts) if isinstance(val, ContextData) else val
            for ref, val in zip(refs, values)
        )
        return CTX_EVAL_ENGINE.unflatten(treedef, values)

    def _build_dict_ref_tree(self, ref: Optional[RefLike] = None) -> Any:
        ref = to_ref(ref) if ref is not None else None
        data = self.to_context_data(ref)
        base_path = ref.path if ref else ""

        def build_tree(current_data: ContextData, current_path: str) -> dict[str, Any]:
            tree: dict[str, Any] = {}
            for k, v in current_data.items():
                path = f"{current_path}.{k}" if current_path else k
                if isinstance(v, ContextData):
                    tree[k] = build_tree(v, path)
                else:
                    tree[k] = Ref(path)
            return tree

        return build_tree(data, base_path)

    # --- Read Operations ---
    def get(
        self,
        ref: RefLike,
        default: Union[_T2, _Missing] = _MISSING,
    ) -> Any:
        ref = to_ref(ref)
        try:
            return self.extract(ref)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: RefLike) -> bool:
        ref = to_ref(ref)
        try:
            self._resolve(ref.parts)
            return True
        except ContextPathError:
            return False

    def keys(self, ref: Optional[RefLike] = None) -> Iterable[str]:
        if ref is None:
            return self._root.keys()
        ref = to_ref(ref)
        element = self._resolve(ref.parts)
        if isinstance(element, ContextData):
            return element.keys()
        raise ContextPathError("Cannot list keys of a leaf value.")

    def to_context_data(self, ref: Optional[RefLike] = None) -> ContextData:
        if ref is None:
            return self._root
        ref = to_ref(ref)
        val = self._resolve(ref.parts)
        if isinstance(val, ContextData):
            return val
        raise ContextPathError("Target is not a ContextData (container).")

    def to_dict(self, ref: Optional[RefLike] = None) -> dict[str, Any]:
        ref_tree = self._build_dict_ref_tree(ref)
        return self.extract(ref_tree)

    def _resolve(self, parts: Iterable[str]) -> Any:
        current: Any = self._root
        for p in parts:
            if not isinstance(current, ContextData):
                raise ContextPathError("Path blocked by leaf value.")
            try:
                current = current[p]
            except KeyError:
                raise ContextPathError(p) from None
        return current

    # --- Unified Modification Interface ---
    def mutate(
        self,
        *,
        updates: Optional[Mapping[RefLike, Any]] = None,
        drops: Optional[Iterable[RefLike]] = None,
    ) -> None:
        """
        Apply a transaction-like set of modifications (updates and drops) atomically.

        Args:
            updates: A mapping of References to new values.
            drops: An iterable of References to remove.

        The mutation is atomic and returns ``None``.
        """
        if not updates and not drops:
            return None

        normalized_updates: dict[Ref[Any], Any] = (
            {to_ref(k): v for k, v in updates.items()} if updates else {}
        )
        normalized_drops: set[Ref[Any]] = {to_ref(r) for r in drops} if drops else set()
        raw_updates: dict[tuple[str, ...], Any] = {
            r.parts: v for r, v in normalized_updates.items()
        }
        raw_drops: set[tuple[str, ...]] = {r.parts for r in normalized_drops}

        self._root.mutate(raw_updates, raw_drops)

    # --- Convenience Interfaces ---
    def update(self, updates: Mapping[RefLike, Any]) -> None:
        """Batch update convenience interface."""
        self.mutate(updates=updates)

    def drop(self, refs: Iterable[RefLike]) -> None:
        """Batch delete convenience interface."""
        self.mutate(drops=refs)

    def set(self, ref: RefLike, value: _T) -> None:
        """Single set convenience interface."""
        ref = to_ref(ref)
        self.mutate(updates={ref: value})

    def update_tree(self, ref_tree: Any, value_tree: Any) -> None:
        """
        Recursively update the context using a structure of references (ref_tree)
        and a matching structure of values (value_tree).

        Args:
            ref_tree: A nested structure (list, tuple, dict, MappingProxyType) where
                      leaves are references (Ref objects) that point to locations in the context.
            value_tree: A nested structure matching the shape of ref_tree, containing
                        the values to be updated at the corresponding references.

        The context is updated in place and the method returns ``None``.
        """
        updates: dict[RefLike, Any] = {
            to_ref(ref): CTX_EVAL_ENGINE.get_element(value_tree, path)
            for path, ref in CTX_EVAL_ENGINE.iter_with_key_path(ref_tree)
        }
        self.mutate(updates=updates)

    def delete(self, ref: RefLike) -> None:
        """Single delete convenience interface."""
        ref = to_ref(ref)
        self.mutate(drops=[ref])

    def clear(self, ref: RefLike) -> None:
        """
        Clear all contents under a reference but keep the path.
        Raises ContextPathError if the target is not a container (ContextData).
        """
        ref = to_ref(ref)
        # 1. Validate target is a container
        val = self._resolve(ref.parts)
        if not isinstance(val, ContextData):
            raise ContextPathError(
                f"Cannot clear '{ref.path}': not a container (ContextData)."
            )

        # 2. Update with empty ContextData
        self.mutate(updates={ref: ContextData()})


@dataclass(frozen=True, repr=False)
class ContextView(ContextElement):
    """
    Read-only view of a subtree within a Context.
    """

    _context: Context
    _parts: tuple[str, ...]

    def _adjust_ref(self, ref: Optional[RefLike]) -> Ref:
        if ref is None:
            return Ref(".".join(self._parts))
        ref = to_ref(ref)
        new_parts = self._parts + ref.parts
        new_path = ".".join(new_parts)
        return Ref(new_path, metadata=ref.metadata)

    def _adjust_ref_tree(self, ref_tree: Any) -> Any:
        def adjust(obj):
            if isinstance(obj, (Ref, RefFactory)):
                return self._adjust_ref(obj)
            return obj

        return CTX_EVAL_ENGINE.map(adjust, ref_tree)

    def extract(self, ref_tree: Any) -> Any:
        return self._context.extract(self._adjust_ref_tree(ref_tree))

    def get(
        self,
        ref: RefLike,
        default: Union[_T2, _Missing] = _MISSING,
    ) -> Any:
        return self._context.get(self._adjust_ref(ref), default)

    def exists(self, ref: RefLike) -> bool:
        return self._context.exists(self._adjust_ref(ref))

    def keys(self, ref: Optional[RefLike] = None) -> Iterable[str]:
        return self._context.keys(self._adjust_ref(ref))

    def to_context_data(self, ref: Optional[RefLike] = None) -> ContextData:
        return self._context.to_context_data(self._adjust_ref(ref))

    def to_dict(self, ref: Optional[RefLike] = None) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref))


from .tree import CONTEXT_ENGINE, CTX_EVAL_ENGINE
