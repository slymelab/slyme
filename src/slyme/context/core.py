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

from __future__ import annotations

import difflib
import keyword
import types
from abc import ABC, abstractmethod
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from dataclasses import InitVar, dataclass, field, replace
from enum import Enum
from typing import (
    Any,
    Generic,
    Literal,
    TypeVar,
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
    """Immutable dotted ref, or an unbound declaration for RefFactory."""

    path: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    parts: tuple[str, ...] = field(init=False)
    hash: int | None = field(init=False)

    def __post_init__(self) -> None:
        if self.path == "":
            raise ValueError("Empty ref path")
        if self.path is None:
            parts: tuple[str, ...] = ()
            ref_hash = None
        else:
            parts = tuple(self.path.split("."))
            if any(not p for p in parts):
                raise ValueError(f"Invalid ref path: {self.path!r}")
            ref_hash = hash(parts)
        # Bypass frozen=True to set fields
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "hash", ref_hash)
        object.__setattr__(
            self,
            "metadata",
            types.MappingProxyType(dict(self.metadata)),
        )

    @property
    def is_bound(self) -> bool:
        return self.path is not None

    @property
    def bound_path(self) -> str:
        """Return the concrete path or reject an unbound declaration."""
        return self._require_bound()

    def _require_bound(self) -> str:
        if self.path is None:
            raise ValueError(
                "Ref has no path; bind it through RefFactory or Ref.bind()."
            )
        return self.path

    def bind(self, path: str) -> Ref[_T]:
        """Return this Ref bound to *path* without mutating the declaration."""
        if self.path is None:
            return replace(self, path=path)
        if self.path != path:
            raise ValueError(
                f"Ref is already bound to {self.path!r}, cannot bind it to {path!r}."
            )
        return self

    def update_metadata(self, metadata: Mapping[str, Any]) -> Ref[_T]:
        """Returns a new Ref with updated metadata (merging with existing)."""
        new_metadata = dict(self.metadata)
        new_metadata.update(metadata)
        return replace(self, metadata=new_metadata)

    def at(
        self,
        subpath: str,
        metadata: Mapping[str, Any] | None | _Missing = _MISSING,
    ) -> Ref:
        """Create a new Ref at a subpath relative to this Ref."""
        path = self._require_bound()
        new_path = f"{path}.{subpath}"
        if metadata is _MISSING or metadata is None:
            return Ref(new_path)
        return Ref(new_path, metadata=metadata)

    def __hash__(self) -> int:
        if self.hash is None:
            raise TypeError("Unbound Ref objects are not hashable.")
        return self.hash

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True
        return (
            isinstance(other, Ref)
            and self.path is not None
            and other.path is not None
            and self.parts == other.parts
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}({self.extra_repr()})"

    def extra_repr(self) -> str:
        path_repr = "<unbound>" if self.path is None else repr(self.path)
        repr_items = [f"path={path_repr}"]
        if self.metadata:
            repr_items.append(f"metadata={self.metadata!r}")
        return ", ".join(repr_items)


_REF_ENTRY_KEY = ""
_REF_FACTORY_RESERVED_NAMES = frozenset({"merge"})


def _freeze_mapping(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Snapshot a mapping behind the current immutable mapping implementation."""
    return types.MappingProxyType(dict(data))


@dataclass(frozen=True)
class _RefDeclaration:
    ref: Ref[Any]
    explicit: bool


_SchemaEntry = Ref[Any] | Mapping[str, Any]


def _validate_schema_name(name: Any, path: str) -> str:
    if not isinstance(name, str):
        raise TypeError(
            f"Invalid schema key at {path or '<root>'}: expected str, "
            f"got {type(name).__name__}."
        )
    if not name or "." in name or not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(
            f"Invalid schema key {name!r} at {path or '<root>'}; "
            "keys must be non-keyword Python identifiers without dots."
        )
    if name.startswith("_") or name in _REF_FACTORY_RESERVED_NAMES:
        raise ValueError(
            f"Schema key {name!r} at {path or '<root>'} is reserved by RefFactory."
        )
    return name


def _freeze_entry(
    value: Any,
    path: str,
    active_mappings: set[int],
) -> _SchemaEntry:
    if value is Ellipsis:
        return Ref().bind(path)

    if isinstance(value, Ref):
        return value.bind(path)

    if not isinstance(value, Mapping):
        raise TypeError(
            f"Invalid schema value at {path!r}: expected a mapping, Ref, or "
            "Ellipsis; use ... or Ref() to declare a default reference."
        )

    mapping_id = id(value)
    if mapping_id in active_mappings:
        raise ValueError(f"Cyclic RefFactory schema detected at {path!r}.")
    active_mappings.add(mapping_id)
    try:
        explicit = _REF_ENTRY_KEY in value
        current = value.get(_REF_ENTRY_KEY, Ref())
        if current is Ellipsis:
            current = Ref()
        if not isinstance(current, Ref):
            raise TypeError(
                f"Invalid current-entry definition at {path!r}: "
                "the empty key must contain Ref() or Ellipsis."
            )

        frozen: dict[str, Any] = {
            _REF_ENTRY_KEY: _RefDeclaration(
                ref=current.bind(path),
                explicit=explicit,
            ),
        }
        for raw_name, child in value.items():
            if raw_name == _REF_ENTRY_KEY:
                continue
            name = _validate_schema_name(raw_name, path)
            child_path = f"{path}.{name}"
            frozen[name] = _freeze_entry(child, child_path, active_mappings)
        return _freeze_mapping(frozen)
    finally:
        active_mappings.remove(mapping_id)


def _freeze_schema(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    if _REF_ENTRY_KEY in schema:
        raise ValueError("The root RefFactory schema cannot define an empty-key Ref.")

    active_mappings = {id(schema)}
    frozen: dict[str, Any] = {}
    for raw_name, value in schema.items():
        name = _validate_schema_name(raw_name, "")
        frozen[name] = _freeze_entry(value, name, active_mappings)
    return _freeze_mapping(frozen)


def _merge_entries(
    left: _SchemaEntry,
    right: _SchemaEntry,
    path: str,
    conflict: Literal["error", "replace"],
) -> _SchemaEntry:
    left_is_leaf = isinstance(left, Ref)
    right_is_leaf = isinstance(right, Ref)
    if left_is_leaf and right_is_leaf:
        if conflict == "replace":
            return cast(Ref[Any], right)
        raise ValueError(f"Conflicting Ref leaves at schema path {path!r}.")
    if left_is_leaf != right_is_leaf:
        left_kind = "leaf" if left_is_leaf else "container"
        right_kind = "leaf" if right_is_leaf else "container"
        raise ValueError(
            f"Conflicting schema structure at path {path!r}: "
            f"left is {left_kind}, right is {right_kind}."
        )

    left_container = cast(Mapping[str, Any], left)
    right_container = cast(Mapping[str, Any], right)
    left_declaration = cast(_RefDeclaration, left_container[_REF_ENTRY_KEY])
    right_declaration = cast(_RefDeclaration, right_container[_REF_ENTRY_KEY])
    if left_declaration.explicit and right_declaration.explicit:
        if conflict == "replace":
            current = right_declaration
        else:
            raise ValueError(
                f"Conflicting container Ref declarations at schema path {path!r}."
            )
    elif right_declaration.explicit:
        current = right_declaration
    else:
        current = left_declaration

    merged: dict[str, Any] = {_REF_ENTRY_KEY: current}
    for name, child in left_container.items():
        if name != _REF_ENTRY_KEY:
            merged[name] = child
    for name, child in right_container.items():
        if name == _REF_ENTRY_KEY:
            continue
        if name in merged:
            merged[name] = _merge_entries(
                merged[name], child, f"{path}.{name}", conflict
            )
        else:
            merged[name] = child
    return _freeze_mapping(merged)


def _merge_schemas(
    left: Mapping[str, Any],
    right: Mapping[str, Any],
    conflict: Literal["error", "replace"],
) -> Mapping[str, Any]:
    merged = dict(left)
    for name, entry in right.items():
        if name in merged:
            merged[name] = _merge_entries(merged[name], entry, name, conflict)
        else:
            merged[name] = entry
    return _freeze_mapping(merged)


class RefFactory:
    """Immutable attribute interface for references declared by a frozen schema."""

    __slots__ = ("__entry", "__path")
    __entry: _SchemaEntry
    __path: str

    def __init__(
        self,
        schema: Mapping[str, Any],
    ) -> None:
        if not isinstance(schema, Mapping):
            raise TypeError(
                f"RefFactory schema must be a mapping, got {type(schema).__name__}."
            )
        root = _freeze_schema(schema)
        object.__setattr__(self, "_RefFactory__entry", root)
        object.__setattr__(self, "_RefFactory__path", "")

    @classmethod
    def _from_entry(
        cls,
        *,
        entry: _SchemaEntry,
        path: str,
    ) -> RefFactory:
        factory = object.__new__(cls)
        object.__setattr__(factory, "_RefFactory__entry", entry)
        object.__setattr__(factory, "_RefFactory__path", path)
        return factory

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __getattr__(self, name: str) -> RefFactory:
        path = self.__path
        new_path = f"{path}.{name}" if path else name
        entry = self.__entry

        if isinstance(entry, Mapping) and name in entry and name != _REF_ENTRY_KEY:
            child = cast(_SchemaEntry, entry[name])
            return type(self)._from_entry(
                entry=child,
                path=new_path,
            )

        candidates = [key for key in entry if key] if isinstance(entry, Mapping) else []
        suggestion = difflib.get_close_matches(name, candidates, n=1)
        detail = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
        location = path or "<root>"
        raise AttributeError(f"Schema path {location!r} has no entry {name!r}.{detail}")

    def __call__(self) -> Ref:
        path = self.__path
        entry = self.__entry
        if not path:
            raise ValueError("Empty ref path")
        if isinstance(entry, Ref):
            return entry
        declaration = cast(_RefDeclaration, entry[_REF_ENTRY_KEY])
        return declaration.ref

    def merge(
        self,
        other: RefFactory | Mapping[str, Any],
        *,
        conflict: Literal["error", "replace"] = "error",
    ) -> RefFactory:
        """Recursively merge schemas and return a new root RefFactory."""
        if conflict not in ("error", "replace"):
            raise ValueError(f"Unknown schema conflict strategy: {conflict!r}.")
        path = self.__path
        if path:
            raise ValueError("RefFactory schema operations require the root factory.")

        left_root = cast(Mapping[str, Any], self.__entry)
        if isinstance(other, RefFactory):
            if other.__path:
                raise ValueError("The right operand must be a root RefFactory.")
            right_root = cast(Mapping[str, Any], other.__entry)
        elif isinstance(other, Mapping):
            right_root = _freeze_schema(other)
        else:
            raise TypeError(
                "RefFactory schemas can only be merged with a mapping or RefFactory."
            )

        merged = _merge_schemas(left_root, right_root, conflict)
        return type(self)._from_entry(
            entry=merged,
            path="",
        )

    def __or__(self, other: RefFactory | Mapping[str, Any]) -> RefFactory:
        return self.merge(other)

    def __repr__(self) -> str:
        return f"RefFactory(path={self.__path!r})"


RefLike = Ref | RefFactory


def to_ref(ref_like: RefLike) -> Ref:
    """Normalize a :class:`Ref` or :class:`RefFactory` into a :class:`Ref` object."""
    ref = ref_like() if isinstance(ref_like, RefFactory) else ref_like
    ref._require_bound()
    return ref


class ContextPathError(KeyError):
    """Internal exception raised when a path cannot be resolved in the context."""

    pass


@dataclass
class _ContextOperations:
    updates: dict[tuple[str, ...], Any] = field(default_factory=dict)
    drops: set[tuple[str, ...]] = field(default_factory=set)


class ContextData(dict[str, Any]):
    """
    Internal dictionary implementation used to distinguish structural elements
    from user-provided dictionary values.

    Acts as the mutable data node for Context while preserving the distinction
    between context structure and user-provided dictionary values.
    """

    __slots__ = ()

    def mutate(
        self,
        updates: dict[tuple[str, ...], Any],
        drops: set[tuple[str, ...]],
    ) -> None:
        """Validate a structural transaction, then apply it in place."""
        if () in updates or () in drops:
            raise ValueError("ContextData root mutation is not supported.")

        def group(
            current_updates: dict[tuple[str, ...], Any],
            current_drops: set[tuple[str, ...]],
        ) -> dict[str, _ContextOperations]:
            grouped: dict[str, _ContextOperations] = defaultdict(_ContextOperations)
            for path, value in current_updates.items():
                head, *tail = path
                grouped[head].updates[tuple(tail)] = value
            for path in current_drops:
                head, *tail = path
                grouped[head].drops.add(tuple(tail))
            return grouped

        def process(
            data: ContextData | None,
            current_updates: dict[tuple[str, ...], Any],
            current_drops: set[tuple[str, ...]],
            *,
            apply_changes: bool,
        ) -> None:
            for head, operations in group(current_updates, current_drops).items():
                sub_updates = operations.updates
                sub_drops = operations.drops
                if () in sub_updates:
                    if not apply_changes and len(sub_updates) > 1:
                        raise ValueError(
                            f"Update conflict at '{head}': Cannot update both parent and child simultaneously."
                        )
                    replacement = sub_updates[()]
                    if apply_changes:
                        cast(ContextData, data)[head] = replacement
                    else:
                        current = _MISSING if data is None else data.get(head, _MISSING)
                        if (
                            current is not _MISSING
                            and () not in sub_drops
                            and isinstance(current, ContextData)
                            != isinstance(replacement, ContextData)
                        ):
                            current_kind = (
                                "container"
                                if isinstance(current, ContextData)
                                else "leaf"
                            )
                            replacement_kind = (
                                "container"
                                if isinstance(replacement, ContextData)
                                else "leaf"
                            )
                            raise ContextPathError(
                                f"Cannot replace {current_kind} path '{head}' with "
                                f"a {replacement_kind} without dropping the path first."
                            )
                    continue

                child = _MISSING if data is None else data.get(head, _MISSING)
                descendant_drops = sub_drops - {()}
                if () in sub_drops:
                    if not sub_updates:
                        if apply_changes:
                            cast(ContextData, data).pop(head, None)
                        continue
                    if apply_changes:
                        child = ContextData()
                        cast(ContextData, data)[head] = child
                    else:
                        child = None

                elif child is _MISSING:
                    if not sub_updates:
                        continue
                    if apply_changes:
                        child = ContextData()
                        cast(ContextData, data)[head] = child
                    else:
                        child = None

                elif not isinstance(child, ContextData):
                    raise ContextPathError(
                        f"Path '{head}' blocked by leaf value during mutation."
                    )

                if apply_changes:
                    process(
                        cast(ContextData, child),
                        sub_updates,
                        descendant_drops,
                        apply_changes=True,
                    )
                else:
                    with enrich_exception(
                        head,
                        exc_types=(ContextPathError, ValueError),
                    ):
                        process(
                            child,
                            sub_updates,
                            descendant_drops,
                            apply_changes=False,
                        )

        process(self, updates, drops, apply_changes=False)
        process(self, updates, drops, apply_changes=True)


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
    nested: Mapping[str, ContextDiff] = field(default_factory=lambda: _EMPTY_MAPPING)

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
        default: _T2 | _Missing = _MISSING,
    ) -> Any:
        pass

    @abstractmethod
    def exists(self, ref: RefLike) -> bool:
        pass

    @abstractmethod
    def keys(self, ref: RefLike | None = None) -> Iterable[str]:
        pass

    @abstractmethod
    def to_context_data(self, ref: RefLike | None = None) -> ContextData:
        pass

    @abstractmethod
    def to_dict(self, ref: RefLike | None = None) -> dict[str, Any]:
        """Convert to standard python dictionary recursively."""
        pass

    def type_repr(self) -> str:
        return type(self).__name__

    def clone(self) -> Context:
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
        self, other: ContextElement, strategy: Literal["is", "eq"] = "is"
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
    data: InitVar[Mapping[str, Any] | None] = None

    def __post_init__(self, data: Mapping[str, Any] | None = None) -> None:
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
    def _from_context_data(cls, root: ContextData) -> Context:
        obj = object.__new__(cls)
        object.__setattr__(obj, "_root", root)
        return obj

    def extract(self, ref_tree: Any) -> Any:
        ref_tree = CTX_EVAL_ENGINE.map(to_ref, ref_tree)
        refs, treedef = CTX_EVAL_ENGINE.flatten(ref_tree)
        values = tuple(self._resolve(ref.parts) for ref in refs)

        values = tuple(
            ContextView(self, ref.parts) if isinstance(val, ContextData) else val
            for ref, val in zip(refs, values, strict=True)
        )
        return CTX_EVAL_ENGINE.unflatten(treedef, values)

    def _build_dict_ref_tree(self, ref: RefLike | None = None) -> Any:
        ref = to_ref(ref) if ref is not None else None
        data = self.to_context_data(ref)
        base_path = ref.bound_path if ref else ""

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
        default: _T2 | _Missing = _MISSING,
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

    def keys(self, ref: RefLike | None = None) -> Iterable[str]:
        if ref is None:
            return self._root.keys()
        ref = to_ref(ref)
        element = self._resolve(ref.parts)
        if isinstance(element, ContextData):
            return element.keys()
        raise ContextPathError("Cannot list keys of a leaf value.")

    def to_context_data(self, ref: RefLike | None = None) -> ContextData:
        if ref is None:
            return self._root
        ref = to_ref(ref)
        val = self._resolve(ref.parts)
        if isinstance(val, ContextData):
            return val
        raise ContextPathError("Target is not a ContextData (container).")

    def to_dict(self, ref: RefLike | None = None) -> dict[str, Any]:
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
        updates: Mapping[RefLike, Any] | None = None,
        drops: Iterable[RefLike] | None = None,
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

    def _adjust_ref(self, ref: RefLike | None) -> Ref:
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
        default: _T2 | _Missing = _MISSING,
    ) -> Any:
        return self._context.get(self._adjust_ref(ref), default)

    def exists(self, ref: RefLike) -> bool:
        return self._context.exists(self._adjust_ref(ref))

    def keys(self, ref: RefLike | None = None) -> Iterable[str]:
        return self._context.keys(self._adjust_ref(ref))

    def to_context_data(self, ref: RefLike | None = None) -> ContextData:
        return self._context.to_context_data(self._adjust_ref(ref))

    def to_dict(self, ref: RefLike | None = None) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref))


from .tree import CONTEXT_ENGINE, CTX_EVAL_ENGINE
