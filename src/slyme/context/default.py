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

from collections.abc import Mapping
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

DATA_TREE_REF = Ref[Compose[TreeRules, TreeRules]]("$.tree.data")
NODE_TREE_REF = Ref[Compose[TreeRules, TreeRules]]("$.tree.node")
EVALUATORS_REF = Ref[
    Compose[Mapping[type, "BatchEvaluatorFunc"], dict[type, "BatchEvaluatorFunc"]]
]("$.eval.handlers")

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


def _install(ctx: "Context") -> None:
    # Context is already constructed before importing Node's default behavior.
    from slyme.node.core import NODE_RULES, Node
    from slyme.node.eval import node_evaluator, ref_evaluator

    ctx.declare(
        {
            "$": {
                "tree": {
                    "data": Schema.leaf(mode="register"),
                    "node": Schema.leaf(mode="register"),
                },
                "eval": {"handlers": Schema.leaf(mode="register")},
            }
        }
    )
    data = Compose(TreeRules.merge)
    nodes = Compose(TreeRules.merge)
    evaluators: Compose[
        Mapping[type, BatchEvaluatorFunc], dict[type, BatchEvaluatorFunc]
    ] = Compose.merge()
    ctx.register(DATA_TREE_REF, data)
    ctx.register(NODE_TREE_REF, nodes)
    ctx.register(EVALUATORS_REF, evaluators)
    ctx.effect(lambda: data.add(ctx.scope, DATA_RULES))
    ctx.effect(lambda: nodes.add(ctx.scope, TreeRules.merge((NODE_RULES, DATA_RULES))))
    ctx.effect(
        lambda: evaluators.add(ctx.scope, {Ref: ref_evaluator, Node: node_evaluator})
    )
