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
from typing import Any

from slyme.context import Context, Ref
from slyme.context.tree import CTX_EVAL_ENGINE
from slyme.utils.continuation import Continuation, await_result
from slyme.utils.registry import GeneralRegistry

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
    """Other child and cleanup failures from a completed Auto batch."""

    def __init__(self, errors: tuple[BaseException, ...]) -> None:
        self.errors = errors
        super().__init__(f"{len(errors)} additional Auto child failures.")


@dataclass(frozen=True)
class EvaluatorDef:
    func: BatchEvaluatorFunc


EVALUATOR_REGISTRY = GeneralRegistry[type, EvaluatorDef]("evaluator")


def eval_tree(ctx: Context, tree: Any) -> Any:
    """Evaluate registered leaves and reconstruct every Tree container.

    Ordinary leaves and evaluator results retain their identities. Results are
    not recursively evaluated. Return an awaitable only for asynchronous work.
    Evaluators match the exact leaf type; subclasses require registration.
    """
    leaves, tree_def = CTX_EVAL_ENGINE.flatten(tree)
    eval_groups: dict[EvaluatorDef, tuple[list[int], list[Any]]] = {}
    for i, leaf in enumerate(leaves):
        evaluator = EVALUATOR_REGISTRY.get(type(leaf), None)
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

    def evaluate(batch: tuple[EvaluatorDef, tuple[list[int], list[Any]]]) -> Any:
        evaluator, (indices, values) = batch
        return (
            Continuation.resolve(evaluator.func(ctx, values))
            .then(lambda result: store(evaluator, indices, result))
            .unwrap()
        )

    return (
        Continuation.resolve(Continuation.each(batches, evaluate))
        .then(lambda _: CTX_EVAL_ENGINE.unflatten(tree_def, leaves))
        .unwrap()
    )


# --- Evaluator Implementations ---
# Ref
def ref_evaluator(ctx: Context, refs: Sequence[Ref]) -> Sequence[Any]:
    return [ctx.get(ref) for ref in refs]


EVALUATOR_REGISTRY.register(EvaluatorDef(ref_evaluator), key=Ref)


def node_evaluator(
    ctx: Context, nodes: Sequence[Node]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    """Evaluate every sibling and report failures after all children settle.

    Execution stays synchronous until a child call or cleanup is awaitable.
    Successful children dispose immediately; failure cleanup follows the batch.
    Child failure never cancels siblings. Caller cancellation follows asyncio
    propagation, with child cleanup completed before leaving the evaluator.
    """
    results: list[Any] = []
    children: list[Context] = []
    errors: list[BaseException] = []

    def record(error: BaseException) -> None:
        if all(error is not previous for previous in errors):
            errors.append(error)

    def report() -> Sequence[Any]:
        if errors:
            primary = next(
                (
                    error
                    for error in errors
                    if not isinstance(error, asyncio.CancelledError)
                ),
                errors[0],
            )
            additional = tuple(error for error in errors if error is not primary)
            if additional:
                cause = (
                    additional[0]
                    if len(additional) == 1
                    else _AdditionalAutoFailures(additional)
                )
                raise primary from cause
            raise primary
        return results

    def release(child: Context) -> None | Awaitable[None]:
        return (
            Continuation.call(child.dispose)
            .catch(record, exceptions=BaseException)
            .unwrap()
        )

    async def finish_async(cleanup: Awaitable[None]) -> Sequence[Any]:
        task = asyncio.create_task(await_result(cleanup))
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as error:
                record(error)
        task.result()
        return report()

    def finish() -> Sequence[Any] | Awaitable[Sequence[Any]]:
        if errors:
            cleanup = Continuation.each(children, release)
            if isawaitable(cleanup):
                return finish_async(cleanup)
        return report()

    def evaluate_one(node: Node) -> Any:
        child = ctx.fork(scope=ctx.scope.fork())
        children.append(child)
        return (
            Continuation.call(lambda: node(child))
            .then(
                lambda value: (
                    Continuation.call(child.dispose).then(lambda _: value).unwrap()
                )
            )
            .unwrap()
        )

    async def remaining(first_index: int, first: Awaitable[Any]) -> Sequence[Any]:
        async def evaluate(index: int) -> Any:
            return await await_result(evaluate_one(nodes[index]))

        tasks = (
            asyncio.create_task(await_result(first)),
            *(
                asyncio.create_task(evaluate(index))
                for index in range(first_index + 1, len(nodes))
            ),
        )
        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError as error:
            record(error)
        for task in tasks:
            try:
                results.append(task.result())
            except BaseException as error:
                record(error)
        return await await_result(finish())

    for index, node in enumerate(nodes):
        try:
            value = evaluate_one(node)
        except BaseException as error:
            record(error)
            continue
        if isawaitable(value):
            return remaining(index, value)
        results.append(value)
    return finish()


EVALUATOR_REGISTRY.register(EvaluatorDef(node_evaluator), key=Node)
