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

"""Native functions with keyword bindings, composition, and optional result binding."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Generator, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import (
    Any,
    Generic,
    ParamSpec,
    Protocol,
    TypeAlias,
    TypeVar,
    cast,
    overload,
)

from slyme.context import (
    DATA_TREE_REF,
    Context,
    ContextKey,
    ContextPathError,
    Metadata,
    RefLeafConfig,
)
from slyme.execution import Continuation, continuation
from slyme.tree import get_element, iter_with_key_path

_P = ParamSpec("_P")
_R = TypeVar("_R")
_S = TypeVar("_S", covariant=True)
_V = TypeVar("_V")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


@dataclass(frozen=True, slots=True)
class Auto(Generic[_S]):
    value: _S


def auto(value: _V) -> Auto[_V]:
    """Mark a value tree for evaluation at invocation."""
    return Auto(value)


class Node(Generic[_P, _R]):
    """A callable with keyword bindings that can write its result to Context."""

    __slots__ = ("_func", "_inject_ctx", "_params", "_result_to", "_metadata")

    @overload
    def __init__(
        self,
        *,
        func: Callable[_P, Awaitable[_R]],
        inject_ctx: bool = False,
        params: Mapping[str, Any] | None = None,
        result_to: Any = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        *,
        func: Callable[_P, _R | Awaitable[_R]],
        inject_ctx: bool = False,
        params: Mapping[str, Any] | None = None,
        result_to: Any = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None: ...
    def __init__(
        self,
        *,
        func: Callable[_P, _R | Awaitable[_R]],
        inject_ctx: bool = False,
        params: Mapping[str, Any] | None = None,
        result_to: Any = None,
        metadata: Mapping[str, Metadata] | None = None,
    ) -> None:
        self._func = func
        self._inject_ctx = inject_ctx
        self._params: Mapping[str, Any] = MappingProxyType(
            {} if params is None else dict(params)
        )
        self._result_to = result_to
        self._metadata: Mapping[str, Metadata] = MappingProxyType(
            {} if metadata is None else dict(metadata)
        )

    @property
    def func(self) -> Callable[_P, _R | Awaitable[_R]]:
        return self._func

    @property
    def inject_ctx(self) -> bool:
        return self._inject_ctx

    @property
    def params(self) -> Mapping[str, Any]:
        return self._params

    @property
    def result_to(self) -> Any:
        return self._result_to

    @property
    def metadata(self) -> Mapping[str, Metadata]:
        return self._metadata

    def get(self, name: str, default: Any = _MISSING) -> Any:
        try:
            return self._params[name]
        except KeyError:
            if default is _MISSING:
                raise
            return default

    def partial(self, /, **params: Any) -> Node[_P, _R]:
        """Return a new Node with merged, unevaluated keyword bindings."""
        return self.replace(params={**self.params, **params})

    def auto_partial(self, /, **params: Any) -> Node[_P, _R]:
        """Bind each supplied value as auto(value) in a new Node."""
        return self.partial(**{name: auto(value) for name, value in params.items()})

    def to(self, result_to: Any) -> Node[_P, _R]:
        """Set result destinations in a new Node, or disable binding with None."""
        return self.replace(result_to=result_to)

    def replace(
        self,
        *,
        params: Mapping[str, Any] | None | _Missing = _MISSING,
        result_to: Any = _MISSING,
        metadata: Mapping[str, Metadata] | None | _Missing = _MISSING,
    ) -> Node[_P, _R]:
        return Node(
            func=self.func,
            inject_ctx=self.inject_ctx,
            params=self.params if params is _MISSING else params,
            result_to=self.result_to if result_to is _MISSING else result_to,
            metadata=self.metadata if metadata is _MISSING else metadata,
        )

    def _publish_result(self, ctx: Context, result: _R) -> None:
        rules = ctx.get(DATA_TREE_REF).resolve(ctx.scope)
        updates = [
            (destination, get_element(result, path))
            for path, destination in iter_with_key_path(self.result_to, rules=rules)
        ]
        entries = ctx.resolve_entries(
            (destination for destination, _ in updates), role="leaf"
        )
        assignments: dict[ContextKey, Any] = {}
        registrations: dict[ContextKey, Any] = {}
        for entry, (_, value) in zip(entries, updates, strict=True):
            if cast(RefLeafConfig, entry.config).mode == "register":
                if entry.ref in registrations:
                    raise ContextPathError(
                        f"Duplicate registration result path {entry.ref.path!r}."
                    )
                registrations[entry.ref] = value
            else:
                assignments[entry.ref] = value
        if registrations:
            ctx.register_many(registrations)
        if assignments:
            ctx.set_many(assignments)

    @continuation
    def __call__(self, ctx: Context, /, **kwargs: Any) -> Generator[Any, Any, _R]:
        try:
            # Keep .func typed while accepting dynamic invocation kwargs.
            func: Callable[..., _R | Awaitable[_R]] = self.func
            params = {**self.params, **kwargs}
            raw: dict[str, Any] = {}
            deferred: dict[str, Any] = {}
            for name, value in params.items():
                if isinstance(value, Auto):
                    deferred[name] = value.value
                else:
                    raw[name] = value
            if deferred:
                raw.update((yield eval_tree.flat_call(ctx, deferred)))
            args = (ctx,) if self.inject_ctx else ()
            result = yield (
                func.flat_call(*args, **raw)
                if isinstance(func, Continuation)
                else func(*args, **raw)
            )
            if self.result_to is not None:
                self._publish_result(ctx, result)
            return result
        except BaseException:
            # Reserved for runtime trace hooks.
            raise


AnyNode: TypeAlias = Node[..., _R]


def wrap(outer: Node[_P, _R], /, *inner: AnyNode[Any]) -> Node[_P, _R]:
    """Bind Nodes from outermost to innermost, with the target last."""
    if not inner:
        return outer
    wrapped = inner[-1]
    for layer in reversed(inner[:-1]):
        wrapped = layer.partial(wrapped=wrapped)
    return outer.partial(wrapped=wrapped)


class _NodeDecorator(Protocol):
    @overload
    def __call__(self, func: Callable[_P, Awaitable[_R]], /) -> Node[_P, _R]: ...
    @overload
    def __call__(self, func: Callable[_P, _R | Awaitable[_R]], /) -> Node[_P, _R]: ...


@overload
def node(
    func: Callable[_P, Awaitable[_R]],
    /,
    *,
    inject_ctx: bool = False,
    params: Mapping[str, Any] | None = None,
    result_to: Any = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Node[_P, _R]: ...
@overload
def node(
    func: Callable[_P, _R | Awaitable[_R]],
    /,
    *,
    inject_ctx: bool = False,
    params: Mapping[str, Any] | None = None,
    result_to: Any = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Node[_P, _R]: ...
@overload
def node(
    func: None = None,
    /,
    *,
    inject_ctx: bool = False,
    params: Mapping[str, Any] | None = None,
    result_to: Any = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> _NodeDecorator: ...
def node(
    func: Callable[..., Any] | None = None,
    /,
    *,
    inject_ctx: bool = False,
    params: Mapping[str, Any] | None = None,
    result_to: Any = None,
    metadata: Mapping[str, Metadata] | None = None,
) -> Any:
    """Adapt a function into a Node, optionally passing it the execution Context."""

    def decorate(target: Callable[..., Any]) -> AnyNode[Any]:
        return Node(
            func=target,
            inject_ctx=inject_ctx,
            params=params,
            result_to=result_to,
            metadata=metadata,
        )

    return decorate if func is None else decorate(func)


from .eval import eval_tree
