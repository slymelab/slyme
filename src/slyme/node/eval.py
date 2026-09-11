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
from inspect import isawaitable
from typing import Any, NoReturn

from slyme.context import Context, Ref
from slyme.context.tree import CTX_EVAL_ENGINE
from slyme.utils.awaitable import resolve
from slyme.utils.pytree import PyTreeDef
from slyme.utils.registry import TypeRegistry

from ._async import finish_uninterruptibly, wait_uninterruptibly
from .core import Node

__all__ = [
    "eval_tree",
    "prepare_eval_plan",
    "execute_eval_plan",
    "contains_eval_type",
    "EvaluationPlan",
    "EVALUATOR_REGISTRY",
    "BatchEvaluatorFunc",
    "EvaluatorDef",
]


BatchEvaluatorFunc = Callable[
    [Context, Sequence[Any]], Sequence[Any] | Awaitable[Sequence[Any]]
]


class _AdditionalAutoFailures(Exception):
    """Additional child failures observed while Auto evaluation was unwinding."""

    def __init__(self, errors: tuple[BaseException, ...]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} additional Auto child failures.")


@dataclass(frozen=True)
class EvaluatorDef:
    func: BatchEvaluatorFunc


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
    """Evaluate a prepared tree, returning an awaitable only when necessary."""
    results = [None] * plan.num_leaves
    for i, val in plan.pass_through:
        results[i] = val
    batches = iter(plan.batches)

    def store(
        evaluator: EvaluatorDef, indices: tuple[int, ...], values: Sequence[Any]
    ) -> None:
        if len(values) != len(indices):
            raise ValueError(
                f"Evaluator {evaluator} returned {len(values)} results, "
                f"expected {len(indices)}."
            )
        for index, value in zip(indices, values, strict=True):
            results[index] = value

    async def continue_async(
        evaluator: EvaluatorDef,
        indices: tuple[int, ...],
        pending: Awaitable[Sequence[Any]],
    ) -> Any:
        store(evaluator, indices, await pending)
        for evaluator, indices, values in batches:
            store(evaluator, indices, await resolve(evaluator.func(ctx, values)))
        return CTX_EVAL_ENGINE.unflatten(plan.tree_def, results)

    for evaluator, indices, values in batches:
        batch_results = evaluator.func(ctx, values)
        if isawaitable(batch_results):
            return continue_async(evaluator, indices, batch_results)
        store(evaluator, indices, batch_results)
    return CTX_EVAL_ENGINE.unflatten(plan.tree_def, results)


def eval_tree(ctx: Context, tree: Any) -> Any:
    """Evaluate Ref and Node leaves using the same completion protocol as Node."""
    return execute_eval_plan(ctx, prepare_eval_plan(tree))


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


EVALUATOR_REGISTRY.register(EvaluatorDef(ref_evaluator), key=Ref)


def node_evaluator(
    ctx: Context, nodes: Sequence[Node]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    """Evaluate siblings concurrently after the first asynchronous completion."""
    results: list[Any] = []
    cleanup_failures: dict[int, BaseException] = {}

    def finish_child(
        index: int, child: Context, value: Any, error: BaseException | None = None
    ) -> Any:
        def finish() -> Any:
            if error is not None:
                raise error
            return value

        def failed(cleanup_error: BaseException) -> NoReturn:
            cleanup_failures[index] = cleanup_error
            if error is not None:
                raise error from cleanup_error
            raise cleanup_error

        try:
            cleanup = child.dispose()
        except BaseException as cleanup_error:
            return failed(cleanup_error)
        if not isawaitable(cleanup):
            return finish()

        async def finish_async() -> Any:
            task: asyncio.Task[None] = asyncio.create_task(resolve(cleanup))
            try:
                await wait_uninterruptibly(task)
            except BaseException as cleanup_error:
                recorded = (
                    task.exception()
                    if task.done() and not task.cancelled()
                    else cleanup_error
                )
                cleanup_failures[index] = recorded or cleanup_error
                if error is not None:
                    raise error from cleanup_error
                raise
            return finish()

        return finish_async()

    def evaluate_one(index: int, node: Node) -> Any:
        child = ctx._fork_for_auto()
        try:
            value = node(child)
        except BaseException as error:
            return finish_child(index, child, None, error)
        if not isawaitable(value):
            return finish_child(index, child, value)

        async def evaluate_async() -> Any:
            try:
                result = await value
            except BaseException as error:
                return await resolve(finish_child(index, child, None, error))
            return await resolve(finish_child(index, child, result))

        return evaluate_async()

    async def remaining(first_index: int, first: Awaitable[Any]) -> Sequence[Any]:
        async def evaluate(index: int) -> Any:
            return await resolve(evaluate_one(index, nodes[index]))

        tasks = (
            asyncio.create_task(resolve(first)),
            *(
                asyncio.create_task(evaluate(index))
                for index in range(first_index + 1, len(nodes))
            ),
        )
        try:
            return [*results, *await asyncio.gather(*tasks)]
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
                    failure = cleanup_failures.get(first_index + index)
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

    for index, node in enumerate(nodes):
        value = evaluate_one(index, node)
        if isawaitable(value):
            return remaining(index, value)
        results.append(value)
    return results


EVALUATOR_REGISTRY.register(EvaluatorDef(node_evaluator), key=Node)
