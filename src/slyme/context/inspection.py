"""Context structure and synchronous observations for slyme.inspection."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, TypedDict

from slyme.inspection.core import Interpretation, Interpreter
from slyme.tree import AttributeKey, TreeAux, TreeHandler, TreeRules

from .compose import Compose
from .core import Context, ContextView
from .default import (
    INSPECTION_INTERPRETERS_REF,
    INSPECTION_RAW_VALUE_TYPES_REF,
    INSPECTION_TREE_RULES_REF,
    INSPECTION_TYPE_NAMES_REF,
)
from .schema import Ref, RefContainerConfig, RefDeclaration, RefEntry, RefLeafConfig
from .scope import Identity, Scope, ScopeBinding


class _InspectionOptions(TypedDict):
    tree_rules: TreeRules
    interpreters: dict[type, Interpreter]
    type_names: dict[type, str]
    raw_value_types: dict[type, None]


def resolve_inspection_options(ctx: Context) -> _InspectionOptions:
    """Resolve scoped configuration into shallow keyword-argument snapshots for inspect().

    Does not select an env; missing configuration raises.
    """
    return {
        "tree_rules": ctx.get(INSPECTION_TREE_RULES_REF).resolve(ctx.scope),
        "interpreters": ctx.get(INSPECTION_INTERPRETERS_REF).resolve(ctx.scope),
        "type_names": ctx.get(INSPECTION_TYPE_NAMES_REF).resolve(ctx.scope),
        "raw_value_types": ctx.get(INSPECTION_RAW_VALUE_TYPES_REF).resolve(ctx.scope),
    }


def _attributes(*names: str) -> TreeHandler:
    # Only declared framework attributes; never enumerate arbitrary properties.
    aux = TreeAux(children_keys=tuple(AttributeKey(name) for name in names))
    return TreeHandler(
        lambda value: ((getattr(value, name) for name in names), aux), None
    )


CONTEXT_INSPECTION_TREE_RULES = TreeRules(
    {
        Context: _attributes(
            "state", "dispose_mode", "children", "scope", "facet", "entries"
        ),
        ContextView: _attributes("path"),
        Ref: _attributes("path"),
        Scope: _attributes("label", "parents", "mro"),
        Identity: _attributes("label", "blocked"),
        ScopeBinding: _attributes("identity", "blocked"),
        RefEntry: _attributes("ref", "alive", "config", "metadata"),
        RefDeclaration: _attributes("config", "metadata"),
        RefLeafConfig: _attributes("mode"),
        RefContainerConfig: _attributes(),
        Compose: TreeHandler(lambda value: (value.layers(), TreeAux()), None),
    }
)

CONTEXT_INSPECTION_TYPE_NAMES: Mapping[type, str] = {
    Context: "slyme.context",
    ContextView: "slyme.context_view",
    Ref: "slyme.ref",
    Scope: "slyme.scope",
    Identity: "slyme.identity",
    ScopeBinding: "slyme.scope_binding",
    RefEntry: "slyme.ref_entry",
    RefDeclaration: "slyme.ref_declaration",
    RefLeafConfig: "slyme.ref_leaf_config",
    RefContainerConfig: "slyme.ref_container_config",
    Compose: "slyme.compose",
}


def _interpret_context(value: Context, env: object) -> Interpretation:
    """Expose visible bindings using the Context itself as their environment."""
    return Interpretation(value.flatten(), env=value)


def _interpret_view(value: ContextView, env: object) -> Interpretation:
    """Expose the view's bindings using its owning Context as their environment."""
    return Interpretation(value.flatten(), env=value.context)


def _interpret_ref(value: Ref, env: object) -> Interpretation | None:
    """Read the Ref when env is a Context; otherwise report unavailable."""
    if isinstance(env, Context):
        return Interpretation(env.get(value))
    return None


def _interpret_compose(value: Compose[Any, Any], env: object) -> Interpretation | None:
    """Run the configured query in env's Scope when both query and Context exist."""
    if isinstance(env, Context) and value._query is not None:
        return Interpretation(value.resolve(env.scope))
    return None


CONTEXT_INSPECTION_INTERPRETERS: Mapping[type, Interpreter] = MappingProxyType(
    {
        Context: _interpret_context,
        ContextView: _interpret_view,
        Ref: _interpret_ref,
        Compose: _interpret_compose,
    }
)
