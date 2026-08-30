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

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Sequence

from slyme.context import Context, Ref, RefFactory
from slyme.context.tree import CTX_EVAL_ENGINE
from slyme.utils.pytree import PyTreeDef
from slyme.utils.registry import TypeRegistry

from .core import AsyncNode, Node

__all__ = [
    "eval_tree",
    "async_eval_tree",
    "prepare_eval_plan",
    "execute_eval_plan",
    "async_execute_eval_plan",
    "contains_eval_type",
    "EvaluationPlan",
    "EVALUATOR_REGISTRY",
    "BatchEvaluatorFunc",
    "AsyncBatchEvaluatorFunc",
    "EvaluatorDef",
]


BatchEvaluatorFunc = Callable[[Context, Sequence[Any]], Sequence[Any]]
AsyncBatchEvaluatorFunc = Callable[[Context, Sequence[Any]], Awaitable[Sequence[Any]]]


@dataclass(frozen=True)
class EvaluatorDef:
    sync_func: BatchEvaluatorFunc
    async_func: AsyncBatchEvaluatorFunc


EVALUATOR_REGISTRY = TypeRegistry[Any, EvaluatorDef]("evaluator")


@dataclass(frozen=True)
class EvaluationPlan:
    tree_def: PyTreeDef
    # batches: list of (evaluator, indices, values)
    batches: tuple[tuple[EvaluatorDef, tuple[int, ...], tuple[Any, ...]], ...]
    pass_through: tuple[tuple[int, Any], ...]
    num_leaves: int


def prepare_eval_plan(tree: Any) -> EvaluationPlan:
    leaves, tree_def = CTX_EVAL_ENGINE.flatten(tree)
    num_leaves = len(leaves)

    # Group by evaluator
    # evaluator -> (indices, values)
    eval_groups: dict[EvaluatorDef, tuple[list[int], list[Any]]] = {}
    pass_through_list = []

    for i, leaf in enumerate(leaves):
        # Lookup batch evaluator
        evaluator = EVALUATOR_REGISTRY.lookup(type(leaf), default=None)
        if evaluator is not None:
            if evaluator not in eval_groups:
                eval_groups[evaluator] = ([], [])
            indices, values = eval_groups[evaluator]
            indices.append(i)
            values.append(leaf)
        else:
            pass_through_list.append((i, leaf))

    # Convert dict to tuple of tuples for immutability
    batches_list = []
    for evaluator, (indices, values) in eval_groups.items():
        batches_list.append((evaluator, tuple(indices), tuple(values)))

    return EvaluationPlan(
        tree_def=tree_def,
        batches=tuple(batches_list),
        pass_through=tuple(pass_through_list),
        num_leaves=num_leaves,
    )


def execute_eval_plan(ctx: Context, plan: EvaluationPlan) -> Any:
    results = [None] * plan.num_leaves

    # Handle pass_through
    for i, val in plan.pass_through:
        results[i] = val

    # Handle batches
    for evaluator, indices, values in plan.batches:
        batch_results = evaluator.sync_func(ctx, values)
        # Validation
        if len(batch_results) != len(indices):
            raise ValueError(
                f"Evaluator {evaluator} returned {len(batch_results)} results, "
                f"expected {len(indices)}."
            )
        for i, res in zip(indices, batch_results):
            results[i] = res

    return CTX_EVAL_ENGINE.unflatten(plan.tree_def, results)


async def async_execute_eval_plan(ctx: Context, plan: EvaluationPlan) -> Any:
    results = [None] * plan.num_leaves

    # Handle pass_through
    for i, val in plan.pass_through:
        results[i] = val

    # Handle batches
    for evaluator, indices, values in plan.batches:
        batch_results = await evaluator.async_func(ctx, values)
        # Validation
        if len(batch_results) != len(indices):
            raise ValueError(
                f"Evaluator {evaluator} returned {len(batch_results)} results, "
                f"expected {len(indices)}."
            )
        for i, res in zip(indices, batch_results):
            results[i] = res

    return CTX_EVAL_ENGINE.unflatten(plan.tree_def, results)


def eval_tree(ctx: Context, tree: Any) -> Any:
    plan = prepare_eval_plan(tree)
    return execute_eval_plan(ctx, plan)


async def async_eval_tree(ctx: Context, tree: Any) -> Any:
    plan = prepare_eval_plan(tree)
    return await async_execute_eval_plan(ctx, plan)


def contains_eval_type(tree: Any) -> bool:
    """
    Check if the tree contains any nodes that require evaluation based on the registry.
    """
    # Optimized iteration without full flattening
    for leaf in CTX_EVAL_ENGINE.iter(tree):
        if EVALUATOR_REGISTRY.lookup(type(leaf), default=None) is not None:
            return True
    return False


# --- Evaluator Implementations ---
# Ref
def ref_evaluator(ctx: Context, refs: Sequence[Ref]) -> Sequence[Any]:
    return ctx.extract(refs)


async def async_ref_evaluator(ctx: Context, refs: Sequence[Ref]) -> Sequence[Any]:
    return ctx.extract(refs)


EVALUATOR_REGISTRY.register(
    EvaluatorDef(sync_func=ref_evaluator, async_func=async_ref_evaluator), key=Ref
)

EVALUATOR_REGISTRY.register(
    EvaluatorDef(sync_func=ref_evaluator, async_func=async_ref_evaluator),
    key=RefFactory,
)


# Node
def node_evaluator(ctx: Context, nodes: Sequence[Any]) -> Sequence[Any]:
    results = []
    for node in nodes:
        if isinstance(node, AsyncNode):
            raise RuntimeError(
                f"Cannot evaluate AsyncNode in synchronous context: {node}"
            )
        results.append(node(ctx))
    return results


async def async_node_evaluator(ctx: Context, nodes: Sequence[Any]) -> Sequence[Any]:

    async def _evaluate_single(node: Any) -> Any:
        if isinstance(node, AsyncNode):
            return await node(ctx)
        else:
            return await asyncio.to_thread(node, ctx)

    return await asyncio.gather(*(_evaluate_single(node) for node in nodes))


SHARED_NODE_EVALUATOR = EvaluatorDef(
    sync_func=node_evaluator, async_func=async_node_evaluator
)

EVALUATOR_REGISTRY.register(SHARED_NODE_EVALUATOR, key=Node)
EVALUATOR_REGISTRY.register(SHARED_NODE_EVALUATOR, key=AsyncNode)
