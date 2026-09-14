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
from collections.abc import Awaitable, Callable, Generator, Sequence
from dataclasses import dataclass
from inspect import isawaitable
from typing import Any, NoReturn, TypeVar, cast

from slyme.context import Context, Ref
from slyme.context.tree import CTX_EVAL_ENGINE
from slyme.utils.continuation import await_result, run
from slyme.utils.exception import BatchError, Result
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

_T = TypeVar("_T")
_R = TypeVar("_R")


def _batch(
    values: Sequence[_T], call: Callable[[_T], _R | Awaitable[_R]]
) -> list[_R] | Awaitable[list[_R]]:
    """Settle independent evaluations in input order, retaining every outcome.

    Calls run inline before scheduling their asynchronous results. Item failures
    do not cancel siblings. Caller cancellation follows asyncio.gather without
    aggregating partial results.
    """

    def execute() -> Generator[Any, Any, list[_R]]:
        results: list[Result[_R]] = []
        pending: dict[int, Awaitable[Result[_R]]] = {}

        def evaluate(value: _T) -> Generator[Any, Any, Result[_R]]:
            try:
                return Result(value=(yield call(value)))
            except BaseException as error:
                return Result(error=error)

        async def wait_pending() -> None:
            settled = await asyncio.gather(*pending.values())
            for index, result in zip(pending, settled, strict=True):
                results[index] = result

        for value in values:
            result = run(evaluate(value))
            if isawaitable(result):
                pending[len(results)] = result
                results.append(Result())
            else:
                results.append(result)

        if pending:
            yield wait_pending()
        if any(result.error is not None for result in results):
            raise BatchError(results)
        return [cast(_R, result.value) for result in results]

    return run(execute())


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
    batches = list(eval_groups.items())

    def evaluate(batch: tuple[EvaluatorDef, tuple[list[int], list[Any]]]) -> Any:
        evaluator, (indices, values) = batch

        def execute() -> Generator[Any, Any, None]:
            result = yield evaluator.func(ctx, values)
            if len(result) != len(indices):
                raise ValueError(
                    f"Evaluator {evaluator} returned {len(result)} results, "
                    f"expected {len(indices)}."
                )
            for index, value in zip(indices, result, strict=True):
                leaves[index] = value

        return run(execute())

    def execute() -> Generator[Any, Any, Any]:
        yield _batch(batches, evaluate)
        return CTX_EVAL_ENGINE.unflatten(tree_def, leaves)

    return run(execute())


# --- Evaluator Implementations ---
# Ref
def ref_evaluator(
    ctx: Context, refs: Sequence[Ref]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    return _batch(refs, ctx.get)


EVALUATOR_REGISTRY.register(EvaluatorDef(ref_evaluator), key=Ref)


def node_evaluator(
    ctx: Context, nodes: Sequence[Node]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    """Evaluate every sibling and report failures after all children settle.

    Calls execute inline before their asynchronous results are scheduled.
    Successful children dispose immediately; failure cleanup follows the batch.
    Child failure never cancels siblings. Caller cancellation follows asyncio
    propagation, with child cleanup completed before leaving the evaluator.
    """
    children: list[Context] = []

    def evaluate_one(node: Node) -> Any:
        child = ctx.fork(scope=ctx.scope.fork())
        children.append(child)

        def execute() -> Generator[Any, Any, Any]:
            value = yield node(child)
            yield child.dispose()
            return value

        return run(execute())

    def cleanup(error: BaseException) -> Any:
        def release(child: Context) -> None | Awaitable[None]:
            def execute() -> Generator[Any, Any, None]:
                try:
                    yield child.dispose()
                except BaseException as failure:
                    if not isinstance(error, BatchError) or all(
                        failure is not previous.error for previous in error.results
                    ):
                        raise

            return run(execute())

        def execute() -> Generator[Any, Any, NoReturn]:
            try:
                yield _batch(children, release)
            except BaseException as failure:
                if isinstance(error, asyncio.CancelledError):
                    raise failure from error
                raise error from failure
            raise error

        pending = run(execute())

        async def finish() -> NoReturn:
            task = asyncio.create_task(await_result(pending))
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    # The original failure or cancellation is reported after cleanup.
                    continue
            task.result()

        return finish()

    def execute() -> Generator[Any, Any, Sequence[Any]]:
        try:
            return (yield _batch(nodes, evaluate_one))
        except BaseException as error:
            return (yield cleanup(error))

    return run(execute())


EVALUATOR_REGISTRY.register(EvaluatorDef(node_evaluator), key=Node)
