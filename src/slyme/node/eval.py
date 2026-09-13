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
from slyme.utils.continuation import BatchError, Continuation, await_result
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


@dataclass(frozen=True)
class EvaluatorDef:
    func: BatchEvaluatorFunc


EVALUATOR_REGISTRY = GeneralRegistry[type, EvaluatorDef]("evaluator")


def eval_tree(ctx: Context, tree: Any) -> Any:
    """Evaluate registered leaves and reconstruct every Tree container.

    Ordinary leaves and evaluator results retain their identities. Results are
    not recursively evaluated. Return an awaitable only for asynchronous work.
    Evaluators match the exact leaf type; subclasses require registration.
    Evaluator groups are independent batches and may execute concurrently.
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
            Continuation(evaluator.func(ctx, values))
            .then(lambda result: store(evaluator, indices, result))
            .unwrap()
        )

    return (
        Continuation.batch(batches, evaluate)
        .then(lambda _: CTX_EVAL_ENGINE.unflatten(tree_def, leaves))
        .unwrap()
    )


# --- Evaluator Implementations ---
# Ref
def ref_evaluator(
    ctx: Context, refs: Sequence[Ref]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    return Continuation.batch(refs, ctx.get).unwrap()


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
    children: list[Context] = []

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

    def cleanup(error: BaseException) -> Any:
        def check_failure(failure: BaseException) -> None:
            if not isinstance(error, BatchError) or all(
                failure is not previous.error for previous in error.results
            ):
                raise failure

        def release(child: Context) -> None | Awaitable[None]:
            return (
                Continuation.call(child.dispose)
                .catch(check_failure, exceptions=BaseException)
                .unwrap()
            )

        def report(outcome: list[None] | BaseException) -> NoReturn:
            if isinstance(outcome, BaseException):
                if isinstance(error, asyncio.CancelledError):
                    raise outcome from error
                raise error from outcome
            raise error

        pending = (
            Continuation.batch(children, release)
            .catch(lambda failure: failure, exceptions=BaseException)
            .unwrap()
        )
        if not isawaitable(pending):
            return report(pending)

        async def finish() -> NoReturn:
            task = asyncio.create_task(await_result(pending))
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # The original failure or cancellation is reported after cleanup.
                    continue
            report(task.result())

        return finish()

    return (
        Continuation.batch(nodes, evaluate_one)
        .catch(cleanup, exceptions=BaseException)
        .unwrap()
    )


EVALUATOR_REGISTRY.register(EvaluatorDef(node_evaluator), key=Node)
