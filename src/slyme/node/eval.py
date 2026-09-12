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
from slyme.utils.registry import TypeRegistry

from ._async import finish_uninterruptibly, wait_uninterruptibly
from .core import Node

__all__ = [
    "eval_tree",
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


def eval_tree(ctx: Context, tree: Any) -> Any:
    """Evaluate registered leaves and reconstruct every PyTree container.

    Ordinary leaves and evaluator results retain their identities. Results are
    not recursively evaluated. Return an awaitable only for asynchronous work.
    """
    leaves, tree_def = CTX_EVAL_ENGINE.flatten(tree)
    eval_groups: dict[EvaluatorDef, tuple[list[int], list[Any]]] = {}
    for i, leaf in enumerate(leaves):
        evaluator = EVALUATOR_REGISTRY.lookup(type(leaf), default=None)
        if evaluator is not None:
            if evaluator not in eval_groups:
                eval_groups[evaluator] = ([], [])
            indices, values = eval_groups[evaluator]
            indices.append(i)
            values.append(leaf)
    batches = iter(eval_groups.items())

    def store(
        evaluator: EvaluatorDef, indices: list[int], values: Sequence[Any]
    ) -> None:
        if len(values) != len(indices):
            raise ValueError(
                f"Evaluator {evaluator} returned {len(values)} results, "
                f"expected {len(indices)}."
            )
        for index, value in zip(indices, values, strict=True):
            leaves[index] = value

    for evaluator, (indices, values) in batches:
        batch_results = evaluator.func(ctx, values)
        if isawaitable(batch_results):

            async def continue_async(
                evaluator: EvaluatorDef,
                indices: list[int],
                pending: Awaitable[Sequence[Any]],
            ) -> Any:
                store(evaluator, indices, await pending)
                for evaluator, (indices, values) in batches:
                    store(
                        evaluator, indices, await resolve(evaluator.func(ctx, values))
                    )
                return CTX_EVAL_ENGINE.unflatten(tree_def, leaves)

            return continue_async(evaluator, indices, batch_results)
        store(evaluator, indices, batch_results)
    return CTX_EVAL_ENGINE.unflatten(tree_def, leaves)


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
