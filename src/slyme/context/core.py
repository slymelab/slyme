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
import weakref
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field, replace
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

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_EMPTY_MAPPING: Mapping[str, Any] = types.MappingProxyType({})
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


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
    """Immutable dotted ref, or an unbound declaration for Schema."""

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
            raise ValueError("Ref has no path; bind it through Schema or Ref.bind().")
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
_SCHEMA_RESERVED_NAMES = frozenset({"from_refs", "merge"})


def _freeze_mapping(data: Mapping[str, Any]) -> Mapping[str, Any]:
    """Snapshot a mapping behind the current immutable mapping implementation."""
    return types.MappingProxyType(dict(data))


@dataclass(frozen=True)
class _RefDeclaration:
    ref: Ref[Any]
    explicit: bool


_SchemaEntry = Ref[Any] | Mapping[str, Any]


def _validate_ref_name(name: Any, path: str) -> str:
    if not isinstance(name, str):
        raise TypeError(
            f"Invalid Schema key at {path or '<root>'}: expected str, "
            f"got {type(name).__name__}."
        )
    if not name or "." in name or not name.isidentifier() or keyword.iskeyword(name):
        raise ValueError(
            f"Invalid Schema key {name!r} at {path or '<root>'}; "
            "keys must be non-keyword Python identifiers without dots."
        )
    if name.startswith("_") or name in _SCHEMA_RESERVED_NAMES:
        raise ValueError(f"Schema key {name!r} at {path or '<root>'} is reserved.")
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
            f"Invalid Schema declaration at {path!r}: expected a mapping, Ref, or "
            "Ellipsis; use ... or Ref() to declare a default reference."
        )

    mapping_id = id(value)
    if mapping_id in active_mappings:
        raise ValueError(f"Cyclic Schema declarations detected at {path!r}.")
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
            name = _validate_ref_name(raw_name, path)
            child_path = f"{path}.{name}"
            frozen[name] = _freeze_entry(child, child_path, active_mappings)
        return _freeze_mapping(frozen)
    finally:
        active_mappings.remove(mapping_id)


def _freeze_schema(declarations: Mapping[str, Any]) -> Mapping[str, Any]:
    if _REF_ENTRY_KEY in declarations:
        raise ValueError("The root Schema declaration cannot define an empty-key Ref.")

    active_mappings = {id(declarations)}
    frozen: dict[str, Any] = {}
    for raw_name, value in declarations.items():
        name = _validate_ref_name(raw_name, "")
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
        raise ValueError(f"Conflicting Ref declarations at path {path!r}.")
    if left_is_leaf != right_is_leaf:
        left_kind = "leaf" if left_is_leaf else "container"
        right_kind = "leaf" if right_is_leaf else "container"
        raise ValueError(
            f"Conflicting Schema structure at path {path!r}: "
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
                f"Conflicting container Ref declarations at path {path!r}."
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


def _merge_schema(
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


class _SchemaState:
    """Backing state shared by views of one Schema declaration tree."""

    __slots__ = ("entry", "fragments", "mutable")

    def __init__(
        self,
        entry: Mapping[str, Any],
        *,
        mutable: bool = False,
        fragments: Iterable[Schema] = (),
    ) -> None:
        self.entry = entry
        self.fragments = list(fragments)
        self.mutable = mutable


class Schema:
    """Hierarchical attribute interface for Ref declarations."""

    __slots__ = ("__path", "__state")
    __path: tuple[str, ...]
    __state: _SchemaState

    def __init__(self, declarations: Mapping[str, Any]) -> None:
        if not isinstance(declarations, Mapping):
            raise TypeError(
                "Schema declarations must be a mapping, "
                f"got {type(declarations).__name__}."
            )
        state = _SchemaState(_freeze_schema(declarations))
        object.__setattr__(self, "_Schema__state", state)
        object.__setattr__(self, "_Schema__path", ())

    @classmethod
    def from_refs(cls, refs: Iterable[RefLike]) -> Schema:
        """Build a declaration tree from bound references."""
        tree: dict[str, Any] = {}
        for ref_like in refs:
            ref = to_ref(ref_like)
            current = tree
            for part in ref.parts:
                current = current.setdefault(part, {})
            current.setdefault(_REF_ENTRY_KEY, ref)

        def collapse(branch: dict[str, Any]) -> dict[str, Any] | Ref[Any]:
            declaration = branch.get(_REF_ENTRY_KEY)
            children = {
                name: collapse(child)
                for name, child in branch.items()
                if name != _REF_ENTRY_KEY
            }
            if declaration is not None and not children:
                return cast(Ref[Any], declaration)
            if declaration is not None:
                children[_REF_ENTRY_KEY] = declaration
            return children

        return cls(cast(dict[str, Any], collapse(tree)))

    @classmethod
    def _from_state(
        cls,
        state: _SchemaState,
        path: tuple[str, ...],
    ) -> Schema:
        schema = object.__new__(cls)
        object.__setattr__(schema, "_Schema__state", state)
        object.__setattr__(schema, "_Schema__path", path)
        return schema

    @classmethod
    def _application(cls, initial: Schema | None) -> Schema:
        if initial is None:
            entry: Mapping[str, Any] = _freeze_mapping({})
            fragments: tuple[Schema, ...] = ()
        else:
            if not isinstance(initial, Schema):
                raise TypeError(
                    f"Context schema must be Schema, got {type(initial).__name__}."
                )
            initial._require_root()
            entry = initial.__state.entry
            fragments = (initial,)
        return cls._from_state(
            _SchemaState(entry, mutable=True, fragments=fragments),
            (),
        )

    def _entry(self) -> _SchemaEntry:
        entry: _SchemaEntry = self.__state.entry
        for part in self.__path:
            entry = cast(Mapping[str, _SchemaEntry], entry)[part]
        return entry

    def _require_root(self) -> None:
        if self.__path:
            raise ValueError("Schema declaration operations require the root object.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} is immutable")

    def __getattr__(self, name: str) -> Schema:
        path = self.__path
        entry = self._entry()

        if isinstance(entry, Mapping) and name in entry and name != _REF_ENTRY_KEY:
            return type(self)._from_state(self.__state, (*path, name))

        candidates = [key for key in entry if key] if isinstance(entry, Mapping) else []
        suggestion = difflib.get_close_matches(name, candidates, n=1)
        detail = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
        location = ".".join(path) or "<root>"
        raise AttributeError(f"Schema path {location!r} has no entry {name!r}.{detail}")

    def __call__(self) -> Ref:
        path = self.__path
        entry = self._entry()
        if not path:
            raise ValueError("Empty ref path")
        if isinstance(entry, Ref):
            return entry
        declaration = cast(_RefDeclaration, entry[_REF_ENTRY_KEY])
        return declaration.ref

    def merge(
        self,
        other: Schema | Mapping[str, Any],
        *,
        conflict: Literal["error", "replace"] = "error",
    ) -> Schema:
        """Merge Ref declarations into a new immutable root Schema object."""
        if conflict not in ("error", "replace"):
            raise ValueError(f"Unknown Schema conflict strategy: {conflict!r}.")
        self._require_root()

        left_root = self.__state.entry
        if isinstance(other, Schema):
            other._require_root()
            right_root = other.__state.entry
        elif isinstance(other, Mapping):
            right_root = _freeze_schema(other)
        else:
            raise TypeError(
                "Schema declarations can only be merged with a mapping or Schema."
            )

        merged = _merge_schema(left_root, right_root, conflict)
        return type(self)._from_state(
            _SchemaState(merged),
            (),
        )

    def _declare(self, fragment: Schema) -> None:
        self._require_root()
        if not self.__state.mutable:
            raise TypeError("Only Context-owned Schema can receive declarations.")
        if not isinstance(fragment, Schema):
            raise TypeError(
                f"Context declarations must be Schema, got {type(fragment).__name__}."
            )
        fragment._require_root()
        if fragment is self or any(
            fragment is declared for declared in self.__state.fragments
        ):
            return
        self.__state.entry = _merge_schema(
            self.__state.entry,
            fragment.__state.entry,
            "error",
        )
        self.__state.fragments.append(fragment)

    def _contains_ref(self, ref: Ref[Any]) -> bool:
        self._require_root()
        entry: _SchemaEntry = self.__state.entry
        for part in ref.parts:
            if not isinstance(entry, Mapping) or part not in entry:
                return False
            entry = cast(_SchemaEntry, entry[part])
        return bool(ref.parts)

    def __or__(self, other: Schema | Mapping[str, Any]) -> Schema:
        return self.merge(other)

    def __repr__(self) -> str:
        return f"Schema(path={'.'.join(self.__path)!r})"


RefLike = Ref | Schema


def to_ref(ref_like: RefLike) -> Ref:
    """Normalize a :class:`Ref` or :class:`Schema` into a :class:`Ref` object."""
    if not isinstance(ref_like, (Ref, Schema)):
        raise TypeError(f"Expected Ref or Schema, got {type(ref_like).__name__}.")
    ref = ref_like() if isinstance(ref_like, Schema) else ref_like
    ref._require_bound()
    return ref


class ContextPathError(KeyError):
    """Raised when a Context path cannot be resolved or changed as requested."""

    pass


@dataclass(frozen=True, eq=False)
class _Value:
    """Private wrapper for every user-provided Context leaf."""

    payload: Any
    replaceable: bool = True


_Tree = dict[str, Any]


def _mutate_tree(
    tree: _Tree,
    updates: dict[tuple[str, ...], _Value],
    drops: set[tuple[str, ...]],
) -> None:
    """Validate and apply one structural transaction, pruning empty containers."""
    if () in updates or () in drops:
        raise ValueError("Context root mutation is not supported.")

    def group(
        current_updates: dict[tuple[str, ...], _Value],
        current_drops: set[tuple[str, ...]],
    ) -> dict[
        str,
        tuple[dict[tuple[str, ...], _Value], set[tuple[str, ...]]],
    ]:
        grouped: dict[
            str,
            tuple[dict[tuple[str, ...], _Value], set[tuple[str, ...]]],
        ] = {}
        for path, value in current_updates.items():
            head, *tail = path
            sub_updates, _ = grouped.setdefault(head, ({}, set()))
            sub_updates[tuple(tail)] = value
        for path in current_drops:
            head, *tail = path
            _, sub_drops = grouped.setdefault(head, ({}, set()))
            sub_drops.add(tuple(tail))
        return grouped

    def process(
        data: _Tree | None,
        current_updates: dict[tuple[str, ...], _Value],
        current_drops: set[tuple[str, ...]],
        *,
        apply_changes: bool,
    ) -> None:
        for head, (sub_updates, sub_drops) in group(
            current_updates, current_drops
        ).items():
            if () in sub_updates:
                if not apply_changes and len(sub_updates) > 1:
                    raise ValueError(
                        f"Update conflict at '{head}': Cannot update both parent and child simultaneously."
                    )
                replacement = sub_updates[()]
                if apply_changes:
                    cast(_Tree, data)[head] = replacement
                else:
                    current = _MISSING if data is None else data.get(head, _MISSING)
                    if isinstance(current, dict) and () not in sub_drops:
                        raise ContextPathError(
                            f"Cannot replace container path '{head}' with a leaf "
                            "without dropping the path first."
                        )
                    if (
                        isinstance(current, _Value)
                        and not current.replaceable
                        and () not in sub_drops
                    ):
                        raise ContextPathError(
                            f"Cannot replace added path '{head}'; delete it or "
                            "write through a forked Context."
                        )
                continue

            child = _MISSING if data is None else data.get(head, _MISSING)
            descendant_drops = sub_drops - {()}
            if () in sub_drops:
                if not sub_updates:
                    if apply_changes:
                        cast(_Tree, data).pop(head, None)
                    continue
                if apply_changes:
                    child = {}
                    cast(_Tree, data)[head] = child
                else:
                    child = None
            elif child is _MISSING:
                if not sub_updates:
                    continue
                if apply_changes:
                    child = {}
                    cast(_Tree, data)[head] = child
                else:
                    child = None
            elif not isinstance(child, dict):
                raise ContextPathError(
                    f"Path '{head}' blocked by leaf value during mutation."
                )

            if apply_changes:
                child_tree = cast(_Tree, child)
                process(
                    child_tree,
                    sub_updates,
                    descendant_drops,
                    apply_changes=True,
                )
                if not child_tree:
                    cast(_Tree, data).pop(head, None)
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

    process(tree, updates, drops, apply_changes=False)
    process(tree, updates, drops, apply_changes=True)


def _contains_context_identity(
    values: Iterable[Context],
    target: Context,
) -> bool:
    return any(value is target for value in values)


def _merge_context_mro(parents: tuple[Context, ...]) -> tuple[Context, ...]:
    """Merge immutable Context parent chains using C3."""
    pending = [list(parent.mro) for parent in parents]
    pending.append(list(parents))
    result: list[Context] = []

    while True:
        pending = [sequence for sequence in pending if sequence]
        if not pending:
            return tuple(result)

        candidate = next(
            (
                sequence[0]
                for sequence in pending
                if not any(
                    _contains_context_identity(other[1:], sequence[0])
                    for other in pending
                )
            ),
            None,
        )
        if candidate is None:
            raise TypeError("Cannot create a consistent Context C3 linearization.")

        result.append(candidate)
        for sequence in pending:
            if sequence and sequence[0] is candidate:
                sequence.pop(0)


_ContextResolved = _Value | tuple[_Tree, ...]


def _context_keys(trees: tuple[_Tree, ...]) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for tree in trees:
        for key in tree:
            if key not in seen:
                seen.add(key)
                result.append(key)
    return tuple(result)


def _select_context_child(
    trees: tuple[_Tree, ...],
    key: str,
    path: tuple[str, ...],
) -> _ContextResolved:
    selected: list[_Tree] = []
    for tree in trees:
        child = tree.get(key, _MISSING)
        if child is _MISSING:
            continue
        if isinstance(child, _Value):
            if not selected:
                return child
            break
        if not isinstance(child, dict):
            raise TypeError("Context contains an unwrapped leaf value.")
        selected.append(child)

    if selected:
        return tuple(selected)
    raise ContextPathError(".".join(path))


def _snapshot_trees(trees: tuple[_Tree, ...]) -> _Tree:
    snapshot: _Tree = {}
    for key in _context_keys(trees):
        child = _select_context_child(trees, key, (key,))
        if isinstance(child, _Value):
            snapshot[key] = _Value(child.payload)
        else:
            snapshot[key] = _snapshot_trees(child)
    return snapshot


def _tree_to_dict(data: _Tree) -> dict[str, Any]:
    return {
        key: (
            value.payload
            if isinstance(value, _Value)
            else _tree_to_dict(cast(_Tree, value))
        )
        for key, value in data.items()
    }


class ContextElement(ABC):
    """
    Abstract base class for context-related entities (Context, ContextView).
    """

    __slots__ = ()

    @abstractmethod
    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        pass

    @abstractmethod
    def get(
        self,
        ref: RefLike,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        pass

    @abstractmethod
    def exists(self, ref: RefLike, *, local: bool = False) -> bool:
        pass

    @abstractmethod
    def keys(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        pass

    @abstractmethod
    def _snapshot_tree(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        pass

    @abstractmethod
    def to_dict(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        """Convert to standard python dictionary recursively."""
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

    def flatten(self, *, local: bool = False) -> dict[Ref[Any], Any]:
        """Return the visible Context leaves as a flat Ref-to-value mapping."""
        leaves: dict[Ref[Any], Any] = {}

        def collect(tree: _Tree, prefix: tuple[str, ...]) -> None:
            for key, value in tree.items():
                path = (*prefix, key)
                if isinstance(value, _Value):
                    leaves[Ref(".".join(path))] = value.payload
                else:
                    collect(cast(_Tree, value), path)

        collect(self._snapshot_tree(local=local), ())
        return leaves


@dataclass(frozen=True, repr=False, eq=False, init=False)
class Context(ContextElement):
    """Declared runtime data with local writes and live C3 parent lookup."""

    parents: tuple[Context, ...] = field(init=False)
    _data: _Tree = field(init=False)
    _mro: tuple[Context, ...] = field(init=False)
    _schema: Schema | None = field(init=False)

    def __init__(
        self,
        data: Mapping[RefLike, Any] | None = None,
        *,
        schema: Schema | None = None,
        parents: tuple[Context, ...] = (),
    ) -> None:
        direct_parents = tuple(parents)
        if any(not isinstance(parent, Context) for parent in direct_parents):
            raise TypeError("Context parents must be Context objects.")
        if any(
            left is right
            for index, left in enumerate(direct_parents)
            for right in direct_parents[index + 1 :]
        ):
            raise TypeError("A Context cannot contain duplicate direct parents.")

        if direct_parents:
            if schema is not None:
                raise TypeError(
                    "A child Context inherits its schema and cannot provide one "
                    "during construction."
                )
            application_root = direct_parents[0].root
            if any(
                parent.root is not application_root for parent in direct_parents[1:]
            ):
                raise TypeError(
                    "Context parents must belong to the same application root."
                )
            owned_schema = None
        else:
            owned_schema = Schema._application(schema)

        object.__setattr__(self, "parents", direct_parents)
        object.__setattr__(self, "_data", {})
        object.__setattr__(self, "_schema", owned_schema)
        object.__setattr__(self, "_mro", (self, *_merge_context_mro(direct_parents)))
        if data is not None:
            if not isinstance(data, Mapping):
                raise TypeError(
                    f"Context data must be a mapping, got {type(data).__name__}."
                )
            self.update(data)

    @property
    def root(self) -> Context:
        """Return the application root shared by this Context hierarchy."""
        return self._mro[-1]

    @property
    def mro(self) -> tuple[Context, ...]:
        """Return this Context followed by its C3-linearized ancestors."""
        return self._mro

    @property
    def schema(self) -> Schema:
        """Return the live Schema owned by this Context hierarchy's root."""
        return cast(Schema, self.root._schema)

    def declare(self, fragment: Schema) -> None:
        """Add one immutable declaration fragment to this application's schema."""
        self.schema._declare(fragment)

    def _validate_ref(self, ref_like: RefLike) -> Ref[Any]:
        ref = to_ref(ref_like)
        if not self.schema._contains_ref(ref):
            raise ContextPathError(
                f"Ref path '{ref.bound_path}' is not declared by this Context."
            )
        return ref

    def fork(self, *mixins: Context) -> Context:
        """Create an empty child inheriting this Context and optional mixins."""
        return type(self)(parents=(self, *mixins))

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        ref_tree = CTX_EVAL_ENGINE.map(self._validate_ref, ref_tree)
        refs, treedef = CTX_EVAL_ENGINE.flatten(ref_tree)
        values = tuple(
            (
                resolved.payload
                if isinstance(resolved, _Value)
                else ContextView(self, ref.parts)
            )
            for ref in refs
            for resolved in (self._resolve(ref.parts, local=local),)
        )
        return CTX_EVAL_ENGINE.unflatten(treedef, values)

    # --- Read Operations ---
    def get(
        self,
        ref: RefLike,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        ref = self._validate_ref(ref)
        try:
            return self.extract(ref, local=local)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: RefLike, *, local: bool = False) -> bool:
        ref = self._validate_ref(ref)
        try:
            self._resolve(ref.parts, local=local)
            return True
        except ContextPathError:
            return False

    def keys(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        parts = () if ref is None else self._validate_ref(ref).parts
        element = self._resolve(parts, local=local)
        if isinstance(element, tuple):
            return _context_keys(element)
        raise ContextPathError("Cannot list keys of a leaf value.")

    def _snapshot_tree(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        parts = () if ref is None else self._validate_ref(ref).parts
        val = self._resolve(parts, local=local)
        if isinstance(val, tuple):
            return _snapshot_trees(val)
        raise ContextPathError("Target is not a Context container.")

    def to_dict(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return _tree_to_dict(self._snapshot_tree(ref, local=local))

    def _resolve(
        self,
        parts: Iterable[str],
        *,
        local: bool = False,
    ) -> _ContextResolved:
        path = tuple(parts)
        lineage = (self,) if local else self._mro
        trees = tuple(context._data for context in lineage)
        if not path:
            return trees

        for index, part in enumerate(path):
            resolved = _select_context_child(
                trees,
                part,
                path[: index + 1],
            )
            if isinstance(resolved, _Value):
                if index != len(path) - 1:
                    prefix = ".".join(path[: index + 1])
                    raise ContextPathError(f"Path '{prefix}' blocked by leaf value.")
                return resolved
            trees = resolved
        return trees

    # --- Unified Modification Interface ---
    def _mutate_raw(
        self,
        updates: Mapping[tuple[str, ...], _Value],
        drops: Iterable[tuple[str, ...]],
    ) -> None:
        _mutate_tree(self._data, dict(updates), set(drops))

    def mutate(
        self,
        *,
        updates: Mapping[RefLike, Any] | None = None,
        drops: Iterable[RefLike] | None = None,
    ) -> None:
        """
        Apply local updates and drops atomically.

        Args:
            updates: A mapping of References to new values.
            drops: An iterable of References to remove.

        The mutation is atomic and returns ``None``.
        """
        if not updates and not drops:
            return None

        normalized_updates: dict[Ref[Any], Any] = (
            {self._validate_ref(k): v for k, v in updates.items()} if updates else {}
        )
        normalized_drops: set[Ref[Any]] = (
            {self._validate_ref(r) for r in drops} if drops else set()
        )
        raw_updates: dict[tuple[str, ...], _Value] = {
            r.parts: _Value(v) for r, v in normalized_updates.items()
        }
        raw_drops: set[tuple[str, ...]] = {r.parts for r in normalized_drops}

        self._mutate_raw(raw_updates, raw_drops)

    # --- Convenience Interfaces ---
    def update(self, updates: Mapping[RefLike, Any]) -> None:
        """Set several local bindings atomically."""
        self.mutate(updates=updates)

    def drop(self, refs: Iterable[RefLike]) -> None:
        """Delete several local paths atomically."""
        self.mutate(drops=refs)

    def set(self, ref: RefLike, value: _T) -> None:
        """Set one local binding."""
        ref = self._validate_ref(ref)
        self.mutate(updates={ref: value})

    def add(self, ref: RefLike, value: _T) -> Callable[[], None]:
        """Add an immutable local binding and return its exact disposer."""
        ref = self._validate_ref(ref)
        try:
            self._resolve(ref.parts, local=True)
        except ContextPathError:
            pass
        else:
            raise ContextPathError(
                f"Cannot add existing local path '{ref.bound_path}'."
            )

        entry = _Value(value, replaceable=False)
        self._mutate_raw({ref.parts: entry}, ())

        context_ref = weakref.ref(self)
        entry_ref = weakref.ref(entry)
        dispose_ref: Ref[Any] = Ref(ref.bound_path)

        def dispose() -> None:
            context = context_ref()
            expected = entry_ref()
            if context is None or expected is None:
                return
            try:
                current_entry = context._resolve(dispose_ref.parts, local=True)
            except ContextPathError:
                return
            if current_entry is not expected:
                return

            context.delete(dispose_ref)

        return dispose

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
        """Delete one local path."""
        ref = self._validate_ref(ref)
        self.mutate(drops=[ref])


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
            if isinstance(obj, (Ref, Schema)):
                return self._adjust_ref(obj)
            return obj

        return CTX_EVAL_ENGINE.map(adjust, ref_tree)

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        return self._context.extract(
            self._adjust_ref_tree(ref_tree),
            local=local,
        )

    def get(
        self,
        ref: RefLike,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        return self._context.get(self._adjust_ref(ref), default, local=local)

    def exists(self, ref: RefLike, *, local: bool = False) -> bool:
        return self._context.exists(self._adjust_ref(ref), local=local)

    def keys(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        return self._context.keys(self._adjust_ref(ref), local=local)

    def _snapshot_tree(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        return self._context._snapshot_tree(self._adjust_ref(ref), local=local)

    def to_dict(
        self,
        ref: RefLike | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref), local=local)


from .tree import CTX_EVAL_ENGINE
