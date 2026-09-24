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
from types import MappingProxyType
from typing import TYPE_CHECKING

from slyme.utils.tree import TreeAux, TreeHandler, TreeRules
from slyme.utils.tree.common import (
    flatten_dict,
    flatten_mapping_proxy,
    unflatten_dict,
    unflatten_mapping_proxy,
)

from .compose import Compose
from .schema import Ref, Schema

if TYPE_CHECKING:
    from slyme.node.eval import BatchEvaluatorFunc

    from .core import Context

__all__ = ["DATA_TREE_REF", "NODE_TREE_REF", "EVALUATORS_REF"]


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
class EvaluatorLayer:
    """Evaluator registrations with one handler per exact class in this layer."""

    handlers: dict[type, BatchEvaluatorFunc] = field(default_factory=dict, init=False)

    def register(
        self, token: object, /, handlers: Mapping[type, BatchEvaluatorFunc]
    ) -> Callable[[], None]:
        classes = tuple(handlers)
        for cls in classes:
            if cls in self.handlers:
                raise ValueError(f"Type {cls!r} is already registered in this layer.")
        self.handlers.update(handlers)

        def dispose() -> None:
            for cls in classes:
                del self.handlers[cls]

        return dispose

    @staticmethod
    def merge(layers: Iterable[EvaluatorLayer]) -> dict[type, BatchEvaluatorFunc]:
        """Snapshot visible evaluators, keeping the first handler for each class."""
        result: dict[type, BatchEvaluatorFunc] = {}
        for layer in layers:
            for cls, handler in layer.handlers.items():
                result.setdefault(cls, handler)
        return result


DATA_TREE_REF = Ref[Compose[TreeLayer, TreeRules]]("$.tree.data.rules")
NODE_TREE_REF = Ref[Compose[TreeLayer, TreeRules]]("$.tree.node.rules")
EVALUATORS_REF = Ref[Compose[EvaluatorLayer, dict[type, "BatchEvaluatorFunc"]]](
    "$.eval.handlers"
)

DATA_RULES = TreeRules(
    handlers={
        tuple: TreeHandler(
            lambda value: (iter(value), TreeAux()), lambda items, _: tuple(items)
        ),
        list: TreeHandler(
            lambda value: (iter(value), TreeAux()), lambda items, _: list(items)
        ),
        dict: TreeHandler(flatten_dict, unflatten_dict),
        MappingProxyType: TreeHandler(flatten_mapping_proxy, unflatten_mapping_proxy),
    }
)


def _apply(ctx: Context) -> None:
    # Root-only; derive creates children of existing Contexts, so default
    # registrations never run ahead of a derived child's explicit Scope bindings.
    # Lazy imports avoid the Context/Node import cycle.
    from slyme.node.core import NODE_RULES, Node
    from slyme.node.eval import node_evaluator, ref_evaluator

    ctx.declare(
        {
            "$": {
                "tree": {
                    "data": {"rules": Schema.leaf(mode="register")},
                    "node": {"rules": Schema.leaf(mode="register")},
                },
                "eval": {"handlers": Schema.leaf(mode="register")},
            }
        }
    )
    data = Compose(factory=TreeLayer, query=TreeLayer.merge)
    nodes = Compose(factory=TreeLayer, query=TreeLayer.merge)
    evaluators = Compose(factory=EvaluatorLayer, query=EvaluatorLayer.merge)
    ctx.register(DATA_TREE_REF, data)
    ctx.register(NODE_TREE_REF, nodes)
    ctx.register(EVALUATORS_REF, evaluators)
    ctx.effect(lambda: data.register(ctx.scope, DATA_RULES))
    ctx.effect(
        lambda: nodes.register(ctx.scope, TreeRules.merge((NODE_RULES, DATA_RULES)))
    )
    ctx.effect(
        lambda: evaluators.register(
            ctx.scope, {Ref: ref_evaluator, Node: node_evaluator}
        )
    )
