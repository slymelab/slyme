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

"""Framework paths and root-owned default contributions.

Only immutable rule definitions are shared between applications. Mutable
compositions are installed separately for each root Context.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Generic, TypeVar

from slyme.tree import TreeRules
from slyme.tree.common import DATA_RULES

from .compose import Compose
from .schema import Ref, Schema

if TYPE_CHECKING:
    from slyme.inspection.core import Interpreter
    from slyme.node.eval import BatchEvaluatorFunc

    from .core import Context

__all__ = [
    "DATA_TREE_REF",
    "EVALUATORS_REF",
    "INSPECTION_TREE_RULES_REF",
    "INSPECTION_INTERPRETERS_REF",
    "INSPECTION_TYPE_NAMES_REF",
    "INSPECTION_RAW_VALUE_TYPES_REF",
]

_T = TypeVar("_T")


@dataclass
class TreeLayer:
    """Tree rule registrations with one handler per exact class in this layer."""

    rules: dict[object, TreeRules] = field(default_factory=dict, init=False)
    _classes: set[type] = field(default_factory=set, init=False)

    def register(self, token: object, /, rules: TreeRules) -> Callable[[], None]:
        for cls in rules.handlers:
            if cls in self._classes:
                raise ValueError(f"Type {cls!r} is already registered in this layer.")
        self.rules[token] = rules
        self._classes.update(rules.handlers)

        def dispose() -> None:
            self._classes.difference_update(self.rules.pop(token).handlers)

        return dispose

    @staticmethod
    def merge(layers: Iterable[TreeLayer]) -> TreeRules:
        """Merge registered Tree rules in visible layer and registration order."""
        return TreeRules.merge(
            tuple(rule for layer in layers for rule in layer.rules.values())
        )


@dataclass
class TypeLayer(Generic[_T]):
    """Registrations with one value per exact class in this layer."""

    entries: dict[type, _T] = field(default_factory=dict, init=False)

    def register(
        self, token: object, /, entries: Mapping[type, _T]
    ) -> Callable[[], None]:
        classes = tuple(entries)
        for cls in classes:
            if cls in self.entries:
                raise ValueError(f"Type {cls!r} is already registered in this layer.")
        self.entries.update(entries)

        def dispose() -> None:
            for cls in classes:
                del self.entries[cls]

        return dispose

    @staticmethod
    def merge(layers: Iterable[TypeLayer[_T]]) -> dict[type, _T]:
        """Snapshot visible entries, keeping the first value for each class."""
        result: dict[type, _T] = {}
        for layer in layers:
            for cls, value in layer.entries.items():
                result.setdefault(cls, value)
        return result


DATA_TREE_REF = Ref[Compose[TreeLayer, TreeRules]]("$.tree.data.rules")
EVALUATORS_REF = Ref[
    Compose[TypeLayer["BatchEvaluatorFunc"], dict[type, "BatchEvaluatorFunc"]]
]("$.eval.handlers")
INSPECTION_TREE_RULES_REF = Ref[Compose[TreeLayer, TreeRules]](
    "$.inspection.tree_rules"
)
INSPECTION_INTERPRETERS_REF = Ref[
    Compose[TypeLayer["Interpreter"], dict[type, "Interpreter"]]
]("$.inspection.interpreters")
INSPECTION_TYPE_NAMES_REF = Ref[Compose[TypeLayer[str], dict[type, str]]](
    "$.inspection.type_names"
)
INSPECTION_RAW_VALUE_TYPES_REF = Ref[Compose[TypeLayer[None], dict[type, None]]](
    "$.inspection.raw_value_types"
)


def _apply(ctx: Context) -> None:
    # Root-only; derive creates children of existing Contexts, so default
    # registrations never run ahead of a derived child's explicit Scope bindings.
    # Lazy imports avoid the Context/Node import cycle.
    from slyme.inspection.common import (
        KEY_INSPECTION_TREE_RULES,
        KEY_INSPECTION_TYPE_NAMES,
        SCALAR_TYPES,
    )
    from slyme.node.core import Node
    from slyme.node.eval import node_evaluator, ref_evaluator
    from slyme.node.inspection import (
        NODE_INSPECTION_TREE_RULES,
        NODE_INSPECTION_TYPE_NAMES,
    )

    from .inspection import (
        CONTEXT_INSPECTION_INTERPRETERS,
        CONTEXT_INSPECTION_TREE_RULES,
        CONTEXT_INSPECTION_TYPE_NAMES,
    )

    ctx.declare(
        {
            "$": {
                "tree": {
                    "data": {"rules": Schema.leaf(mode="register")},
                },
                "eval": {"handlers": Schema.leaf(mode="register")},
                "inspection": {
                    "tree_rules": Schema.leaf(mode="register"),
                    "interpreters": Schema.leaf(mode="register"),
                    "type_names": Schema.leaf(mode="register"),
                    "raw_value_types": Schema.leaf(mode="register"),
                },
            }
        }
    )
    data = Compose(factory=TreeLayer, query=TreeLayer.merge)
    evaluators: Compose[
        TypeLayer[BatchEvaluatorFunc], dict[type, BatchEvaluatorFunc]
    ] = Compose(factory=TypeLayer, query=TypeLayer.merge)
    inspection_rules = Compose(factory=TreeLayer, query=TreeLayer.merge)
    interpreters: Compose[TypeLayer[Interpreter], dict[type, Interpreter]] = Compose(
        factory=TypeLayer, query=TypeLayer.merge
    )
    type_names: Compose[TypeLayer[str], dict[type, str]] = Compose(
        factory=TypeLayer, query=TypeLayer.merge
    )
    raw_value_types: Compose[TypeLayer[None], dict[type, None]] = Compose(
        factory=TypeLayer, query=TypeLayer.merge
    )
    ctx.register(DATA_TREE_REF, data)
    ctx.register(EVALUATORS_REF, evaluators)
    ctx.register(INSPECTION_TREE_RULES_REF, inspection_rules)
    ctx.register(INSPECTION_INTERPRETERS_REF, interpreters)
    ctx.register(INSPECTION_TYPE_NAMES_REF, type_names)
    ctx.register(INSPECTION_RAW_VALUE_TYPES_REF, raw_value_types)
    ctx.effect(lambda: data.register(ctx.scope, DATA_RULES))
    ctx.effect(
        lambda: evaluators.register(
            ctx.scope, {Ref: ref_evaluator, Node: node_evaluator}
        )
    )
    ctx.effect(
        lambda: inspection_rules.register(
            ctx.scope,
            TreeRules.merge(
                (
                    NODE_INSPECTION_TREE_RULES,
                    CONTEXT_INSPECTION_TREE_RULES,
                    KEY_INSPECTION_TREE_RULES,
                    DATA_RULES,
                )
            ),
        )
    )
    ctx.effect(
        lambda: interpreters.register(ctx.scope, CONTEXT_INSPECTION_INTERPRETERS)
    )
    ctx.effect(lambda: raw_value_types.register(ctx.scope, dict.fromkeys(SCALAR_TYPES)))
    ctx.effect(
        lambda: type_names.register(
            ctx.scope,
            {
                **CONTEXT_INSPECTION_TYPE_NAMES,
                **NODE_INSPECTION_TYPE_NAMES,
                **KEY_INSPECTION_TYPE_NAMES,
            },
        )
    )
