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

import difflib
import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from .ref import Ref

__all__ = ["Schema"]

_T = TypeVar("_T")


@dataclass(frozen=True)
class _RefLeafConfig(Generic[_T]):
    """Behavior of one leaf declared in a Schema."""

    value_type: Any | None = None
    replaceable: bool = True


@dataclass(frozen=True)
class _RefContainerConfig:
    """Behavior of one container declared in a Schema."""


_RefConfig = _RefLeafConfig[Any] | _RefContainerConfig


_REF_ENTRY_KEY = ""
_SchemaContainer = dict[str, Any]


@dataclass(eq=False)
class _RefEntry(Generic[_T]):
    """One active Ref definition and its independent declaration owners."""

    ref: Ref[_T]
    config: _RefConfig
    declarations: set[object]


_SchemaNode = _RefEntry[Any] | _SchemaContainer


class Schema:
    """Mutable tree of independently reversible Context path declarations."""

    __slots__ = ("__data", "__weakref__")
    __data: _SchemaContainer

    @staticmethod
    def leaf(
        value_type: Any | None = None,
        *,
        replaceable: bool = True,
    ) -> _RefLeafConfig[Any]:
        """Describe one assignable Schema leaf."""
        return _RefLeafConfig(value_type, replaceable)

    @staticmethod
    def container() -> _RefContainerConfig:
        """Describe one container explicitly at a mapping's empty key."""
        return _RefContainerConfig()

    @staticmethod
    def _build_entry(
        value: Any,
        path: str,
        active_mappings: set[int],
        declaration_id: object,
    ) -> _SchemaNode:
        if isinstance(value, _RefLeafConfig):
            return _RefEntry(Ref(path), value, {declaration_id})
        if isinstance(value, _RefContainerConfig):
            raise TypeError(
                f"Invalid Schema declaration at {path!r}: Schema.container() is "
                "only valid under the empty key of a container mapping."
            )
        if not isinstance(value, Mapping):
            raise TypeError(
                f"Invalid Schema declaration at {path!r}: expected a mapping or "
                f"Schema.leaf(), got {type(value).__name__}."
            )

        mapping_id = id(value)
        if mapping_id in active_mappings:
            raise ValueError(f"Cyclic Schema declarations detected at {path!r}.")
        active_mappings.add(mapping_id)
        try:
            container_config = _RefContainerConfig()
            if _REF_ENTRY_KEY in value:
                current = value[_REF_ENTRY_KEY]
                if not isinstance(current, _RefContainerConfig):
                    raise TypeError(
                        f"Invalid current-entry declaration at {path!r}: "
                        "the empty key must contain Schema.container()."
                    )
                container_config = current

            result: _SchemaContainer = {
                _REF_ENTRY_KEY: _RefEntry(
                    Ref(path),
                    container_config,
                    {declaration_id},
                )
            }
            for raw_name, child in value.items():
                if raw_name == _REF_ENTRY_KEY:
                    continue
                name = Ref._validate_name(raw_name, path)
                child_path = f"{path}.{name}"
                result[name] = Schema._build_entry(
                    child,
                    child_path,
                    active_mappings,
                    declaration_id,
                )
            return result
        finally:
            active_mappings.remove(mapping_id)

    @staticmethod
    def _build(
        declarations: Mapping[str, Any],
        declaration_id: object,
    ) -> _SchemaContainer:
        if _REF_ENTRY_KEY in declarations:
            raise ValueError(
                "The root Schema declaration cannot define an empty-key "
                "Schema.container()."
            )

        active_mappings = {id(declarations)}
        result: _SchemaContainer = {}
        for raw_name, value in declarations.items():
            name = Ref._validate_name(raw_name, "")
            result[name] = Schema._build_entry(
                value,
                name,
                active_mappings,
                declaration_id,
            )
        return result

    @staticmethod
    def _container_entry(container: _SchemaContainer) -> _RefEntry[Any]:
        return cast(_RefEntry[Any], container[_REF_ENTRY_KEY])

    @staticmethod
    def _iter_entries(
        container: _SchemaContainer,
        prefix: tuple[str, ...] = (),
    ) -> Iterable[tuple[tuple[str, ...], _RefEntry[Any]]]:
        for name, node in container.items():
            if name == _REF_ENTRY_KEY:
                continue
            parts = (*prefix, name)
            if isinstance(node, _RefEntry):
                yield parts, node
                continue
            child = cast(_SchemaContainer, node)
            yield parts, Schema._container_entry(child)
            yield from Schema._iter_entries(child, parts)

    @staticmethod
    def _copy_declaration(
        source: _SchemaContainer,
        declaration_id: object,
    ) -> _SchemaContainer:
        result: _SchemaContainer = {}
        for name, node in source.items():
            if name == _REF_ENTRY_KEY:
                entry = cast(_RefEntry[Any], node)
                result[name] = _RefEntry(
                    Ref(entry.ref.path),
                    entry.config,
                    {declaration_id},
                )
            elif isinstance(node, _RefEntry):
                result[name] = _RefEntry(
                    Ref(node.ref.path),
                    node.config,
                    {declaration_id},
                )
            else:
                result[name] = Schema._copy_declaration(
                    cast(_SchemaContainer, node),
                    declaration_id,
                )
        return result

    @staticmethod
    def _validate_merge(
        current: _SchemaContainer,
        incoming: _SchemaContainer,
        prefix: tuple[str, ...] = (),
    ) -> None:
        for name, incoming_node in incoming.items():
            if name == _REF_ENTRY_KEY:
                current_entry = Schema._container_entry(current)
                incoming_entry = cast(_RefEntry[Any], incoming_node)
                if current_entry.config != incoming_entry.config:
                    path = ".".join(prefix)
                    raise ValueError(f"Conflicting Ref configuration at path {path!r}.")
                continue
            if name not in current:
                continue

            path_parts = (*prefix, name)
            path = ".".join(path_parts)
            current_node = current[name]
            current_is_leaf = isinstance(current_node, _RefEntry)
            incoming_is_leaf = isinstance(incoming_node, _RefEntry)
            if current_is_leaf != incoming_is_leaf:
                current_kind = "leaf" if current_is_leaf else "container"
                incoming_kind = "leaf" if incoming_is_leaf else "container"
                raise ValueError(
                    f"Conflicting Schema structure at path {path!r}: existing "
                    f"entry is {current_kind}, incoming entry is {incoming_kind}."
                )
            if current_is_leaf:
                current_entry = cast(_RefEntry[Any], current_node)
                incoming_entry = cast(_RefEntry[Any], incoming_node)
                if current_entry.config != incoming_entry.config:
                    raise ValueError(f"Conflicting Ref configuration at path {path!r}.")
                continue
            Schema._validate_merge(
                cast(_SchemaContainer, current_node),
                cast(_SchemaContainer, incoming_node),
                path_parts,
            )

    @staticmethod
    def _commit_merge(
        current: _SchemaContainer,
        incoming: _SchemaContainer,
    ) -> None:
        for name, incoming_node in incoming.items():
            if name not in current:
                current[name] = incoming_node
                continue
            current_node = current[name]
            if isinstance(incoming_node, _RefEntry):
                cast(_RefEntry[Any], current_node).declarations.update(
                    incoming_node.declarations
                )
                continue
            Schema._commit_merge(
                cast(_SchemaContainer, current_node),
                cast(_SchemaContainer, incoming_node),
            )

    @staticmethod
    def _remove_declaration(
        root: _SchemaContainer,
        paths: tuple[tuple[str, ...], ...],
        declaration_id: object,
    ) -> None:
        for parts in sorted(paths, key=len, reverse=True):
            parent = root
            missing_parent = False
            for part in parts[:-1]:
                node = parent.get(part)
                if not isinstance(node, dict):
                    missing_parent = True
                    break
                parent = cast(_SchemaContainer, node)
            if missing_parent:
                continue

            name = parts[-1]
            node = parent.get(name)
            if node is None:
                continue
            entry = (
                node
                if isinstance(node, _RefEntry)
                else Schema._container_entry(cast(_SchemaContainer, node))
            )
            entry.declarations.discard(declaration_id)
            if entry.declarations:
                continue
            if isinstance(node, dict) and any(key for key in node if key):
                raise RuntimeError(
                    "Schema declaration ownership invariant was violated at "
                    f"{'.'.join(parts)!r}."
                )
            del parent[name]

    def __init__(self, declarations: Mapping[str, Any] | None = None) -> None:
        if declarations is None:
            declarations = {}
        object.__setattr__(
            self,
            "_Schema__data",
            self._build(declarations, object()),
        )

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"{type(self).__name__} attributes are read-only; use declare()."
        )

    def __delattr__(self, name: str) -> None:
        raise AttributeError(
            f"{type(self).__name__} attributes are read-only; dispose declarations "
            "through the callable returned by declare()."
        )

    def _resolve_node(self, path: str) -> _SchemaNode:
        parts = Ref._split_path(path)
        node: _SchemaNode = self.__data
        for index, part in enumerate(parts):
            if not isinstance(node, dict) or part not in node:
                candidates = (
                    [key for key in node if key] if isinstance(node, dict) else []
                )
                suggestion = difflib.get_close_matches(part, candidates, n=1)
                detail = f" Did you mean {suggestion[0]!r}?" if suggestion else ""
                parent = ".".join(parts[:index]) or "<root>"
                raise KeyError(f"Schema path {parent!r} has no entry {part!r}.{detail}")
            node = cast(_SchemaNode, node[part])
        return node

    def _resolve_entry(self, path: str) -> _RefEntry[Any]:
        node = self._resolve_node(path)
        if isinstance(node, _RefEntry):
            return node
        return self._container_entry(node)

    def resolve(self, path: str) -> Ref[Any]:
        """Return the immutable Ref declared at *path*."""
        return self._resolve_entry(path).ref

    def declare(
        self,
        declarations: Schema | Mapping[str, Any],
    ) -> Callable[[], None]:
        """Add one atomic declaration and return its idempotent disposer."""
        declaration_id = object()
        if isinstance(declarations, Schema):
            incoming = self._copy_declaration(
                declarations.__data,
                declaration_id,
            )
        else:
            incoming = self._build(declarations, declaration_id)

        self._validate_merge(self.__data, incoming)
        paths = tuple(path for path, _ in self._iter_entries(incoming))
        self._commit_merge(self.__data, incoming)

        schema_ref = weakref.ref(self)
        disposed = False

        def dispose() -> None:
            nonlocal disposed
            if disposed:
                return
            disposed = True
            schema = schema_ref()
            if schema is not None:
                schema._remove_declaration(
                    schema.__data,
                    paths,
                    declaration_id,
                )

        return dispose

    def _node_at(self, parts: tuple[str, ...]) -> _SchemaNode:
        node: _SchemaNode = self.__data
        for part in parts:
            if not isinstance(node, dict):
                raise KeyError(".".join(parts))
            node = cast(_SchemaNode, node[part])
        return node

    def _child_names(self, parts: tuple[str, ...]) -> tuple[str, ...]:
        node = self._node_at(parts)
        if not isinstance(node, dict):
            raise TypeError("Schema leaf paths do not have children.")
        return tuple(name for name in node if name != _REF_ENTRY_KEY)

    def _leaf_entries(
        self,
        parts: tuple[str, ...] = (),
    ) -> tuple[_RefEntry[Any], ...]:
        node = self._node_at(parts)
        if isinstance(node, _RefEntry):
            return (node,)

        leaves: list[_RefEntry[Any]] = []

        def collect(container: _SchemaContainer) -> None:
            for name, child_node in container.items():
                if name == _REF_ENTRY_KEY:
                    continue
                if isinstance(child_node, _RefEntry):
                    leaves.append(child_node)
                else:
                    collect(child_node)

        collect(node)
        return tuple(leaves)
