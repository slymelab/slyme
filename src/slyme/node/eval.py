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
from collections.abc import Awaitable, Callable, Generator, Iterable, Sequence
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
    values: Iterable[_T], call: Callable[[_T], _R | Awaitable[_R]]
) -> list[_R] | Awaitable[list[_R]]:
    """Settle independent evaluations in input order, retaining every outcome.

    Calls run inline before scheduling their asynchronous results. Item failures
    do not cancel siblings. Iterator failures wait for submitted work and retain
    item errors as their cause. Caller cancellation propagates through asyncio;
    simultaneous item failures are reported with cancellation as their cause.
    """

    def execute() -> Generator[Any, Any, list[_R]]:
        results: list[Result[_R]] = []
        pending: dict[int, Awaitable[Result[_R]]] = {}

        def evaluate(value: _T) -> Generator[Any, Any, Result[_R]]:
            try:
                return Result(value=(yield call(value)))
            except BaseException as error:
                return Result(error=error)

        async def wait_pending() -> asyncio.CancelledError | None:
            tasks = {
                index: asyncio.create_task(await_result(value))
                for index, value in pending.items()
            }
            cancellation = None
            try:
                await asyncio.gather(*tasks.values(), return_exceptions=True)
            except asyncio.CancelledError as error:
                cancellation = error
            for index, task in tasks.items():
                try:
                    results[index] = task.result()
                except BaseException as error:
                    results[index] = Result(error=error)
            return cancellation

        iteration_error: BaseException | None = None
        try:
            for value in values:
                result = run(evaluate(value))
                if isawaitable(result):
                    pending[len(results)] = result
                    results.append(Result())
                else:
                    results.append(result)
        except BaseException as error:
            iteration_error = error

        cancellation = (yield wait_pending()) if pending else None
        failed = any(result.error is not None for result in results)
        if iteration_error is not None:
            if failed:
                raise iteration_error from BatchError(results)
            raise iteration_error
        if cancellation is not None:
            if any(
                result.error is not None
                and not isinstance(result.error, asyncio.CancelledError)
                for result in results
            ):
                raise BatchError(results) from cancellation
            raise cancellation
        if failed:
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
    batches = iter(eval_groups.items())

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

        def report(outcome: list[None] | BaseException) -> NoReturn:
            if isinstance(outcome, BaseException):
                if isinstance(error, asyncio.CancelledError):
                    raise outcome from error
                raise error from outcome
            raise error

        def release_all() -> Generator[Any, Any, list[None] | BaseException]:
            try:
                return (yield _batch(children, release))
            except BaseException as failure:
                return failure

        pending = run(release_all())
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

    def execute() -> Generator[Any, Any, Sequence[Any]]:
        try:
            return (yield _batch(nodes, evaluate_one))
        except BaseException as error:
            return (yield cleanup(error))

    return run(execute())


EVALUATOR_REGISTRY.register(EvaluatorDef(node_evaluator), key=Node)
