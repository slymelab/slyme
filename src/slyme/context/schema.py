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

from collections import OrderedDict
from collections.abc import Callable, Hashable, Mapping
from dataclasses import InitVar, dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Generic, Literal, cast, final

from typing_extensions import TypeVar

from slyme.execution import once
from slyme.utils.exception import exception_group

if TYPE_CHECKING:
    from .store import ContextStore

__all__ = [
    "ContextKey",
    "ContextPathError",
    "Ref",
    "ref",
    "Metadata",
    "RefConfig",
    "RefContainerConfig",
    "RefDeclaration",
    "RefEntry",
    "RefLeafConfig",
    "Schema",
]

_T = TypeVar("_T", default=Any)


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


def ref(path: str) -> Ref[_T]:
    """Create a path reference; the expected type can supply its value type."""
    return Ref(path)


ContextKey = str | Ref[_T]


class ContextPathError(KeyError):
    """A Context path cannot be resolved or changed as requested."""


@dataclass(frozen=True)
class Metadata:
    """Immutable auxiliary data with identity compatibility by default.

    Override equals for content equivalence. It must be synchronous, pure,
    stable, reflexive, symmetric and transitive. Equivalent objects must be
    interchangeable for consumers. Comparisons must not read or modify Schema or external state.
    The framework accepts identical objects without calling equals.
    """

    def equals(self, other: Metadata) -> bool:
        """Whether two markers are equivalent, without combining their data."""
        return self is other


@final
@dataclass(frozen=True, kw_only=True)
class RefLeafConfig:
    """The write mode of one declared leaf."""

    mode: Literal["assign", "register"] = "assign"

    def equals(self, other: RefConfig) -> bool:
        return isinstance(other, RefLeafConfig) and self.mode == other.mode


@final
@dataclass(frozen=True)
class RefContainerConfig:
    """The container role of a declared path."""

    def equals(self, other: RefConfig) -> bool:
        return isinstance(other, RefContainerConfig)


RefConfig = RefLeafConfig | RefContainerConfig


@dataclass(frozen=True)
class RefDeclaration:
    """One owner's configuration and immutable metadata mapping."""

    config: RefConfig
    metadata: Mapping[str, Metadata] = field(default_factory=dict, kw_only=True)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


_CONTAINER_ENTRY_KEY = ""
_SchemaContainer = dict[str, "_SchemaElement"]


@dataclass(frozen=True, eq=False)
class RefEntry(Generic[_T]):
    """A live Schema entry exposing its Ref, config, metadata, and validity.

    Obtain entries through Schema. Withdrawing the last declaration makes an
    entry inactive; declaring the same path again creates a different entry.
    """

    ref: Ref[_T]
    # NOTE: use OrderedDict to keep first-owner selection efficient after arbitrary withdrawals.
    _declarations: OrderedDict[Hashable, RefDeclaration] = field(
        default_factory=OrderedDict, init=False, repr=False
    )
    _metadata_declarations: dict[str, OrderedDict[Hashable, Metadata]] = field(
        default_factory=dict, init=False, repr=False
    )
    _metadata: Mapping[str, Metadata] | None = field(
        default=None, init=False, repr=False
    )

    @property
    def alive(self) -> bool:
        """Whether at least one declaration owns this entry."""
        return bool(self._declarations)

    @property
    def config(self) -> RefConfig:
        """The earliest active owner's original configuration."""
        if not self.alive:
            raise LookupError(f"Ref path {self.ref.path!r} is no longer declared.")
        return next(iter(self._declarations.values())).config

    @property
    def metadata(self) -> Mapping[str, Metadata]:
        """A cached read-only snapshot of the current metadata representatives."""
        if not self.alive:
            raise LookupError(f"Ref path {self.ref.path!r} is no longer declared.")
        metadata = self._metadata
        if metadata is None:
            metadata = MappingProxyType(
                {
                    key: next(iter(owners.values()))
                    for key, owners in self._metadata_declarations.items()
                }
            )
            object.__setattr__(self, "_metadata", metadata)
        return metadata

    def _declare(self, declaration_id: Hashable, declaration: RefDeclaration) -> None:
        """Validate and register contributions in one pass, rolling back on failure."""
        if declaration_id in self._declarations:
            raise ValueError(
                f"Duplicate declaration {declaration_id!r} at {self.ref.path!r}."
            )
        if self.alive:
            first = next(iter(self._declarations.values())).config
            if first is not declaration.config and not first.equals(declaration.config):
                raise ValueError(
                    f"Conflicting Ref configurations: {first!r} and {declaration.config!r}."
                )
        self._declarations[declaration_id] = declaration
        changed = False
        try:
            for key, incoming in declaration.metadata.items():
                owners = self._metadata_declarations.get(key)
                if owners is None:
                    owners = self._metadata_declarations[key] = OrderedDict()
                    changed = True
                else:
                    representative = next(iter(owners.values()))
                    if representative is not incoming and not representative.equals(
                        incoming
                    ):
                        raise ValueError(
                            f"Conflicting metadata at {self.ref.path!r}, key {key!r}."
                        )
                owners[declaration_id] = incoming
        except BaseException:
            self._undeclare(declaration_id)
            raise
        if changed:
            object.__setattr__(self, "_metadata", None)

    def _undeclare(self, declaration_id: Hashable) -> None:
        """Withdraw complete or partial contributions without comparing metadata."""
        declaration = self._declarations.pop(declaration_id)
        changed = not self.alive
        for key in declaration.metadata:
            owners = self._metadata_declarations.get(key)
            if owners is None:
                continue
            representative = next(iter(owners.values()))
            owners.pop(declaration_id, None)
            if not owners:
                del self._metadata_declarations[key]
                changed = True
            elif representative is not next(iter(owners.values())):
                changed = True
        if changed:
            object.__setattr__(self, "_metadata", None)


_SchemaElement = RefEntry[Any] | _SchemaContainer
_Declaration = dict[str, "_Declaration | RefDeclaration"]


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
        root._declare(object(), self.container())
        self._set_entry(root)
        if declaration is not None:
            self.declare(declaration)

    def _attach_store(self, store: ContextStore) -> None:
        """Retain a Store that must be notified when a field is withdrawn."""
        self._stores.add(store)

    def _detach_store(self, store: ContextStore) -> None:
        """Stop retaining and notifying a disposed Store."""
        self._stores.discard(store)

    @staticmethod
    def leaf(
        *,
        mode: Literal["assign", "register"] = "assign",
        metadata: Mapping[str, Metadata] | None = None,
    ) -> RefDeclaration:
        """Describe a leaf's write mode and auxiliary metadata."""
        return RefDeclaration(
            RefLeafConfig(mode=mode), metadata={} if metadata is None else metadata
        )

    @staticmethod
    def container(*, metadata: Mapping[str, Metadata] | None = None) -> RefDeclaration:
        """Describe one container explicitly at a dict's empty key."""
        return RefDeclaration(
            RefContainerConfig(), metadata={} if metadata is None else metadata
        )

    @staticmethod
    def _normalize_declaration(
        tree: _Declaration, path: str = ""
    ) -> list[tuple[str, RefDeclaration]]:
        """Validate a declaration tree and flatten it in tree order."""
        declaration = tree.get(_CONTAINER_ENTRY_KEY, Schema.container())
        if not isinstance(declaration, RefDeclaration) or not isinstance(
            declaration.config, RefContainerConfig
        ):
            raise TypeError(
                f"Invalid current-entry declaration at {path!r}: "
                "the empty key must contain Schema.container()."
            )
        result: list[tuple[str, RefDeclaration]] = [(path, declaration)]
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
                result.extend(Schema._normalize_declaration(value, child_path))
            elif not isinstance(value, RefDeclaration) or not isinstance(
                value.config, RefLeafConfig
            ):
                raise TypeError(
                    f"Invalid Schema declaration at {child_path!r}: expected a "
                    f"dict or Schema.leaf(), got {type(value).__name__}."
                )
            else:
                result.append((child_path, value))
        return result

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
            store.delete_entry(entry)

    @property
    def entries(self) -> tuple[RefEntry[Any], ...]:
        """Snapshot the registered root, container, and leaf entries, not configs."""
        return tuple(self._entries.values())

    def resolve_entry(
        self,
        key: ContextKey[_T],
        *,
        role: Literal["leaf", "container"] | None = None,
    ) -> RefEntry[_T]:
        """Resolve a declared path; reject missing paths or a required role mismatch.

        Strings and Refs resolve by path. Without a required role, resolution
        does not need to read the entry's configuration.
        """
        path = key if isinstance(key, str) else key.path
        try:
            entry = self._entries[path]
        except KeyError as error:
            raise ContextPathError(f"Context path {path!r} is not declared.") from error
        if role is None:
            return entry

        kind = "leaf" if isinstance(entry.config, RefLeafConfig) else "container"
        if role != kind:
            raise ContextPathError(
                f"Ref path {path!r} is declared as a {kind}, not a {role}."
            )
        return entry

    def resolve(
        self,
        key: ContextKey[_T],
        *,
        role: Literal["leaf", "container"] | None = None,
    ) -> Ref[_T]:
        """Return the declared Ref; an empty path resolves the root container."""
        return self.resolve_entry(key, role=role).ref

    def declare(
        self,
        declaration: Schema | _Declaration,
    ) -> Callable[[], None]:
        """Register paths with rollback on failure and an idempotent disposer.

        Registration updates paths incrementally, without isolation from
        synchronous callbacks. Metadata comparisons must depend only on their inputs.
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
                errors: list[BaseException] = []
                for entry in reversed(entries):
                    try:
                        entry._undeclare(declaration_id)
                        if not entry.alive:
                            cast(Schema, schema)._delete_entry(entry)
                    except BaseException as error:
                        errors.append(error)
                if errors:
                    raise exception_group("Failed to release declaration", errors)
            finally:
                schema = None
                entries.clear()

        try:
            if isinstance(declaration, Schema):
                declarations = [
                    (
                        entry.ref.path,
                        RefDeclaration(entry.config, metadata=entry.metadata),
                    )
                    for entry in declaration.entries
                ]
            else:
                declarations = self._normalize_declaration(declaration)
            for path, contribution in declarations:
                entry = self._entries.get(path)
                if entry is None:
                    entry = RefEntry(Ref(path))
                entry._declare(declaration_id, contribution)
                entries.append(entry)
                if path not in self._entries:
                    self._set_entry(entry)
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
