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
import types
import weakref
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Literal, TypeVar, cast, overload

from typing_extensions import Self

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


def _split_ref_path(path: str) -> tuple[str, ...]:
    if not isinstance(path, str):
        raise TypeError(f"Ref path must be str, got {type(path).__name__}.")
    if not path:
        raise ValueError("Ref path cannot be empty.")
    parts = tuple(path.split("."))
    if any(not part for part in parts):
        raise ValueError(f"Invalid Ref path: {path!r}.")
    return parts


@dataclass(frozen=True, repr=False, eq=False)
class Ref(Generic[_T]):
    """Immutable Context dependency handle for one dotted path."""

    path: str
    metadata: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)
    value_type: Any | None = None
    parts: tuple[str, ...] = field(init=False)
    hash: int = field(init=False)

    def __post_init__(self) -> None:
        parts = _split_ref_path(self.path)
        object.__setattr__(
            self,
            "metadata",
            types.MappingProxyType(dict(self.metadata)),
        )
        object.__setattr__(self, "parts", parts)
        object.__setattr__(self, "hash", hash(parts))

    def __hash__(self) -> int:
        return self.hash

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Ref) and self.parts == other.parts

    def __repr__(self) -> str:
        items = [f"path={self.path!r}"]
        if self.value_type is not None:
            items.append(f"value_type={self.value_type!r}")
        if self.metadata:
            items.append(f"metadata={self.metadata!r}")
        return f"{type(self).__name__}({', '.join(items)})"


def _make_ref(
    path: str,
    value_type: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> Ref[Any]:
    return Ref(path, metadata or _EMPTY_MAPPING, value_type)


@dataclass(frozen=True, repr=False)
class _RefDeclaration(Generic[_T]):
    """Path-free declaration consumed by :class:`Schema`."""

    value_type: Any | None = None
    metadata: Mapping[str, Any] = field(default_factory=lambda: _EMPTY_MAPPING)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "metadata",
            types.MappingProxyType(dict(self.metadata)),
        )

    def _bind(self, path: str) -> Ref[_T]:
        return cast(Ref[_T], _make_ref(path, self.value_type, self.metadata))

    def __repr__(self) -> str:
        items = []
        if self.value_type is not None:
            items.append(f"value_type={self.value_type!r}")
        if self.metadata:
            items.append(f"metadata={self.metadata!r}")
        return f"ref({', '.join(items)})"


@overload
def ref(
    value_type: type[_T],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> _RefDeclaration[_T]: ...
@overload
def ref(
    value_type: None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> _RefDeclaration[Any]: ...
def ref(
    value_type: Any | None = None,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> _RefDeclaration[Any]:
    """Declare one Schema leaf without assigning its path directly."""
    return _RefDeclaration(value_type, metadata or _EMPTY_MAPPING)


_REF_ENTRY_KEY = ""
_SchemaContainer = dict[str, Any]
_SchemaEntry = Ref[Any] | _SchemaContainer


def _validate_ref_name(name: Any, path: str) -> str:
    if not isinstance(name, str):
        raise TypeError(
            f"Invalid Schema key at {path or '<root>'}: expected str, "
            f"got {type(name).__name__}."
        )
    if not name or "." in name:
        raise ValueError(
            f"Invalid Schema key {name!r} at {path or '<root>'}; "
            "keys must be non-empty strings without dots."
        )
    return name


def _build_schema_entry(
    value: Any,
    path: str,
    active_mappings: set[int],
) -> _SchemaEntry:
    if isinstance(value, _RefDeclaration):
        return value._bind(path)
    if not isinstance(value, Mapping):
        raise TypeError(
            f"Invalid Schema declaration at {path!r}: expected a mapping or ref(), "
            f"got {type(value).__name__}."
        )

    mapping_id = id(value)
    if mapping_id in active_mappings:
        raise ValueError(f"Cyclic Schema declarations detected at {path!r}.")
    active_mappings.add(mapping_id)
    try:
        result: _SchemaContainer = {}
        if _REF_ENTRY_KEY in value:
            current = value[_REF_ENTRY_KEY]
            if not isinstance(current, _RefDeclaration):
                raise TypeError(
                    f"Invalid current-entry declaration at {path!r}: "
                    "the empty key must contain ref()."
                )
            result[_REF_ENTRY_KEY] = current._bind(path)

        for raw_name, child in value.items():
            if raw_name == _REF_ENTRY_KEY:
                continue
            name = _validate_ref_name(raw_name, path)
            child_path = f"{path}.{name}"
            result[name] = _build_schema_entry(child, child_path, active_mappings)
        return result
    finally:
        active_mappings.remove(mapping_id)


def _build_schema(declarations: Mapping[str, Any]) -> _SchemaContainer:
    if _REF_ENTRY_KEY in declarations:
        raise ValueError(
            "The root Schema declaration cannot define an empty-key ref()."
        )

    active_mappings = {id(declarations)}
    result: _SchemaContainer = {}
    for raw_name, value in declarations.items():
        name = _validate_ref_name(raw_name, "")
        result[name] = _build_schema_entry(value, name, active_mappings)
    return result


def _merge_entries(
    left: _SchemaEntry,
    right: _SchemaEntry,
    path: str,
) -> _SchemaEntry:
    left_is_leaf = isinstance(left, Ref)
    right_is_leaf = isinstance(right, Ref)
    if left_is_leaf and right_is_leaf:
        left_ref = cast(Ref[Any], left)
        right_ref = cast(Ref[Any], right)
        if (
            left_ref.value_type == right_ref.value_type
            and left_ref.metadata == right_ref.metadata
        ):
            return left_ref
        raise ValueError(f"Conflicting Ref declarations at path {path!r}.")
    if left_is_leaf != right_is_leaf:
        left_kind = "leaf" if left_is_leaf else "container"
        right_kind = "leaf" if right_is_leaf else "container"
        raise ValueError(
            f"Conflicting Schema structure at path {path!r}: "
            f"left is {left_kind}, right is {right_kind}."
        )

    left_container = cast(_SchemaContainer, left)
    right_container = cast(_SchemaContainer, right)
    left_container_ref = cast(Ref[Any] | None, left_container.get(_REF_ENTRY_KEY))
    right_container_ref = cast(Ref[Any] | None, right_container.get(_REF_ENTRY_KEY))
    current_ref: Ref[Any] | None
    if left_container_ref is not None and right_container_ref is not None:
        if (
            left_container_ref.value_type != right_container_ref.value_type
            or left_container_ref.metadata != right_container_ref.metadata
        ):
            raise ValueError(
                f"Conflicting container Ref declarations at path {path!r}."
            )
        current_ref = left_container_ref
    elif right_container_ref is not None:
        current_ref = right_container_ref
    else:
        current_ref = left_container_ref

    merged: _SchemaContainer = {}
    if current_ref is not None:
        merged[_REF_ENTRY_KEY] = current_ref
    for name, child in left_container.items():
        if name != _REF_ENTRY_KEY:
            merged[name] = child
    for name, child in right_container.items():
        if name == _REF_ENTRY_KEY:
            continue
        if name in merged:
            merged[name] = _merge_entries(merged[name], child, f"{path}.{name}")
        else:
            merged[name] = child
    return merged


def _merge_schema(
    left: _SchemaContainer,
    right: _SchemaContainer,
) -> _SchemaContainer:
    merged = dict(left)
    for name, entry in right.items():
        if name in merged:
            merged[name] = _merge_entries(merged[name], entry, name)
        else:
            merged[name] = entry
    return merged


class Schema:
    """Mutable, monotonic declaration tree for Context paths."""

    __slots__ = ("__data",)
    __data: _SchemaContainer

    def __init__(self, declarations: Mapping[str, Any] | None = None) -> None:
        if declarations is None:
            declarations = {}
        if not isinstance(declarations, Mapping):
            raise TypeError(
                "Schema declarations must be a mapping, "
                f"got {type(declarations).__name__}."
            )
        object.__setattr__(self, "_Schema__data", _build_schema(declarations))

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"{type(self).__name__} attributes are read-only; use declare()."
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"{type(self).__name__} declarations cannot be deleted.")

    def _resolve_entry(self, path: str) -> _SchemaEntry:
        parts = _split_ref_path(path)
        entry: _SchemaEntry = self.__data
        for index, part in enumerate(parts):
            if not isinstance(entry, dict) or part not in entry:
                candidates = (
                    [key for key in entry if key] if isinstance(entry, dict) else []
                )
                suggestion = difflib.get_close_matches(part, candidates, n=1)
                detail = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
                parent = ".".join(parts[:index]) or "<root>"
                raise KeyError(f"Schema path {parent!r} has no entry {part!r}.{detail}")
            entry = cast(_SchemaEntry, entry[part])
        return entry

    def resolve(self, path: str) -> Ref[Any]:
        """Return the immutable Ref declared at *path*."""
        entry = self._resolve_entry(path)
        if isinstance(entry, Ref):
            return entry
        current = entry.get(_REF_ENTRY_KEY)
        if current is not None:
            return cast(Ref[Any], current)
        return _make_ref(path)

    def declare(self, declarations: Schema | Mapping[str, Any]) -> Self:
        """Add declarations atomically without changing existing declarations."""
        left_root = self.__data
        if isinstance(declarations, Schema):
            if declarations is self:
                return self
            right_root = declarations.__data
        elif isinstance(declarations, Mapping):
            right_root = _build_schema(declarations)
        else:
            raise TypeError(
                "Schema declarations must be a mapping or Schema, "
                f"got {type(declarations).__name__}."
            )

        self.__data.update(_merge_schema(left_root, right_root))
        return self

    def _kind_for_ref(
        self,
        ref: Ref[Any],
    ) -> Literal["leaf", "container"] | None:
        """Return the declared structural kind of a path."""
        try:
            entry = self._resolve_entry(ref.path)
        except (KeyError, TypeError, ValueError):
            return None
        if isinstance(entry, Ref):
            return "leaf"
        return "container"

    def __repr__(self) -> str:
        return "Schema()"


ContextKey = str | Ref[Any]
_RefRole = Literal["any", "leaf", "container"]


class ContextPathError(KeyError):
    """Raised when a Context path cannot be resolved or changed as requested."""

    pass


@dataclass(frozen=True, eq=False)
class _Value:
    """Private wrapper for every user-provided Context leaf."""

    payload: Any
    replaceable: bool = True


_Tree = dict[str, Any]


def _tree_entry(tree: _Tree, path: tuple[str, ...]) -> _Tree | _Value | _Missing:
    current = tree
    for index, part in enumerate(path):
        child = current.get(part, _MISSING)
        if child is _MISSING:
            return _MISSING
        if index == len(path) - 1:
            return cast(_Tree | _Value, child)
        if not isinstance(child, dict):
            raise TypeError("Context data conflicts with its Schema structure.")
        current = child
    raise ValueError("Context root mutation is not supported.")


def _drop_tree_entry(tree: _Tree, path: tuple[str, ...]) -> None:
    head, *tail = path
    if not tail:
        tree.pop(head, None)
        return

    child = tree.get(head)
    if child is None:
        return
    if not isinstance(child, dict):
        raise TypeError("Context data conflicts with its Schema structure.")
    _drop_tree_entry(child, tuple(tail))
    if not child:
        tree.pop(head)


def _set_tree_entry(tree: _Tree, path: tuple[str, ...], value: _Value) -> None:
    head, *tail = path
    if not tail:
        tree[head] = value
        return

    child = tree.get(head)
    if child is None:
        child = {}
        tree[head] = child
    elif not isinstance(child, dict):
        raise TypeError("Context data conflicts with its Schema structure.")
    _set_tree_entry(child, tuple(tail), value)


def _mutate_tree(
    tree: _Tree,
    updates: dict[tuple[str, ...], _Value],
    drops: set[tuple[str, ...]],
) -> None:
    """Apply one validated transaction and prune empty data branches."""
    if () in updates or () in drops:
        raise ValueError("Context root mutation is not supported.")

    for path in drops:
        _tree_entry(tree, path)

    for path in updates:
        current = _tree_entry(tree, path)
        is_dropped = any(path[: len(drop)] == drop for drop in drops)
        if isinstance(current, _Value) and not current.replaceable and not is_dropped:
            raise ContextPathError(
                f"Cannot replace added path '{'.'.join(path)}'; delete it or "
                "write through a forked Context."
            )
        if isinstance(current, dict):
            raise TypeError("Context data conflicts with its Schema structure.")

    for path in drops:
        _drop_tree_entry(tree, path)
    for path, value in updates.items():
        _set_tree_entry(tree, path, value)


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
            if selected:
                raise TypeError("Context data conflicts with its Schema structure.")
            return child
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
            snapshot[key] = child
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
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        pass

    @abstractmethod
    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        pass

    @abstractmethod
    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        pass

    @abstractmethod
    def _snapshot_tree(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        pass

    @abstractmethod
    def to_dict(
        self,
        ref: ContextKey | None = None,
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
            val: Any = self.get(key)
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
                    leaves[self._absolute_ref(path)] = value.payload
                else:
                    collect(cast(_Tree, value), path)

        collect(self._snapshot_tree(local=local), ())
        return leaves

    @abstractmethod
    def _absolute_ref(self, parts: tuple[str, ...]) -> Ref[Any]:
        """Resolve parts relative to this element into an absolute Ref."""
        pass


@dataclass(frozen=True, repr=False, eq=False, init=False)
class Context(ContextElement):
    """Declared runtime data with local writes and live C3 parent lookup."""

    parents: tuple[Context, ...] = field(init=False)
    _data: _Tree = field(init=False)
    _mro: tuple[Context, ...] = field(init=False)
    _schema: Schema | None = field(init=False)

    def __init__(
        self,
        data: Mapping[ContextKey, Any] | None = None,
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
            root_schema = None
        else:
            if schema is None:
                root_schema = Schema()
            elif isinstance(schema, Schema):
                root_schema = schema
            else:
                raise TypeError(
                    f"Context schema must be Schema, got {type(schema).__name__}."
                )

        object.__setattr__(self, "parents", direct_parents)
        object.__setattr__(self, "_data", {})
        object.__setattr__(self, "_schema", root_schema)
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
        """Return the live Schema shared by this Context hierarchy."""
        return cast(Schema, self.root._schema)

    def declare(self, declarations: Schema | Mapping[str, Any]) -> None:
        """Add declarations to this application's shared Schema."""
        self.schema.declare(declarations)

    def _validate_ref(
        self,
        key: ContextKey,
        *,
        role: _RefRole = "any",
    ) -> Ref[Any]:
        if isinstance(key, str):
            try:
                ref = self.schema.resolve(key)
            except (KeyError, TypeError, ValueError) as error:
                raise ContextPathError(str(error)) from error
        elif isinstance(key, Ref):
            try:
                ref = self.schema.resolve(key.path)
            except (KeyError, TypeError, ValueError) as error:
                raise ContextPathError(
                    f"Ref path {key.path!r} is not declared by this Context."
                ) from error
        else:
            raise TypeError(
                f"Context keys must be str or Ref, got {type(key).__name__}."
            )

        kind = self.schema._kind_for_ref(ref)
        if kind is None:
            raise ContextPathError(
                f"Ref path {ref.path!r} is not declared by this Context."
            )
        if role != "any" and role != kind:
            raise ContextPathError(
                f"Ref path {ref.path!r} is declared as a {kind}, not a {role}."
            )
        return ref

    def _absolute_ref(self, parts: tuple[str, ...]) -> Ref[Any]:
        return self.schema.resolve(".".join(parts))

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
        ref: ContextKey,
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

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        ref = self._validate_ref(ref)
        try:
            self._resolve(ref.parts, local=local)
            return True
        except ContextPathError:
            return False

    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        element = self._resolve(parts, local=local)
        if isinstance(element, tuple):
            return _context_keys(element)
        raise ContextPathError("Cannot list keys of a leaf value.")

    def _snapshot_tree(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        val = self._resolve(parts, local=local)
        if isinstance(val, tuple):
            return _snapshot_trees(val)
        raise ContextPathError("Target is not a Context container.")

    def to_dict(
        self,
        ref: ContextKey | None = None,
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
        updates: Mapping[ContextKey, Any] | None = None,
        drops: Iterable[ContextKey] | None = None,
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
            {self._validate_ref(k, role="leaf"): v for k, v in updates.items()}
            if updates
            else {}
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
    def update(self, updates: Mapping[ContextKey, Any]) -> None:
        """Set several local bindings atomically."""
        self.mutate(updates=updates)

    def drop(self, refs: Iterable[ContextKey]) -> None:
        """Delete several local paths atomically."""
        self.mutate(drops=refs)

    def set(self, ref: ContextKey, value: _T) -> None:
        """Set one local binding."""
        self.mutate(updates={ref: value})

    def add(self, ref: ContextKey, value: _T) -> Callable[[], None]:
        """Add an immutable local binding and return its exact disposer."""
        ref = self._validate_ref(ref, role="leaf")
        try:
            self._resolve(ref.parts, local=True)
        except ContextPathError:
            pass
        else:
            raise ContextPathError(f"Cannot add existing local path {ref.path!r}.")

        entry = _Value(value, replaceable=False)
        self._mutate_raw({ref.parts: entry}, ())

        context_ref = weakref.ref(self)
        entry_ref = weakref.ref(entry)

        def dispose() -> None:
            context = context_ref()
            expected = entry_ref()
            if context is None or expected is None:
                return
            try:
                current_entry = context._resolve(ref.parts, local=True)
            except ContextPathError:
                return
            if current_entry is not expected:
                return

            context.delete(ref)

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
        updates: dict[ContextKey, Any] = {
            self._validate_ref(ref): CTX_EVAL_ENGINE.get_element(value_tree, path)
            for path, ref in CTX_EVAL_ENGINE.iter_with_key_path(ref_tree)
        }
        self.mutate(updates=updates)

    def delete(self, ref: ContextKey) -> None:
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

    def _adjust_ref(self, ref: ContextKey | None) -> Ref[Any]:
        if ref is None:
            return self._context.schema.resolve(".".join(self._parts))
        if isinstance(ref, str):
            relative_parts = _split_ref_path(ref)
            return self._context.schema.resolve(
                ".".join((*self._parts, *relative_parts))
            )
        if not isinstance(ref, Ref):
            raise TypeError(
                f"ContextView keys must be str or Ref, got {type(ref).__name__}."
            )
        if ref.parts[: len(self._parts)] != self._parts:
            raise ContextPathError(
                f"Ref path {ref.path!r} is outside this ContextView."
            )
        return self._context._validate_ref(ref)

    def _adjust_ref_tree(self, ref_tree: Any) -> Any:
        def adjust(obj):
            if isinstance(obj, (str, Ref)):
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
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        return self._context.get(self._adjust_ref(ref), default, local=local)

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        return self._context.exists(self._adjust_ref(ref), local=local)

    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        return self._context.keys(self._adjust_ref(ref), local=local)

    def _snapshot_tree(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        return self._context._snapshot_tree(self._adjust_ref(ref), local=local)

    def to_dict(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref), local=local)

    def _absolute_ref(self, parts: tuple[str, ...]) -> Ref[Any]:
        return self._context.schema.resolve(".".join((*self._parts, *parts)))


from .tree import CTX_EVAL_ENGINE
