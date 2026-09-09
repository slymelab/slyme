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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

from slyme.context import Context, Ref
from slyme.context.tree import CTX_EVAL_ENGINE
from slyme.utils.pytree import PyTreeDef
from slyme.utils.registry import TypeRegistry

from ._async import finish_uninterruptibly, wait_uninterruptibly
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


class _AdditionalAutoFailures(Exception):
    """Additional child failures observed while Auto evaluation was unwinding."""

    def __init__(self, errors: tuple[BaseException, ...]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} additional Auto child failures.")


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
        for i, res in zip(indices, batch_results, strict=True):
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
        for i, res in zip(indices, batch_results, strict=True):
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


# Node
def node_evaluator(ctx: Context, nodes: Sequence[Any]) -> Sequence[Any]:
    results = []
    for node in nodes:
        if isinstance(node, AsyncNode):
            raise RuntimeError(
                f"Cannot evaluate AsyncNode in synchronous context: {node}"
            )
        child_ctx = ctx._fork_for_auto(synchronous=True)
        try:
            result = node(child_ctx)
        except BaseException as error:
            try:
                child_ctx.dispose()
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise
        else:
            child_ctx.dispose()
            results.append(result)
    return results


async def async_node_evaluator(ctx: Context, nodes: Sequence[Any]) -> Sequence[Any]:
    cleanup_failures: dict[int, BaseException] = {}

    async def _dispose_child(index: int, child_ctx: Context) -> None:
        cleanup = asyncio.create_task(child_ctx.async_dispose())
        try:
            await wait_uninterruptibly(cleanup)
        except asyncio.CancelledError as error:
            if cleanup.cancelled():
                cleanup_failures[index] = error
            elif cleanup.done():
                cleanup_error = cleanup.exception()
                if cleanup_error is not None:
                    cleanup_failures[index] = cleanup_error
            raise
        except BaseException as error:
            cleanup_failures[index] = error
            raise

    async def _evaluate_single(index: int, node: Any) -> Any:
        child_ctx = ctx._fork_for_auto(synchronous=False)
        try:
            if isinstance(node, AsyncNode):
                result = await node(child_ctx)
            else:
                result = node(child_ctx)
        except BaseException as error:
            try:
                await _dispose_child(index, child_ctx)
            except BaseException as cleanup_error:
                raise error from cleanup_error
            raise
        else:
            await _dispose_child(index, child_ctx)
            return result

    tasks = tuple(
        asyncio.create_task(_evaluate_single(index, node))
        for index, node in enumerate(nodes)
    )
    try:
        return await asyncio.gather(*tasks)
    except BaseException as error:
        for task in tasks:
            if not task.done():
                task.cancel()
        pending = asyncio.gather(*tasks, return_exceptions=True)
        await finish_uninterruptibly(pending)
        outcomes = pending.result()
        primary_index = next(
            (
                index
                for index, task in enumerate(tasks)
                if not task.cancelled() and task.exception() is error
            ),
            None,
        )
        additional: list[BaseException] = []
        for index, outcome in enumerate(outcomes):
            if index == primary_index or not isinstance(outcome, BaseException):
                continue
            if isinstance(outcome, asyncio.CancelledError):
                failure = cleanup_failures.get(index)
                if failure is not None:
                    additional.append(failure)
            else:
                additional.append(outcome)

        if isinstance(error, asyncio.CancelledError) and additional:
            failures = tuple(
                failure
                for failure in additional
                if not isinstance(failure, asyncio.CancelledError)
            )
            if len(failures) == 1:
                raise failures[0] from error
            if failures:
                raise _AdditionalAutoFailures(failures) from error
        elif additional:
            causes = [] if error.__cause__ is None else [error.__cause__]
            for failure in additional:
                if all(failure is not current for current in causes):
                    causes.append(failure)
            cause: BaseException = (
                causes[0]
                if len(causes) == 1
                else _AdditionalAutoFailures(tuple(causes))
            )
            raise error from cause
        raise


SHARED_NODE_EVALUATOR = EvaluatorDef(
    sync_func=node_evaluator, async_func=async_node_evaluator
)

EVALUATOR_REGISTRY.register(SHARED_NODE_EVALUATOR, key=Node)
EVALUATOR_REGISTRY.register(SHARED_NODE_EVALUATOR, key=AsyncNode)
