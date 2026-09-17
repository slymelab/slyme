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
from inspect import isawaitable
from typing import Any, TypeVar, cast

from slyme.context import Context, Ref
from slyme.context.tree import CTX_EVAL_ENGINE
from slyme.utils.exception import exception_group
from slyme.utils.execution import continuation
from slyme.utils.registry import GeneralRegistry

from .core import Node

__all__ = [
    "eval_tree",
    "EVALUATOR_REGISTRY",
    "BatchEvaluatorFunc",
]


BatchEvaluatorFunc = Callable[
    [Context, Sequence[Any]], Sequence[Any] | Awaitable[Sequence[Any]]
]
EVALUATOR_REGISTRY = GeneralRegistry[type, BatchEvaluatorFunc]("evaluator")

_T = TypeVar("_T")
_R = TypeVar("_R")


@continuation
def _batch(
    values: Sequence[_T], call: Callable[[_T], _R | Awaitable[_R]]
) -> Generator[Any, Any, list[_R]]:
    """Return ordered values, or group failures after every item settles.

    Calls run inline before scheduling their asynchronous results. Item failures
    do not cancel siblings. Caller cancellation follows asyncio.gather without
    aggregating partial results. Failure groups contain only errors, in input
    order, with their original input indices recorded in the group message.
    """

    results: list[_R | None] = [None] * len(values)
    errors: list[tuple[int, BaseException]] = []
    pending: list[Awaitable[None]] = []

    @continuation
    def evaluate(index: int, value: _T) -> Generator[Any, Any, None]:
        try:
            results[index] = yield call(value)
        except BaseException as error:
            errors.append((index, error))

    for index, value in enumerate(values):
        result = evaluate(index, value)
        if isawaitable(result):
            pending.append(result)

    if pending:

        async def gather() -> None:
            # NOTE: There may be no running event loop when calling asyncio.gather,
            # so we wrap it in a coroutine.
            await asyncio.gather(*pending)

        yield gather()
    if errors:
        errors.sort(key=lambda item: item[0])
        raise exception_group(
            f"Evaluation failed at input indices {[index for index, _ in errors]}",
            [error for _, error in errors],
        )
    return cast(list[_R], results)


@continuation
def eval_tree(ctx: Context, tree: Any) -> Generator[Any, Any, Any]:
    """Evaluate registered leaves and reconstruct every Tree container.

    Ordinary leaves and evaluator results retain their identities. Results are
    not recursively evaluated. Return an awaitable only for asynchronous work.
    Evaluators match the exact leaf type; subclasses require registration.
    Evaluator groups are independent batches and may execute concurrently.
    """
    leaves, tree_def = CTX_EVAL_ENGINE.flatten(tree)
    eval_groups: dict[BatchEvaluatorFunc, tuple[list[int], list[Any]]] = {}
    for i, leaf in enumerate(leaves):
        if (evaluator := EVALUATOR_REGISTRY.get(type(leaf), None)) is not None:
            if evaluator not in eval_groups:
                eval_groups[evaluator] = ([], [])
            indices, values = eval_groups[evaluator]
            indices.append(i)
            values.append(leaf)
    batches = list(eval_groups.items())

    @continuation
    def evaluate(
        batch: tuple[BatchEvaluatorFunc, tuple[list[int], list[Any]]],
    ) -> Generator[Any, Any, None]:
        evaluator, (indices, values) = batch
        result = yield evaluator(ctx, values)
        for index, value in zip(indices, result, strict=True):
            leaves[index] = value

    yield _batch(batches, evaluate)
    return CTX_EVAL_ENGINE.unflatten(tree_def, leaves)


# --- Evaluator Implementations ---
# Ref
def ref_evaluator(
    ctx: Context, refs: Sequence[Ref]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    return _batch(refs, ctx.get)


EVALUATOR_REGISTRY.register(ref_evaluator, key=Ref)


def node_evaluator(
    ctx: Context, nodes: Sequence[Node]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    """Evaluate every sibling and report failures after all children settle.

    Calls execute inline before their asynchronous results are scheduled.
    Each child disposes in finally, on success or failure. Cleanup failures
    replace evaluation failures using Python's exception chaining. Child failure
    never cancels siblings. Caller cancellation follows asyncio propagation and
    may leave Context-owned cleanup running after the evaluator exits.
    """

    @continuation
    def evaluate_one(node: Node) -> Generator[Any, Any, Any]:
        child = ctx.fork(scope=ctx.scope.fork())
        try:
            return (yield node(child))
        finally:
            yield child.dispose()

    return _batch(nodes, evaluate_one)


EVALUATOR_REGISTRY.register(node_evaluator, key=Node)
