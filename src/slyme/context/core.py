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
    TYPE_CHECKING,
)
from typing_extensions import Self
from slyme.utils.pytree import (
    PyTreeEngine,
    MappingKey,
    KeyPath,
)
from slyme.utils.exception import enrich_exception

if TYPE_CHECKING:
    from .hook import Hook

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_EMPTY_MAPPING = types.MappingProxyType({})
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
    """Immutable dotted ref with cached hash and split parts.

    NOTE: Ref.key_path can only resolve leaf values.
    """

    path: str
    key_path: KeyPath = ()
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
        object.__setattr__(self, "hash", hash((parts, self.key_path)))
        if not isinstance(self.metadata, types.MappingProxyType):
            object.__setattr__(self, "metadata", types.MappingProxyType(self.metadata))

    def _resolve(self, pytree) -> _T:
        return PyTreeEngine.get_element(pytree, self.key_path)

    def update_metadata(self, metadata: Mapping[str, Any]) -> "Ref[_T]":
        """Returns a new Ref with updated metadata (merging with existing)."""
        new_metadata = dict(self.metadata)
        new_metadata.update(metadata)
        return Ref(self.path, key_path=self.key_path, metadata=new_metadata)

    def at(
        self,
        subpath: str,
        key_path: Union[KeyPath, _Missing] = _MISSING,
        metadata: Union[Optional[Mapping[str, Any]], _Missing] = _MISSING,
    ) -> "Ref":
        """
        Create a new Ref at a subpath relative to this Ref.

        Does NOT inherit key_path or metadata from the parent Ref by default.
        """
        new_path = f"{self.path}.{subpath}" if self.path else subpath
        kwargs = {}
        if key_path is not _MISSING:
            kwargs["key_path"] = key_path
        if metadata is not _MISSING:
            kwargs["metadata"] = metadata
        return Ref(new_path, **kwargs)

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, Ref)
            and self.parts == other.parts
            and self.key_path == other.key_path
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.extra_repr()})"

    def extra_repr(self) -> str:
        repr_items = [f"path={self.path!r}"]
        if self.key_path:
            repr_items.append(
                f"key_path_expr={PyTreeEngine.codify_key_path(self.key_path)}"
            )
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
                new_child = child.mutate(sub_updates, sub_drops)
                if new_child is _MISSING:
                    new_data.pop(head, None)
                else:
                    new_data[head] = new_child

        return ContextData(new_data)


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
    Optimized for Copy-On-Write (COW) structures by skipping identical subtrees.
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

        # Optimization: Skip identical objects (COW sharing)
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
    def extract(self, ref_tree: Any, *, apply_hook: bool = True, **kwargs) -> Any:
        pass

    @abstractmethod
    async def async_extract(
        self, ref_tree: Any, *, apply_hook: bool = True, **kwargs
    ) -> Any:
        pass

    @abstractmethod
    def get(
        self,
        ref: Ref[_T],
        default: Union[_T2, _Missing] = _MISSING,
        *,
        apply_hook: bool = True,
    ) -> Union[_T, _T2]:
        pass

    @abstractmethod
    async def async_get(
        self,
        ref: Ref[_T],
        default: Union[_T2, _Missing] = _MISSING,
        *,
        apply_hook: bool = True,
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

    @abstractmethod
    def to_dict(
        self, ref: Optional[Ref[_T]] = None, *, apply_hook: bool = True
    ) -> dict[str, Any]:
        """Convert to standard python dictionary recursively."""
        pass

    @abstractmethod
    async def async_to_dict(
        self, ref: Optional[Ref[_T]] = None, *, apply_hook: bool = True
    ) -> dict[str, Any]:
        pass

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
            val = self.get(Ref(key), apply_hook=False)
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
    Immutable Context implementation with efficient Copy-On-Write (COW) updates.
    Wraps a root `ContextData`.

    The main **runtime** context container for the node execution.
    """

    _root: ContextData = field(init=False)
    _hook: Optional["Hook"] = field(default=None, init=False)
    data: InitVar[Optional[Mapping[str, Any]]] = None
    hook: InitVar[Optional["Hook"]] = None

    def __post_init__(
        self, data: Optional[Mapping[str, Any]] = None, hook: Optional["Hook"] = None
    ) -> None:
        if data is None:
            root = ContextData()
        elif isinstance(data, ContextData):
            root = data
        else:
            # Shallow conversion strictly for the top level.
            # Trusts user input for deep structure.
            root = ContextData(data)
        object.__setattr__(self, "_root", root)
        object.__setattr__(self, "_hook", hook)

    @classmethod
    def _from_context_data(
        cls, root: ContextData, hook: Optional["Hook"] = None
    ) -> "Context":
        obj = object.__new__(cls)
        object.__setattr__(obj, "_root", root)
        object.__setattr__(obj, "_hook", hook)
        return obj

    def extract(self, ref_tree: Any, *, apply_hook: bool = True, **kwargs) -> Any:
        refs, treedef = CTX_EVAL_ENGINE.flatten(ref_tree)
        values = tuple(self._resolve(ref.parts) for ref in refs)
        # NOTE: Check key_path
        for ref, val in zip(refs, values):
            if isinstance(val, ContextData) and ref.key_path:
                raise ValueError("Ref.key_path can only resolve leaf values.")

        if apply_hook and self._hook:
            result = self._hook.on_extract(ctx=self, refs=refs, values=values, **kwargs)
            values = result.values

        values = tuple(
            ContextView(self, ref.parts)
            if isinstance(val, ContextData)
            else ref._resolve(val)
            for ref, val in zip(refs, values)
        )
        return CTX_EVAL_ENGINE.unflatten(treedef, values)

    async def async_extract(
        self, ref_tree: Any, *, apply_hook: bool = True, **kwargs
    ) -> Any:
        refs, treedef = CTX_EVAL_ENGINE.flatten(ref_tree)
        values = tuple(self._resolve(ref.parts) for ref in refs)
        # NOTE: Check key_path
        for ref, val in zip(refs, values):
            if isinstance(val, ContextData) and ref.key_path:
                raise ValueError("Ref.key_path can only resolve leaf values.")

        if apply_hook and self._hook:
            result = await self._hook.on_async_extract(
                ctx=self, refs=refs, values=values, **kwargs
            )
            values = result.values

        values = tuple(
            ContextView(self, ref.parts)
            if isinstance(val, ContextData)
            else ref._resolve(val)
            for ref, val in zip(refs, values)
        )
        return CTX_EVAL_ENGINE.unflatten(treedef, values)

    def _build_dict_ref_tree(self, ref: Optional[Ref[_T]] = None) -> Any:
        data = self.to_context_data(ref)
        base_path = ref.path if ref else ""

        def build_tree(current_data: ContextData, current_path: str) -> dict[str, Any]:
            tree = {}
            for k, v in current_data.items():
                path = f"{current_path}.{k}" if current_path else k
                if isinstance(v, ContextData):
                    tree[k] = build_tree(v, path)
                else:
                    tree[k] = Ref(path)
            return tree

        return build_tree(data, base_path)

    # --- Read Operations ---
    @overload
    def get(self, ref: Ref[_T], *, apply_hook: bool = True) -> _T: ...
    @overload
    def get(
        self, ref: Ref[_T], default: _T2, *, apply_hook: bool = True
    ) -> Union[_T, _T2]: ...
    def get(
        self,
        ref: Ref[_T],
        default: Union[_T2, _Missing] = _MISSING,
        *,
        apply_hook: bool = True,
    ) -> Union[_T, _T2]:
        try:
            return self.extract(ref, apply_hook=apply_hook)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    @overload
    async def async_get(
        self,
        ref: Ref[_T],
        *,
        apply_hook: bool = True,
    ) -> _T: ...
    @overload
    async def async_get(
        self,
        ref: Ref[_T],
        default: _T2,
        *,
        apply_hook: bool = True,
    ) -> Union[_T, _T2]: ...
    async def async_get(
        self,
        ref: Ref[_T],
        default: Union[_T2, _Missing] = _MISSING,
        *,
        apply_hook: bool = True,
    ) -> Union[_T, _T2]:
        try:
            return await self.async_extract(ref, apply_hook=apply_hook)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: Ref[_T]) -> bool:
        if ref.key_path:
            raise ValueError(
                f"Ref.key_path must be empty for existence check (found {ref.key_path!r}). "
                "Use get() to check leaf value existence."
            )
        try:
            self._resolve(ref.parts)
            return True
        except ContextPathError:
            return False

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        if ref is None:
            return self._root.keys()
        if ref.key_path:
            raise ValueError(
                f"Ref.key_path must be empty for listing keys (found {ref.key_path!r}). "
                "Context keys operation is structural and does not support leaf navigation."
            )
        element = self._resolve(ref.parts)
        if isinstance(element, ContextData):
            return element.keys()
        raise ContextPathError("Cannot list keys of a leaf value.")

    def to_context_data(self, ref: Optional[Ref[_T]] = None) -> ContextData:
        if ref is None:
            return self._root
        if ref.key_path:
            raise ValueError(
                f"Ref.key_path must be empty for converting to ContextData (found {ref.key_path!r}). "
                "ContextData conversion is structural and does not support leaf navigation."
            )
        val = self._resolve(ref.parts)
        if isinstance(val, ContextData):
            return val
        raise ContextPathError("Target is not a ContextData (container).")

    def to_dict(
        self, ref: Optional[Ref[_T]] = None, *, apply_hook: bool = True
    ) -> dict[str, Any]:
        ref_tree = self._build_dict_ref_tree(ref)
        return self.extract(ref_tree, apply_hook=apply_hook)

    async def async_to_dict(
        self, ref: Optional[Ref[_T]] = None, *, apply_hook: bool = True
    ) -> dict[str, Any]:
        ref_tree = self._build_dict_ref_tree(ref)
        return await self.async_extract(ref_tree, apply_hook=apply_hook)

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
        updates: Optional[Mapping[Ref, Any]] = None,
        drops: Optional[Iterable[Ref]] = None,
        apply_hook: bool = True,
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

        updates = dict(updates) if updates else {}
        drops = set(drops) if drops else set()
        # Validate Ref.key_path is empty for all mutation operations
        invalid_updates = [r for r in updates if r.key_path]
        invalid_drops = [r for r in drops if r.key_path]
        if invalid_updates or invalid_drops:
            raise ValueError(
                f"Ref.key_path must be empty for mutation operations (found {invalid_updates!r} in updates "
                f"and {invalid_drops!r} in drops). Mutation on a specific key path is ambiguous; operate "
                "on the full path instead."
            )

        if apply_hook and self._hook:
            result = self._hook.on_mutate(ctx=self, updates=updates, drops=drops)
            updates, drops = result.updates, result.drops
        raw_updates = {r.parts: v for r, v in updates.items()}
        raw_drops = {r.parts for r in drops}

        # Delegate to the root ContextData
        new_root = self._root.mutate(raw_updates, raw_drops)
        # Edge Case: If the root itself resulted in MISSING (dropped), we reset to empty.
        if new_root is _MISSING:
            new_root = ContextData()
        return self._from_context_data(new_root, hook=self._hook)

    async def async_mutate(
        self,
        *,
        updates: Optional[Mapping[Ref, Any]] = None,
        drops: Optional[Iterable[Ref]] = None,
        apply_hook: bool = True,
    ) -> "Context":
        if not updates and not drops:
            return self

        updates = dict(updates) if updates else {}
        drops = set(drops) if drops else set()
        # Validate Ref.key_path is empty for all mutation operations
        invalid_updates = [r for r in updates if r.key_path]
        invalid_drops = [r for r in drops if r.key_path]
        if invalid_updates or invalid_drops:
            raise ValueError(
                f"Ref.key_path must be empty for mutation operations (found {invalid_updates!r} in updates "
                f"and {invalid_drops!r} in drops). Mutation on a specific key path is ambiguous; operate "
                "on the full path instead."
            )

        if apply_hook and self._hook:
            result = await self._hook.on_async_mutate(
                ctx=self, updates=updates, drops=drops
            )
            updates, drops = result.updates, result.drops
        raw_updates = {r.parts: v for r, v in updates.items()}
        raw_drops = {r.parts for r in drops}

        # Delegate to the root ContextData
        new_root = self._root.mutate(raw_updates, raw_drops)
        # Edge Case: If the root itself resulted in MISSING (dropped), we reset to empty.
        if new_root is _MISSING:
            new_root = ContextData()
        return self._from_context_data(new_root, hook=self._hook)

    # --- Convenience Interfaces ---
    def update(
        self, updates: Mapping[Ref, Any], *, apply_hook: bool = True
    ) -> "Context":
        """Batch update convenience interface."""
        return self.mutate(updates=updates, apply_hook=apply_hook)

    async def async_update(
        self, updates: Mapping[Ref, Any], *, apply_hook: bool = True
    ) -> "Context":
        return await self.async_mutate(updates=updates, apply_hook=apply_hook)

    def drop(self, refs: Iterable[Ref], *, apply_hook: bool = True) -> "Context":
        """Batch delete convenience interface."""
        return self.mutate(drops=refs, apply_hook=apply_hook)

    async def async_drop(
        self, refs: Iterable[Ref], *, apply_hook: bool = True
    ) -> "Context":
        return await self.async_mutate(drops=refs, apply_hook=apply_hook)

    def set(self, ref: Ref[_T], value: _T, *, apply_hook: bool = True) -> "Context":
        """Single set convenience interface."""
        return self.mutate(updates={ref: value}, apply_hook=apply_hook)

    async def async_set(
        self, ref: Ref[_T], value: _T, *, apply_hook: bool = True
    ) -> "Context":
        return await self.async_mutate(updates={ref: value}, apply_hook=apply_hook)

    def update_tree(
        self, ref_tree: Any, value_tree: Any, *, apply_hook: bool = True
    ) -> "Context":
        """
        Recursively update the context using a structure of references (ref_tree)
        and a matching structure of values (value_tree).

        Args:
            ref_tree: A nested structure (list, tuple, dict, MappingProxyType) where
                      leaves are references (Ref objects) that point to locations in the context.
            value_tree: A nested structure matching the shape of ref_tree, containing
                        the values to be updated at the corresponding references.

        Returns:
            A new Context instance with the updates applied.
        """
        updates = {
            ref: CTX_EVAL_ENGINE.get_element(value_tree, path)
            for path, ref in CTX_EVAL_ENGINE.iter_with_key_path(ref_tree)
        }
        return self.mutate(updates=updates, apply_hook=apply_hook)

    async def async_update_tree(
        self, ref_tree: Any, value_tree: Any, *, apply_hook: bool = True
    ) -> "Context":
        """
        Recursively update the context using a structure of references (ref_tree)
        and a matching structure of values (value_tree).

        Args:
            ref_tree: A nested structure (list, tuple, dict, MappingProxyType) where
                      leaves are references (Ref objects) that point to locations in the context.
            value_tree: A nested structure matching the shape of ref_tree, containing
                        the values to be updated at the corresponding references.

        Returns:
            A new Context instance with the updates applied.
        """
        updates = {
            ref: CTX_EVAL_ENGINE.get_element(value_tree, path)
            for path, ref in CTX_EVAL_ENGINE.iter_with_key_path(ref_tree)
        }
        return await self.async_mutate(updates=updates, apply_hook=apply_hook)

    def delete(self, ref: Ref[_T], *, apply_hook: bool = True) -> "Context":
        """Single delete convenience interface."""
        return self.mutate(drops=[ref], apply_hook=apply_hook)

    async def async_delete(self, ref: Ref[_T], *, apply_hook: bool = True) -> "Context":
        return await self.async_mutate(drops=[ref], apply_hook=apply_hook)

    def clear(self, ref: Ref[_T], *, apply_hook: bool = True) -> "Context":
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
        return self.mutate(updates={ref: ContextData()}, apply_hook=apply_hook)

    async def async_clear(self, ref: Ref[_T], *, apply_hook: bool = True) -> "Context":
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
        return await self.async_mutate(
            updates={ref: ContextData()}, apply_hook=apply_hook
        )


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
        return Ref(new_path, key_path=ref.key_path, metadata=ref.metadata)

    def _adjust_ref_tree(self, ref_tree: Any) -> Any:
        def adjust(obj):
            if isinstance(obj, Ref):
                return self._adjust_ref(obj)
            return obj

        return CTX_EVAL_ENGINE.map(adjust, ref_tree)

    def extract(self, ref_tree: Any, *, apply_hook: bool = True, **kwargs) -> Any:
        return self._context.extract(
            self._adjust_ref_tree(ref_tree), apply_hook=apply_hook, **kwargs
        )

    async def async_extract(
        self, ref_tree: Any, *, apply_hook: bool = True, **kwargs
    ) -> Any:
        return await self._context.async_extract(
            self._adjust_ref_tree(ref_tree), apply_hook=apply_hook, **kwargs
        )

    def get(
        self,
        ref: Ref[_T],
        default: Union[_T2, _Missing] = _MISSING,
        *,
        apply_hook: bool = True,
    ) -> Union[_T, _T2]:
        return self._context.get(self._adjust_ref(ref), default, apply_hook=apply_hook)

    async def async_get(
        self,
        ref: Ref[_T],
        default: Union[_T2, _Missing] = _MISSING,
        *,
        apply_hook: bool = True,
    ) -> Union[_T, _T2]:
        return await self._context.async_get(
            self._adjust_ref(ref), default, apply_hook=apply_hook
        )

    def exists(self, ref: Ref[_T]) -> bool:
        return self._context.exists(self._adjust_ref(ref))

    def keys(self, ref: Optional[Ref[_T]] = None) -> Iterable[str]:
        return self._context.keys(self._adjust_ref(ref))

    def to_context_data(self, ref: Optional[Ref[_T]] = None) -> ContextData:
        return self._context.to_context_data(self._adjust_ref(ref))

    def to_dict(
        self, ref: Optional[Ref[_T]] = None, *, apply_hook: bool = True
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref), apply_hook=apply_hook)

    async def async_to_dict(
        self, ref: Optional[Ref[_T]] = None, *, apply_hook: bool = True
    ) -> dict[str, Any]:
        return await self._context.async_to_dict(
            self._adjust_ref(ref), apply_hook=apply_hook
        )


from .tree import CONTEXT_ENGINE, CTX_EVAL_ENGINE
