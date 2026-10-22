"""Inspect object structure and environment-dependent values through explicit rules."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from typing_extensions import NotRequired, TypedDict

from slyme.tree import TreeResolver, TreeRules, flatten_one
from slyme.utils.exception import enrich_exception

InspectionReference = TypedDict("InspectionReference", {"$ref": str})
"""A reference to one record in InspectionGraph.objects."""


class InspectionEntry(TypedDict):
    """A named relationship to another record, with an optional key reference."""

    role: str
    key: NotRequired[InspectionReference]
    value: InspectionReference


class InspectionObject(TypedDict):
    """An object or interpretation record, with its relationships and optional raw value."""

    kind: str
    value: NotRequired[object]
    type: NotRequired[str]
    label: NotRequired[str]
    attributes: NotRequired[dict[str, object]]
    entries: list[InspectionEntry]


class InspectionGraph(TypedDict):
    """The root reference and object table returned by inspect()."""

    root: InspectionReference
    objects: dict[str, InspectionObject]


_INHERIT = object()
_NO_KEY = object()


@dataclass(frozen=True)
class Interpretation:
    """An Interpreter result; omit env to inherit, or pass None to clear it."""

    value: object
    env: object = _INHERIT


Interpreter = Callable[[Any, object], Interpretation | None]
"""Synchronously interpret (value, env); return None when unavailable."""

InterpreterResolver = Callable[[object, object], Interpreter | bool]
"""Select an Interpreter for (value, env); True skips, False uses type lookup."""


@dataclass
class _Object:
    # Keep raw values alive: using id alone would alias short-lived projections.
    value: object
    ref: InspectionReference
    data: InspectionObject
    expanded: bool = False
    children: list[tuple[object, object]] = field(default_factory=list)


def _type_label(value: object) -> str:
    cls = type(value)
    return f"{type.__getattribute__(cls, '__module__')}.{type.__getattribute__(cls, '__qualname__')}"


class _Inspector:
    """Per-inspection identity caches, traversal queue and output limits."""

    def __init__(
        self,
        tree_rules: TreeRules,
        tree_resolver: TreeResolver | None,
        interpreters: Mapping[type, Interpreter],
        interpreter_resolver: InterpreterResolver | None,
        type_names: Mapping[type, str],
        raw_value_types: Collection[type],
        budget: int | None,
        max_depth: int | None,
    ) -> None:
        self.tree_rules = tree_rules
        self.tree_resolver = tree_resolver
        self.interpreters = dict(interpreters)
        self.interpreter_resolver = interpreter_resolver
        self.type_names = dict(type_names)
        self.raw_value_types = frozenset(raw_value_types)
        self.budget = budget
        self.max_depth = max_depth
        self.exhausted = False
        self.objects: dict[str, InspectionObject] = {}
        self.sources: dict[int, _Object] = {}
        self.pending: deque[tuple[_Object, object, int]] = deque()
        self.seen: set[tuple[int, int]] = set()
        self.envs: dict[int, object] = {}
        self.entry_count = 0

    def record(self, data: InspectionObject) -> InspectionReference:
        """Allocate a graph record after its budget has been checked."""
        key = str(len(self.objects))
        self.objects[key] = data
        return {"$ref": key}

    def fits(self, data: InspectionObject, cost: int = 1) -> bool:
        """Check capacity; on failure mark truncation and stop further additions."""
        if self.exhausted or (
            self.budget is not None
            and len(self.objects) + self.entry_count + cost > self.budget
        ):
            self.exhausted = True
            self.truncate(data, "budget")
            return False
        return True

    def visit(self, source: _Object, env: object, depth: int) -> None:
        """Queue each object/environment pair once."""
        key = (id(source.value), id(env))
        if key not in self.seen:
            self.seen.add(key)
            self.envs[id(env)] = env
            self.pending.append((source, env, depth))

    def reference(
        self, value: object, env: object, depth: int, *, follow: bool = True
    ) -> InspectionReference:
        """Get or create an object's identity record and optionally queue inspection."""
        source = self.sources.get(id(value))
        if source is None:
            data: InspectionObject = {
                "kind": "opaque",
                "label": _type_label(value),
                "entries": [],
            }
            if type(value) in self.raw_value_types:
                data["value"] = value
            semantic_type = self.type_names.get(type(value))
            if semantic_type is not None:
                data["type"] = semantic_type
            ref = self.record(data)
            source = self.sources[id(value)] = _Object(value, ref, data)
        if follow:
            self.visit(source, env, depth)
        return source.ref

    @staticmethod
    def truncate(data: InspectionObject, reason: str) -> None:
        """Mark a record as incomplete because content was omitted."""
        data.setdefault("attributes", {}).update(truncated=True, reason=reason)

    def append(
        self,
        data: InspectionObject,
        role: str,
        value: InspectionReference,
        key: InspectionReference | object = _NO_KEY,
    ) -> None:
        """Append an already-admitted relationship between existing records."""
        entry: InspectionEntry = {"role": role, "value": value}
        if key is not _NO_KEY:
            entry["key"] = cast(InspectionReference, key)
        data["entries"].append(entry)
        self.entry_count += 1

    def append_value(
        self,
        data: InspectionObject,
        role: str,
        value: object,
        env: object,
        depth: int,
        *,
        key: object = _NO_KEY,
        follow: bool = True,
    ) -> bool:
        """Admit a relationship within budget; apply depth limits to followed targets."""
        if follow and self.max_depth is not None and depth > self.max_depth:
            self.truncate(data, "max_depth")
            return False
        # Admit the edge and all new targets together, keeping every record reachable.
        targets = (value,) if key is _NO_KEY else (value, key)
        cost = 1 + len({id(item) for item in targets if id(item) not in self.sources})
        if not self.fits(data, cost):
            return False
        ref = self.reference(value, env, depth, follow=follow)
        key_ref = _NO_KEY if key is _NO_KEY else self.reference(key, env, depth)
        self.append(data, role, ref, key_ref)
        return True

    def expand(self, source: _Object, env: object, depth: int) -> None:
        """Cache structural children once and visit them under the current environment."""
        if source.expanded:
            if (
                source.children
                and self.max_depth is not None
                and depth >= self.max_depth
            ):
                self.truncate(source.data, "max_depth")
                return
            for child_key, child in source.children:
                self.reference(child, env, depth + 1)
                self.reference(child_key, env, depth + 1)
            return
        source.expanded = True
        result = flatten_one(
            source.value, rules=self.tree_rules, resolver=self.tree_resolver
        )
        if result is None:
            return
        _, children, aux = result
        source.data["kind"] = "object"
        for index, child in enumerate(children):
            key = index if aux.children_keys is None else aux.children_keys[index]
            if not self.append_value(
                source.data, "child", child, env, depth + 1, key=key
            ):
                break
            source.children.append((key, child))

    def interpret(self, source: _Object, env: object, depth: int) -> None:
        """Select an interpreter and record its result in the current environment."""
        selected = (
            False
            if self.interpreter_resolver is None
            else self.interpreter_resolver(source.value, env)
        )
        if selected is True:
            return
        interpreter = (
            self.interpreters.get(type(source.value)) if selected is False else selected
        )
        if interpreter is None:
            return
        # One interpretation record, two edges, and an environment record if absent.
        if not self.fits(source.data, 3 + (id(env) not in self.sources)):
            return
        interpretation: InspectionObject = {
            "kind": "interpretation",
            "attributes": {"state": "unavailable"},
            "entries": [],
        }
        ref = self.record(interpretation)
        self.append(source.data, "interpretation", ref)
        self.append(
            interpretation, "env", self.reference(env, env, depth, follow=False)
        )
        result = interpreter(source.value, env)
        if result is None:
            return
        interpretation["attributes"]["state"] = "resolved"
        value_env = env if result.env is _INHERIT else result.env
        if value_env is not env and not self.append_value(
            interpretation, "value_env", value_env, value_env, depth + 1, follow=False
        ):
            return
        self.append_value(interpretation, "value", result.value, value_env, depth + 1)

    def inspect(self, value: object, env: object) -> InspectionGraph:
        """Build the graph breadth-first from structure and interpretations."""
        root = self.reference(value, env, 0)
        while self.pending:
            source, current_env, depth = self.pending.popleft()
            operation = "structure"
            try:
                self.expand(source, current_env, depth)
                operation = "interpretation"
                self.interpret(source, current_env, depth)
            except Exception as error:
                enrich_exception(
                    error,
                    f"Inspection {operation}: object {source.ref['$ref']} "
                    f"({_type_label(source.value)}), env {_type_label(current_env)}, "
                    f"depth {depth}.",
                )
                raise
        return {
            "root": root,
            "objects": self.objects,
        }


def inspect(
    value: object,
    *,
    env: object = None,
    tree_rules: TreeRules,
    tree_resolver: TreeResolver | None = None,
    interpreters: Mapping[type, Interpreter] | None = None,
    interpreter_resolver: InterpreterResolver | None = None,
    type_names: Mapping[type, str] | None = None,
    raw_value_types: Collection[type] = (),
    budget: int | None = None,
    max_depth: int | None = None,
) -> InspectionGraph:
    """Build an object graph from structure and environment-dependent interpretations.

    Rules match exact types; tree_resolver must use takes_aux=False.
    Only values whose exact types are in raw_value_types retain their raw value.
    Consumers own serialization. budget counts records plus entries; max_depth
    limits followed relationships from root depth zero. None leaves either unlimited.
    Callbacks must be synchronous, stable and side-effect-free; exceptions propagate.
    """
    if tree_resolver is not None and tree_resolver.takes_aux:
        raise ValueError("Inspection requires a resolver with takes_aux=False.")
    if budget is not None and budget < 1:
        raise ValueError("Inspection budget must be positive.")
    if max_depth is not None and max_depth < 0:
        raise ValueError("Inspection max_depth must be nonnegative.")
    return _Inspector(
        tree_rules,
        tree_resolver,
        {} if interpreters is None else interpreters,
        interpreter_resolver,
        {} if type_names is None else type_names,
        raw_value_types,
        budget,
        max_depth,
    ).inspect(value, env)
