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

"""Reversible Context path declarations and their configurations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Hashable, Mapping
from dataclasses import InitVar, dataclass, field, replace
from functools import reduce
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, Literal, TypeVar, cast

from slyme.utils.exception import exception_group
from slyme.utils.execution import once
from slyme.utils.tree import MappingKey, TreeEngine
from slyme.utils.tree.common import flatten_dict, unflatten_dict

if TYPE_CHECKING:
    from .store import ContextStore

__all__ = [
    "Ref",
    "Metadata",
    "RefConfig",
    "RefContainerConfig",
    "RefEntry",
    "RefLeafConfig",
    "Schema",
]

_T = TypeVar("_T")

SCHEMA_ENGINE = TreeEngine("schema_engine", register_defaults=False)
SCHEMA_ENGINE.register(dict, flatten_dict, unflatten_dict)


@dataclass(frozen=True)
class Ref(Generic[_T]):
    """Immutable Context path; the empty path identifies the root container."""

    path: str
    parts: tuple[str, ...] = field(init=False)

    @staticmethod
    def _split_path(path: str) -> tuple[str, ...]:
        if not path:
            return ()
        parts = tuple(path.split("."))
        if any(not part for part in parts):
            raise ValueError(f"Invalid Ref path: {path!r}.")
        return parts

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", self._split_path(self.path))


@dataclass(frozen=True)
class Metadata:
    """Immutable metadata, compatible by identity unless merge is overridden.

    Merge only the input items, without reading or modifying Schema or other
    external state. Merges follow declaration order, including repeated
    references to the same item.
    Accepted contributions must remain mergeable after any subset is removed.
    """

    def merge(self, other: Metadata) -> Metadata:
        """Accept the same instance; override to define content-based merging."""
        if self is not other:
            raise ValueError(f"Conflicting metadata: {self!r} and {other!r}.")
        return self


@dataclass(frozen=True, kw_only=True)
class RefConfig(ABC, Generic[_T]):
    """Immutable declaration configuration with side-effect-free merging."""

    metadata: Mapping[str, Metadata] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def _merge_metadata(self, other: RefConfig[Any]) -> dict[str, Metadata]:
        result = dict(self.metadata)
        for key, incoming in other.metadata.items():
            result[key] = result[key].merge(incoming) if key in result else incoming
        return result

    @abstractmethod
    def merge(self, other: RefConfig[_T]) -> RefConfig[_T]:
        """Return a new merged config, or raise for incompatible declarations."""


@dataclass(frozen=True)
class RefLeafConfig(RefConfig[_T]):
    """Behavior of one leaf declared in a Schema."""

    value_type: type[_T] | None = None
    mode: Literal["assign", "register"] = "assign"

    def merge(self, other: RefConfig[_T]) -> RefLeafConfig[_T]:
        if not isinstance(other, RefLeafConfig):
            raise ValueError(f"Conflicting Ref configurations: {self!r} and {other!r}.")
        if self.value_type != other.value_type or self.mode != other.mode:
            raise ValueError(f"Conflicting Ref configurations: {self!r} and {other!r}.")
        return replace(self, metadata=self._merge_metadata(other))


@dataclass(frozen=True)
class RefContainerConfig(RefConfig[Any]):
    """Behavior of one container declared in a Schema."""

    def merge(self, other: RefConfig[Any]) -> RefContainerConfig:
        if not isinstance(other, RefContainerConfig):
            raise ValueError(f"Conflicting Ref configurations: {self!r} and {other!r}.")
        return replace(self, metadata=self._merge_metadata(other))


_CONTAINER_ENTRY_KEY = ""
_SchemaContainer = dict[str, "_SchemaElement"]


@dataclass(frozen=True, eq=False)
class RefEntry(Generic[_T]):
    """A live Schema entry exposing its Ref, merged config, and validity.

    Obtain entries through Schema. Withdrawing the last declaration makes an
    entry inactive; declaring the same path again creates a different entry.
    """

    ref: Ref[_T]
    _config: RefConfig[_T] | None = field(default=None, init=False, repr=False)
    _declarations: dict[Hashable, RefConfig[_T]] = field(
        default_factory=dict, init=False, repr=False
    )

    @property
    def alive(self) -> bool:
        """Whether at least one declaration owns this entry."""
        return bool(self._declarations)

    @property
    def config(self) -> RefConfig[_T]:
        """Return the merged config; raise LookupError if no declarations remain."""
        config = self._config
        if config is None:
            if not self._declarations:
                raise LookupError(f"Ref path {self.ref.path!r} is no longer declared.")
            config = reduce(
                lambda current, incoming: current.merge(incoming),
                self._declarations.values(),
            )
            object.__setattr__(self, "_config", config)
        return config

    def _declare(self, declaration_id: Hashable, config: RefConfig[_T]) -> None:
        """Merge an incoming config before registering its unique owner."""
        if declaration_id in self._declarations:
            raise ValueError(
                f"Duplicate declaration {declaration_id!r} at {self.ref.path!r}."
            )
        merged = self.config.merge(config) if self.alive else config
        self._declarations[declaration_id] = config
        object.__setattr__(self, "_config", merged)

    def _undeclare(self, declaration_id: Hashable) -> None:
        """Remove an owner and invalidate the merged config cache."""
        del self._declarations[declaration_id]
        object.__setattr__(self, "_config", None)


_SchemaElement = RefEntry[Any] | _SchemaContainer
_Declaration = dict[str, "_Declaration | RefConfig[Any]"]


@dataclass(frozen=True, eq=False, repr=False)
class Schema:
    """Reversible declarations shared by application data stores.

    The root container is permanent. Withdrawing a field cleans its bindings in
    every attached store, without guaranteeing order between applications.
    """

    declaration: InitVar[Schema | _Declaration | None] = None
    _data: _SchemaContainer = field(default_factory=dict, init=False)
    _entries: dict[str, RefEntry[Any]] = field(default_factory=dict, init=False)
    _stores: set[ContextStore] = field(default_factory=set, init=False)

    def __post_init__(self, declaration: Schema | _Declaration | None) -> None:
        root: RefEntry[Any] = RefEntry(Ref(_CONTAINER_ENTRY_KEY))
        root._declare(object(), RefContainerConfig())
        self._set_entry(root)
        if declaration is not None:
            self.declare(declaration)

    @staticmethod
    def leaf(
        value_type: type[_T] | None = None,
        *,
        mode: Literal["assign", "register"] = "assign",
        metadata: Mapping[str, Metadata] | None = None,
    ) -> RefLeafConfig[_T]:
        """Describe mutable assignments or an owner-managed registration leaf."""
        return RefLeafConfig(
            value_type,
            mode,
            metadata={} if metadata is None else metadata,
        )

    @staticmethod
    def container(
        *, metadata: Mapping[str, Metadata] | None = None
    ) -> RefContainerConfig:
        """Describe one container explicitly at a dict's empty key."""
        return RefContainerConfig(metadata={} if metadata is None else metadata)

    @staticmethod
    def _normalize_declaration(tree: _Declaration, path: str = "") -> _Declaration:
        """Copy and validate a declaration tree with explicit container configs."""
        config = tree.get(_CONTAINER_ENTRY_KEY, RefContainerConfig())
        if not isinstance(config, RefContainerConfig):
            raise TypeError(
                f"Invalid current-entry declaration at {path!r}: "
                "the empty key must contain Schema.container()."
            )
        result: _Declaration = {_CONTAINER_ENTRY_KEY: config}
        for name, value in tree.items():
            if name == _CONTAINER_ENTRY_KEY:
                continue
            if "." in name:
                raise ValueError(
                    f"Invalid Schema key {name!r} at {path or '<root>'!r}: "
                    "expected a name without dots."
                )
            child_path = f"{path}.{name}" if path else name
            if isinstance(value, dict):
                result[name] = Schema._normalize_declaration(value, child_path)
            elif not isinstance(value, RefLeafConfig):
                raise TypeError(
                    f"Invalid Schema declaration at {child_path!r}: expected a "
                    f"dict or Schema.leaf(), got {type(value).__name__}."
                )
            else:
                result[name] = value
        return result

    def _merge_declaration(
        self,
        declaration: Schema | _Declaration,
        declaration_id: object,
        entries: list[RefEntry[Any]],
    ) -> None:
        """Merge paths, recording successful registrations for undo."""
        importing = isinstance(declaration, Schema)
        source = (
            declaration._element_at(())
            if isinstance(declaration, Schema)
            else self._normalize_declaration(declaration)
        )
        for key_path, value in SCHEMA_ENGINE.iter_with_key_path(source):
            parts = tuple(cast(str, cast(MappingKey, key).key) for key in key_path)
            is_container = parts[-1] == _CONTAINER_ENTRY_KEY
            path = ".".join(parts[:-1] if is_container else parts)
            config = cast(RefEntry[Any], value).config if importing else value
            entry = self._entries.get(path)
            if entry is None:
                entry = RefEntry(Ref(path))
            entry._declare(declaration_id, config)
            entries.append(entry)
            if path not in self._entries:
                self._set_entry(entry)

    def _set_entry(self, entry: RefEntry[Any]) -> None:
        """Install one entry in both indexes without declaring its ancestors."""
        parts = entry.ref.parts
        if isinstance(entry.config, RefContainerConfig):
            parts += (_CONTAINER_ENTRY_KEY,)
        parent = cast(_SchemaContainer, self._element_at(parts[:-1], create=True))
        parent[parts[-1]] = entry
        self._entries[entry.ref.path] = entry

    def _delete_entry(self, entry: RefEntry[Any]) -> None:
        """Remove one entry, preserving descendants and pruning empty dicts."""
        if self._entries.get(entry.ref.path) is not entry:
            return
        parts = entry.ref.parts
        if isinstance(self._element_at(parts), dict):
            parts += (_CONTAINER_ENTRY_KEY,)
        for depth in range(len(parts), 0, -1):
            parent = cast(_SchemaContainer, self._element_at(parts[: depth - 1]))
            del parent[parts[depth - 1]]
            if parent:
                break
        del self._entries[entry.ref.path]
        for store in tuple(self._stores):
            store.remove_entry(entry)

    @staticmethod
    def _release_declaration(
        schema: Schema,
        declaration_id: object,
        entries: list[RefEntry[Any]],
    ) -> None:
        errors: list[BaseException] = []
        for entry in reversed(entries):
            try:
                entry._undeclare(declaration_id)
                if not entry.alive:
                    schema._delete_entry(entry)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise exception_group("Failed to release declaration", errors)

    @property
    def entries(self) -> tuple[RefEntry[Any], ...]:
        """Snapshot the registered root, container, and leaf entries, not configs."""
        return tuple(self._entries.values())

    def resolve_entry(self, path: str) -> RefEntry[Any]:
        """Return the current entry at a path; raise KeyError if it is undeclared."""
        return self._entries[path]

    def resolve(self, path: str) -> Ref[Any]:
        """Return the declared Ref; an empty path resolves the root container."""
        return self.resolve_entry(path).ref

    def declare(
        self,
        declaration: Schema | _Declaration,
    ) -> Callable[[], None]:
        """Register paths with rollback on failure and an idempotent disposer.

        Registration updates paths incrementally, without isolation from
        synchronous callbacks. Config merges must depend only on their inputs.
        The root container remains declared after every disposer has run.
        Disposal reverses registration order, continues after failures, and
        raises them together as an exception group. Subsequent calls reproduce
        the same group without repeating cleanup. The disposer retains this
        Schema and its entries until called, then releases its references even
        on failure; failure tracebacks can still retain cleanup state.
        """
        declaration_id = object()
        entries: list[RefEntry[Any]] = []
        schema: Schema | None = self

        @once
        def dispose() -> None:
            nonlocal schema
            try:
                Schema._release_declaration(
                    cast(Schema, schema), declaration_id, entries
                )
            finally:
                schema = None
                entries.clear()

        try:
            self._merge_declaration(declaration, declaration_id, entries)
        except BaseException:
            dispose()
            raise
        return dispose

    def _element_at(
        self, parts: tuple[str, ...], *, create: bool = False
    ) -> _SchemaElement:
        """Look up a tree element, optionally creating undeclared container dicts."""
        element: _SchemaElement = self._data
        for part in parts:
            if not isinstance(element, dict):
                raise KeyError(".".join(parts))
            element = element.setdefault(part, {}) if create else element[part]
            if create and not isinstance(element, dict):
                raise ValueError(
                    f"Conflicting Ref configurations at {'.'.join(parts)!r}: "
                    "a leaf cannot contain child paths."
                )
        return element

    def _child_names(self, parts: tuple[str, ...]) -> tuple[str, ...]:
        """Return direct child names, excluding the container's own entry."""
        element = self._element_at(parts)
        if not isinstance(element, dict):
            raise TypeError("Schema leaf paths do not have children.")
        return tuple(name for name in element if name != _CONTAINER_ENTRY_KEY)

    def _leaf_entries(
        self,
        parts: tuple[str, ...] = (),
    ) -> tuple[RefEntry[Any], ...]:
        element = self._element_at(parts)
        if isinstance(element, RefEntry):
            return (element,)

        leaves: list[RefEntry[Any]] = []

        def collect(container: _SchemaContainer) -> None:
            for name, child_element in container.items():
                if name == _CONTAINER_ENTRY_KEY:
                    continue
                if isinstance(child_element, RefEntry):
                    leaves.append(child_element)
                else:
                    collect(child_element)

        collect(element)
        return tuple(leaves)
