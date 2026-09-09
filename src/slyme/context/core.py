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

import asyncio
import difflib
import inspect
import weakref
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Iterable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, Literal, TypeVar, cast, overload

from .compose import Compose
from .scope import Scope

_T = TypeVar("_T")
_T2 = TypeVar("_T2")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
_Blocked = Enum("_Blocked", ["MARK"])
_BLOCKED = _Blocked.MARK
_DISPOSAL_CHAIN: ContextVar[tuple[object, ...]] = ContextVar(
    "slyme_context_disposal_chain",
    default=(),
)


class _Effect:
    """One cleanup operation owned by a Context."""

    __slots__ = ("owner",)
    is_async: bool

    def __init__(self, owner: Context) -> None:
        self.owner: Context | None = owner

    @staticmethod
    def _is_async_callable(callback: Callable[..., Any]) -> bool:
        unwrapped = inspect.unwrap(callback)
        if inspect.iscoroutinefunction(unwrapped):
            return True
        return callable(unwrapped) and inspect.iscoroutinefunction(
            type(unwrapped).__call__
        )

    @staticmethod
    def _close_awaitable(value: Awaitable[Any]) -> None:
        close = getattr(value, "close", None)
        if close is not None:
            close()

    @staticmethod
    def _observe_task_result(task: asyncio.Task[Any]) -> None:
        if not task.cancelled():
            task.exception()

    def _release(self) -> None:
        owner = self.owner
        self.owner = None
        if owner is not None:
            owner._forget_effect(self)


class _SyncEffect(_Effect):
    __slots__ = ("_cleanup", "_disposed", "_disposing", "_error")
    is_async = False

    def __init__(
        self,
        owner: Context,
        cleanup: Callable[[], None],
    ) -> None:
        super().__init__(owner)
        self._cleanup: Callable[[], None] | None = cleanup
        self._disposed = False
        self._disposing = False
        self._error: BaseException | None = None

    def dispose(self) -> None:
        if self._disposing:
            raise RuntimeError("Context effect disposal cannot be re-entered.")
        if self._disposed:
            if self._error is not None:
                raise self._error
            return
        self._disposing = True
        cleanup = self._cleanup
        self._cleanup = None
        owner = self.owner
        guarded = owner._enter_sync_disposal_guard() if owner is not None else ()
        try:
            if cleanup is None:
                return
            result = cast(Callable[[], Any], cleanup)()
            if inspect.isawaitable(result):
                self._close_awaitable(result)
                raise TypeError(
                    "A synchronous Context effect returned an awaitable; "
                    "use async_effect()."
                )
        except BaseException as error:
            self._error = error
            raise
        finally:
            try:
                self._disposing = False
                self._disposed = True
                self._release()
            finally:
                Context._exit_sync_disposal_guard(guarded)


class _AsyncEffect(_Effect):
    __slots__ = ("_cleanup", "_task")
    is_async = True

    def __init__(
        self,
        owner: Context,
        cleanup: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(owner)
        self._cleanup: Callable[[], Awaitable[None]] | None = cleanup
        self._task: asyncio.Task[None] | None = None

    async def _run(self) -> None:
        cleanup = self._cleanup
        self._cleanup = None
        owners: list[Context] = []
        owner = self.owner
        while owner is not None:
            owners.append(owner)
            owner = owner.parent
        token = _DISPOSAL_CHAIN.set((*_DISPOSAL_CHAIN.get(), self, *owners))
        try:
            if cleanup is None:
                return
            result = cleanup()
            if not inspect.isawaitable(result):
                raise TypeError(
                    "An asynchronous Context effect did not return an awaitable."
                )
            await result
        finally:
            try:
                self._release()
            finally:
                _DISPOSAL_CHAIN.reset(token)

    async def dispose(self) -> None:
        if any(owner is self for owner in _DISPOSAL_CHAIN.get()):
            raise RuntimeError("Context effect disposal cannot await itself.")
        task = self._task
        if task is None:
            task = asyncio.create_task(self._run())
            task.add_done_callback(self._observe_task_result)
            self._task = task
        elif task is asyncio.current_task():
            raise RuntimeError("Context effect disposal cannot await itself.")
        await asyncio.shield(task)


class _ContextState(Enum):
    ACTIVE = "active"
    DISPOSING = "disposing"
    DISPOSED = "disposed"


@dataclass(frozen=True, repr=False)
class Ref(Generic[_T]):
    """Immutable Context dependency handle for one declared dotted path."""

    path: str
    parts: tuple[str, ...] = field(init=False)

    @staticmethod
    def _split_path(path: str) -> tuple[str, ...]:
        if not isinstance(path, str):
            raise TypeError(f"Ref path must be str, got {type(path).__name__}.")
        if not path:
            raise ValueError("Ref path cannot be empty.")
        parts = tuple(path.split("."))
        if any(not part for part in parts):
            raise ValueError(f"Invalid Ref path: {path!r}.")
        return parts

    @staticmethod
    def _validate_name(name: Any, path: str) -> str:
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

    def __post_init__(self) -> None:
        object.__setattr__(self, "parts", self._split_path(self.path))


@dataclass(frozen=True)
class _RefLeafConfig(Generic[_T]):
    """Behavior of one leaf declared in a Schema."""

    value_type: Any | None = None
    replaceable: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.replaceable, bool):
            raise TypeError(
                "Schema.leaf() replaceable must be bool, "
                f"got {type(self.replaceable).__name__}."
            )


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
        if not isinstance(declarations, Mapping):
            raise TypeError(
                "Schema declarations must be a mapping, "
                f"got {type(declarations).__name__}."
            )
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
        elif isinstance(declarations, Mapping):
            incoming = self._build(declarations, declaration_id)
        else:
            raise TypeError(
                "Schema declarations must be a mapping or Schema, "
                f"got {type(declarations).__name__}."
            )

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


ContextKey = str | Ref[Any]
_RefRole = Literal["any", "leaf", "container"]


class ContextPathError(KeyError):
    """Raised when a Context path cannot be resolved or changed as requested."""

    pass


class _ContextBinding(Compose[Any | _Blocked, Any]):
    """Private one-value composition backing one Context leaf."""

    __slots__ = ()

    def __init__(self) -> None:
        def resolve(values: tuple[Any | _Blocked, ...]) -> Any:
            if not values or values[0] is _BLOCKED:
                raise LookupError("Context value is not visible.")
            return values[0]

        super().__init__(resolve)

    def _local_value_entry(self, scope: Scope) -> Any | None:
        return next(
            (
                entry
                for entry in self._buckets.get(scope, {}).values()
                if entry.value is not _BLOCKED
            ),
            None,
        )

    def has_value(self, scope: Scope, *, local: bool = False) -> bool:
        try:
            self.resolve(scope, local=local)
            return True
        except LookupError:
            return False

    def set_value(
        self,
        scope: Scope,
        value: Any,
        *,
        replaceable: bool,
    ) -> None:
        current = self._local_value_entry(scope)
        if current is not None:
            if not replaceable:
                raise ValueError("Context local value is not replaceable.")
            self._remove(scope, current.identity)
        self._insert(scope, value, position="prepend")

    def add_value(self, scope: Scope, value: Any) -> Callable[[], None]:
        if self._local_value_entry(scope) is not None:
            raise ValueError("Context already has a local value.")
        entry = self._insert(scope, value, position="prepend")
        binding_ref = weakref.ref(self)
        identity = entry.identity
        target: Scope | None = scope

        def dispose() -> None:
            nonlocal target
            if target is None:
                return
            binding = binding_ref()
            if binding is not None:
                binding._remove(target, identity)
            target = None

        return dispose

    def delete_value(self, scope: Scope) -> None:
        current = self._local_value_entry(scope)
        if current is not None:
            self._remove(scope, current.identity)

    def block(self, scope: Scope) -> None:
        if any(
            entry.value is _BLOCKED for entry in self._buckets.get(scope, {}).values()
        ):
            return
        self._insert(scope, _BLOCKED, position="append")

    def clear_scope(self, scope: Scope) -> None:
        """Remove every value stored directly at *scope*."""
        self._buckets.pop(scope, None)

    @property
    def empty(self) -> bool:
        return not self._buckets


_ContextData = weakref.WeakKeyDictionary[_RefEntry[Any], _ContextBinding]
_Tree = dict[str, Any]


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

    @abstractmethod
    def _flat_items(
        self,
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        pass

    def flatten(self, *, local: bool = False) -> dict[Ref[Any], Any]:
        """Return the visible Context leaves as a flat Ref-to-value mapping."""
        return dict(self._flat_items(local=local))


@dataclass(frozen=True, repr=False, eq=False, init=False)
class Context(ContextElement):
    """Declared runtime data and reversible effects bound to one Scope."""

    parent: Context | None = field(init=False)
    scope: Scope = field(init=False)
    _data: _ContextData = field(init=False)
    _root: Context = field(init=False)
    _schema: Schema | None = field(init=False)
    _owned: list[Context | _Effect] = field(init=False)
    _scope_counts: dict[Scope, int] | None = field(init=False)
    _state: _ContextState = field(init=False)
    _dispose_task: asyncio.Task[None] | None = field(init=False)
    _dispose_error: BaseException | None = field(init=False)
    _sync_disposal_guard_depth: int = field(init=False)
    _sync_only: bool = field(init=False)

    @staticmethod
    def _set_nested_value(
        tree: _Tree,
        parts: tuple[str, ...],
        value: Any,
    ) -> None:
        current = tree
        for part in parts[:-1]:
            current = current.setdefault(part, {})
        current[parts[-1]] = value

    def __init__(
        self,
        data: Mapping[ContextKey, Any] | None = None,
        *,
        schema: Schema | None = None,
        parent: Context | None = None,
        scope: Scope | None = None,
    ) -> None:
        scope_counts: dict[Scope, int] | None
        if parent is not None and not isinstance(parent, Context):
            raise TypeError("Context parent must be a Context object.")
        if scope is not None and not isinstance(scope, Scope):
            raise TypeError("Context scope must be a Scope object.")

        if parent is not None:
            parent._assert_mutable()
            if schema is not None:
                raise TypeError(
                    "A child Context inherits its schema and cannot provide one "
                    "during construction."
                )
            application_root = parent.root
            root_schema = None
            root_data = application_root._data
            bound_scope = parent.scope if scope is None else scope
            scope_counts = None
            sync_only = parent._sync_only
        else:
            if schema is None:
                root_schema = Schema()
            elif isinstance(schema, Schema):
                root_schema = schema
            else:
                raise TypeError(
                    f"Context schema must be Schema, got {type(schema).__name__}."
                )
            root_data = weakref.WeakKeyDictionary()
            application_root = self
            bound_scope = Scope() if scope is None else scope
            scope_counts = {}
            sync_only = False

        object.__setattr__(self, "parent", parent)
        object.__setattr__(self, "scope", bound_scope)
        object.__setattr__(self, "_data", root_data)
        object.__setattr__(self, "_root", application_root)
        object.__setattr__(self, "_schema", root_schema)
        object.__setattr__(self, "_owned", [])
        object.__setattr__(self, "_scope_counts", scope_counts)
        object.__setattr__(self, "_state", _ContextState.ACTIVE)
        object.__setattr__(self, "_dispose_task", None)
        object.__setattr__(self, "_dispose_error", None)
        object.__setattr__(self, "_sync_disposal_guard_depth", 0)
        object.__setattr__(self, "_sync_only", sync_only)

        self._acquire_scope()
        if parent is not None:
            parent._owned.append(self)
        try:
            if data is not None:
                if not isinstance(data, Mapping):
                    raise TypeError(
                        f"Context data must be a mapping, got {type(data).__name__}."
                    )
                self.update(data)
        except BaseException:
            self._release_scope()
            if parent is not None:
                parent._remove_child(self)
            object.__setattr__(self, "_state", _ContextState.DISPOSED)
            raise

    def _assert_readable(self) -> None:
        if self._state is _ContextState.DISPOSED:
            raise RuntimeError("Context has been disposed.")

    def _assert_mutable(self) -> None:
        if self._state is _ContextState.DISPOSING:
            raise RuntimeError("Context is being disposed.")
        if self._state is _ContextState.DISPOSED:
            raise RuntimeError("Context has been disposed.")
        ancestor = self.parent
        while ancestor is not None:
            if ancestor._state is _ContextState.DISPOSING:
                raise RuntimeError("An ancestor Context is being disposed.")
            if ancestor._state is _ContextState.DISPOSED:
                raise RuntimeError("An ancestor Context has been disposed.")
            ancestor = ancestor.parent

    def _enter_sync_disposal_guard(self) -> tuple[Context, ...]:
        guarded: list[Context] = []
        current: Context | None = self
        while current is not None:
            object.__setattr__(
                current,
                "_sync_disposal_guard_depth",
                current._sync_disposal_guard_depth + 1,
            )
            guarded.append(current)
            current = current.parent
        return tuple(guarded)

    @staticmethod
    def _exit_sync_disposal_guard(guarded: tuple[Context, ...]) -> None:
        for context in guarded:
            object.__setattr__(
                context,
                "_sync_disposal_guard_depth",
                context._sync_disposal_guard_depth - 1,
            )

    def _assert_disposal_allowed(self) -> None:
        if self._sync_disposal_guard_depth or any(
            owner is self for owner in _DISPOSAL_CHAIN.get()
        ):
            raise RuntimeError(
                "Context disposal cannot be re-entered from effect setup or cleanup."
            )

    def _acquire_scope(self) -> None:
        counts = cast(dict[Scope, int], self.root._scope_counts)
        for scope in self.scope.mro:
            counts[scope] = counts.get(scope, 0) + 1

    def _release_scope(self) -> None:
        counts = cast(dict[Scope, int], self.root._scope_counts)
        expired: list[Scope] = []
        for scope in self.scope.mro:
            remaining = counts[scope] - 1
            if remaining:
                counts[scope] = remaining
            else:
                counts.pop(scope)
                expired.append(scope)

        if not expired:
            return
        for entry, binding in tuple(self._data.items()):
            for scope in expired:
                binding.clear_scope(scope)
            if binding.empty:
                self._data.pop(entry, None)

    def _remove_child(self, child: Context) -> None:
        if child in self._owned:
            self._owned.remove(child)

    def _forget_effect(self, effect: _Effect) -> None:
        if effect in self._owned:
            self._owned.remove(effect)

    def _adopt_sync_effect(
        self,
        cleanup: Callable[[], None],
    ) -> Callable[[], None]:
        self._assert_mutable()
        if not callable(cleanup):
            raise TypeError("Context effect cleanup must be callable.")
        if _Effect._is_async_callable(cleanup):
            raise TypeError(
                "Context.effect() requires synchronous cleanup; use async_effect()."
            )
        effect = _SyncEffect(self, cleanup)
        self._owned.append(effect)
        return effect.dispose

    def _adopt_async_effect(
        self,
        cleanup: Callable[[], Awaitable[None]],
    ) -> Callable[[], Awaitable[None]]:
        self._assert_mutable()
        if not callable(cleanup):
            raise TypeError("Context effect cleanup must be callable.")
        effect = _AsyncEffect(self, cleanup)
        self._owned.append(effect)
        return effect.dispose

    def effect(
        self,
        setup: Callable[[], Callable[[], None]],
    ) -> Callable[[], None]:
        """Run synchronous setup and own its cleanup until Context disposal.

        Setup and cleanup cannot dispose this Context or one of its ancestors.
        """
        self._assert_mutable()
        if not callable(setup):
            raise TypeError("Context effect setup must be callable.")
        guarded = self._enter_sync_disposal_guard()
        try:
            cleanup = cast(Callable[[], Any], setup)()
        finally:
            self._exit_sync_disposal_guard(guarded)
        if inspect.isawaitable(cleanup):
            _Effect._close_awaitable(cleanup)
            raise TypeError(
                "Context.effect() setup returned an awaitable; setup must be "
                "synchronous."
            )
        return self._adopt_sync_effect(cleanup)

    def async_effect(
        self,
        setup: Callable[[], Callable[[], Awaitable[None]]],
    ) -> Callable[[], Awaitable[None]]:
        """Run synchronous setup and own its asynchronous cleanup.

        Setup and cleanup cannot dispose this Context or one of its ancestors.
        """
        self._assert_mutable()
        if self._sync_only:
            raise RuntimeError(
                "A synchronous evaluation Context cannot own asynchronous cleanup."
            )
        if not callable(setup):
            raise TypeError("Context effect setup must be callable.")
        guarded = self._enter_sync_disposal_guard()
        try:
            cleanup = cast(Callable[[], Any], setup)()
        finally:
            self._exit_sync_disposal_guard(guarded)
        if inspect.isawaitable(cleanup):
            _Effect._close_awaitable(cleanup)
            raise TypeError(
                "Context.async_effect() setup returned an awaitable; setup must be "
                "synchronous."
            )
        return self._adopt_async_effect(cleanup)

    def _preflight_sync_dispose(self) -> None:
        if self._state is _ContextState.DISPOSED:
            return
        if self._state is _ContextState.DISPOSING:
            raise RuntimeError("Context disposal is already in progress.")
        for owned in self._owned:
            if isinstance(owned, Context):
                owned._preflight_sync_dispose()
            elif owned.is_async:
                raise RuntimeError(
                    "Context owns asynchronous cleanup; use await async_dispose()."
                )

    def _finish_dispose(self, error: BaseException | None) -> None:
        self._owned.clear()
        self._release_scope()
        if self.parent is not None:
            self.parent._remove_child(self)
        object.__setattr__(self, "_dispose_error", error)
        object.__setattr__(self, "_state", _ContextState.DISPOSED)

    def dispose(self) -> None:
        """Dispose synchronous ownership once and reproduce its terminal failure."""
        self._assert_disposal_allowed()
        if self._state is _ContextState.DISPOSED:
            if self._dispose_error is not None:
                raise self._dispose_error
            return
        self._preflight_sync_dispose()
        object.__setattr__(self, "_state", _ContextState.DISPOSING)
        first_error: BaseException | None = None
        try:
            for owned in reversed(tuple(self._owned)):
                try:
                    if isinstance(owned, Context):
                        owned.dispose()
                    else:
                        cast(_SyncEffect, owned).dispose()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        finally:
            self._finish_dispose(first_error)
        if first_error is not None:
            raise first_error

    async def _run_async_dispose(self) -> None:
        token = _DISPOSAL_CHAIN.set((*_DISPOSAL_CHAIN.get(), self))
        first_error: BaseException | None = None
        try:
            for owned in reversed(tuple(self._owned)):
                try:
                    if isinstance(owned, Context):
                        await owned.async_dispose()
                    elif isinstance(owned, _AsyncEffect):
                        await owned.dispose()
                    else:
                        cast(_SyncEffect, owned).dispose()
                except BaseException as error:
                    if first_error is None:
                        first_error = error
        finally:
            try:
                self._finish_dispose(first_error)
            finally:
                _DISPOSAL_CHAIN.reset(token)
        if first_error is not None:
            raise first_error

    async def async_dispose(self) -> None:
        """Dispose all ownership once and reproduce its terminal failure."""
        self._assert_disposal_allowed()
        task = self._dispose_task
        if task is not None:
            if task is asyncio.current_task():
                raise RuntimeError("Context disposal cannot await itself.")
            await asyncio.shield(task)
            return
        if self._state is _ContextState.DISPOSED:
            if self._dispose_error is not None:
                raise self._dispose_error
            return
        if self._state is _ContextState.DISPOSING:
            raise RuntimeError("Synchronous Context disposal is already in progress.")
        object.__setattr__(self, "_state", _ContextState.DISPOSING)
        task = asyncio.create_task(self._run_async_dispose())
        task.add_done_callback(_Effect._observe_task_result)
        object.__setattr__(self, "_dispose_task", task)
        await asyncio.shield(task)

    @property
    def root(self) -> Context:
        """Return the application root shared by this Context hierarchy."""
        return self._root

    @property
    def schema(self) -> Schema:
        """Return the live Schema shared by this Context hierarchy."""
        return cast(Schema, self.root._schema)

    def declare(
        self,
        declarations: Schema | Mapping[str, Any],
    ) -> Callable[[], None]:
        """Declare shared Schema paths owned by this Context."""
        return self.effect(lambda: self.schema.declare(declarations))

    def _validate_entry(
        self,
        key: ContextKey,
        *,
        role: _RefRole = "any",
    ) -> _RefEntry[Any]:
        self._assert_readable()
        if isinstance(key, str):
            path = key
        elif isinstance(key, Ref):
            path = key.path
        else:
            raise TypeError(
                f"Context keys must be str or Ref, got {type(key).__name__}."
            )

        try:
            node = self.schema._resolve_node(path)
        except (KeyError, TypeError, ValueError) as error:
            if isinstance(key, Ref):
                raise ContextPathError(
                    f"Ref path {path!r} is not declared by this Context."
                ) from error
            raise ContextPathError(str(error)) from error

        kind: Literal["leaf", "container"]
        if isinstance(node, _RefEntry):
            kind = "leaf"
            entry = node
        else:
            kind = "container"
            entry = Schema._container_entry(node)
        if role != "any" and role != kind:
            raise ContextPathError(
                f"Ref path {path!r} is declared as a {kind}, not a {role}."
            )
        return entry

    def _validate_ref(
        self,
        key: ContextKey,
        *,
        role: _RefRole = "any",
    ) -> Ref[Any]:
        return self._validate_entry(key, role=role).ref

    def fork(self, *, scope: Scope | None = None) -> Context:
        """Create an owned child sharing this Context's Scope by default."""
        self._assert_mutable()
        return type(self)(parent=self, scope=scope)

    def _fork_for_auto(self, *, synchronous: bool) -> Context:
        child = self.fork(scope=self.scope.fork())
        object.__setattr__(
            child,
            "_sync_only",
            synchronous,
        )
        return child

    def isolate(self, *refs: ContextKey) -> Context:
        """Create a child that blocks inherited values for selected leaves."""
        self._assert_mutable()
        entries = tuple(self._validate_entry(ref, role="leaf") for ref in refs)
        child = self.fork(scope=self.scope.fork())
        try:
            for entry in entries:
                child._binding(entry, create=True).block(child.scope)
        except BaseException:
            child.dispose()
            raise
        return child

    @overload
    def _binding(
        self,
        entry: _RefEntry[Any],
        *,
        create: Literal[True],
    ) -> _ContextBinding: ...

    @overload
    def _binding(
        self,
        entry: _RefEntry[Any],
        *,
        create: Literal[False],
    ) -> _ContextBinding | None: ...

    def _binding(
        self,
        entry: _RefEntry[Any],
        *,
        create: bool,
    ) -> _ContextBinding | None:
        binding = self._data.get(entry)
        if binding is None and create:
            binding = _ContextBinding()
            self._data[entry] = binding
        return binding

    def _leaf_value(self, entry: _RefEntry[Any], *, local: bool) -> Any:
        binding = self._binding(entry, create=False)
        if binding is None:
            raise ContextPathError(entry.ref.path)
        try:
            return binding.resolve(self.scope, local=local)
        except LookupError as error:
            raise ContextPathError(entry.ref.path) from error

    def _leaf_items(
        self,
        parts: tuple[str, ...],
        *,
        local: bool,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        for entry in self.schema._leaf_entries(parts):
            try:
                value = self._leaf_value(entry, local=local)
            except ContextPathError:
                continue
            yield entry.ref, value

    def _entry_value(self, entry: _RefEntry[Any], *, local: bool) -> Any:
        if isinstance(entry.config, _RefLeafConfig):
            return self._leaf_value(entry, local=local)
        if not any(self._leaf_items(entry.ref.parts, local=local)):
            raise ContextPathError(entry.ref.path)
        return ContextView(self, entry.ref.parts)

    def _flat_items(
        self,
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        self._assert_readable()
        return self._leaf_items((), local=local)

    def extract(self, ref_tree: Any, *, local: bool = False) -> Any:
        self._assert_readable()
        entry_tree = CTX_EVAL_ENGINE.map(self._validate_entry, ref_tree)
        entries, treedef = CTX_EVAL_ENGINE.flatten(entry_tree)
        values = [self._entry_value(entry, local=local) for entry in entries]
        return CTX_EVAL_ENGINE.unflatten(treedef, values)

    # --- Read Operations ---
    def get(
        self,
        ref: ContextKey,
        default: _T2 | _Missing = _MISSING,
        *,
        local: bool = False,
    ) -> Any:
        entry = self._validate_entry(ref)
        try:
            return self._entry_value(entry, local=local)
        except ContextPathError:
            if default is _MISSING:
                raise
            return default

    def exists(self, ref: ContextKey, *, local: bool = False) -> bool:
        entry = self._validate_entry(ref)
        if isinstance(entry.config, _RefLeafConfig):
            try:
                self._leaf_value(entry, local=local)
                return True
            except ContextPathError:
                return False
        return any(self._leaf_items(entry.ref.parts, local=local))

    def keys(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> Iterable[str]:
        self._assert_readable()
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        visible = tuple(self._leaf_items(parts, local=local))
        if ref is not None and not visible:
            raise ContextPathError(".".join(parts))

        result: list[str] = []
        for name in self.schema._child_names(parts):
            child_parts = (*parts, name)
            if any(
                leaf.parts[: len(child_parts)] == child_parts for leaf, _ in visible
            ):
                result.append(name)
        return tuple(result)

    def _snapshot_tree(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> _Tree:
        self._assert_readable()
        parts = () if ref is None else self._validate_ref(ref, role="container").parts
        visible = tuple(self._leaf_items(parts, local=local))
        if ref is not None and not visible:
            raise ContextPathError(".".join(parts))

        result: _Tree = {}
        for leaf, value in visible:
            self._set_nested_value(result, leaf.parts[len(parts) :], value)
        return result

    def to_dict(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._snapshot_tree(ref, local=local)

    # --- Unified Modification Interface ---
    def mutate(
        self,
        *,
        updates: Mapping[ContextKey, Any] | None = None,
        drops: Iterable[ContextKey] | None = None,
    ) -> None:
        """
        Validate all local updates and drops before applying them.

        Args:
            updates: A mapping of References to new values.
            drops: An iterable of References to remove.

        Validation failures leave existing bindings unchanged.
        """
        self._assert_mutable()
        if not updates and not drops:
            return None

        normalized_updates: dict[_RefEntry[Any], Any] = (
            {self._validate_entry(k, role="leaf"): v for k, v in updates.items()}
            if updates
            else {}
        )
        normalized_drops: set[_RefEntry[Any]] = (
            {self._validate_entry(ref) for ref in drops} if drops else set()
        )
        dropped_leaves: set[_RefEntry[Any]] = set()
        for entry in normalized_drops:
            if isinstance(entry.config, _RefLeafConfig):
                dropped_leaves.add(entry)
            else:
                dropped_leaves.update(self.schema._leaf_entries(entry.ref.parts))

        for entry in normalized_updates:
            binding = self._binding(entry, create=False)
            config = cast(_RefLeafConfig[Any], entry.config)
            if (
                entry not in dropped_leaves
                and binding is not None
                and binding.has_value(self.scope, local=True)
                and not config.replaceable
            ):
                raise ContextPathError(
                    f"Cannot replace non-replaceable local path {entry.ref.path!r}; "
                    "delete it or use a Context bound to a child Scope."
                )

        for entry in dropped_leaves:
            binding = self._binding(entry, create=False)
            if binding is not None:
                binding.delete_value(self.scope)
        for entry, value in normalized_updates.items():
            config = cast(_RefLeafConfig[Any], entry.config)
            try:
                self._binding(entry, create=True).set_value(
                    self.scope,
                    value,
                    replaceable=config.replaceable or entry in dropped_leaves,
                )
            except ValueError as error:
                raise ContextPathError(
                    f"Cannot replace non-replaceable local path "
                    f"{entry.ref.path!r}; delete it or use a Context bound to "
                    "a child Scope."
                ) from error

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
        """Add one local binding owned by this Context."""
        self._assert_mutable()
        entry = self._validate_entry(ref, role="leaf")
        binding = self._binding(entry, create=True)
        if binding.has_value(self.scope, local=True):
            raise ContextPathError(
                f"Cannot add existing local path {entry.ref.path!r}."
            )
        try:
            cleanup = binding.add_value(self.scope, value)
        except ValueError as error:
            raise ContextPathError(
                f"Cannot add existing local path {entry.ref.path!r}."
            ) from error
        try:
            return self._adopt_sync_effect(cleanup)
        except BaseException:
            cleanup()
            raise

    def contribute(
        self,
        ref: ContextKey,
        value: _T,
        *,
        scope: Scope | None = None,
        metadata: Mapping[str, Any] | None = None,
        position: Literal["prepend", "append"] = "append",
    ) -> Callable[[], None]:
        """Add an owned value to the Compose stored at *ref*."""
        self._assert_mutable()
        target = self.scope if scope is None else scope
        if not isinstance(target, Scope):
            raise TypeError(
                f"Context contribution scope must be Scope, got "
                f"{type(target).__name__}."
            )
        composition = self.get(ref)
        if not isinstance(composition, Compose):
            raise TypeError(
                f"Context path {self._validate_ref(ref).path!r} does not hold Compose."
            )
        cleanup = composition.add(
            target,
            value,
            metadata=metadata,
            position=position,
        )
        try:
            return self._adopt_sync_effect(cleanup)
        except BaseException:
            cleanup()
            raise

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
            return self._context._validate_ref(".".join(self._parts))
        if isinstance(ref, str):
            relative_parts = Ref._split_path(ref)
            return self._context._validate_ref(
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

    def _flat_items(
        self,
        *,
        local: bool = False,
    ) -> Iterable[tuple[Ref[Any], Any]]:
        entry = self._context._validate_entry(
            ".".join(self._parts),
            role="container",
        )
        items = tuple(self._context._leaf_items(entry.ref.parts, local=local))
        if not items:
            raise ContextPathError(entry.ref.path)
        return items

    def to_dict(
        self,
        ref: ContextKey | None = None,
        *,
        local: bool = False,
    ) -> dict[str, Any]:
        return self._context.to_dict(self._adjust_ref(ref), local=local)


from .tree import CTX_EVAL_ENGINE
