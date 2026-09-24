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
from functools import partial
from inspect import isawaitable
from typing import Any, TypeVar, cast

from slyme.context import Context, Ref
from slyme.context.default import DATA_TREE_REF, EVALUATORS_REF
from slyme.utils.exception import exception_group
from slyme.utils.execution import continuation
from slyme.utils.tree import TreeEngine

from .core import Node

__all__ = [
    "eval_tree",
    "BatchEvaluatorFunc",
]


BatchEvaluatorFunc = Callable[
    [Context, Sequence[Any]], Sequence[Any] | Awaitable[Sequence[Any]]
]

_T = TypeVar("_T")
_R = TypeVar("_R")


@continuation
def _evaluate_item(
    call: Callable[[_T], _R | Awaitable[_R]],
    results: list[_R | None],
    errors: list[tuple[int, BaseException]],
    index: int,
    value: _T,
) -> Generator[Any, Any, None]:
    try:
        results[index] = yield call(value)
    except BaseException as error:
        errors.append((index, error))


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

    for index, value in enumerate(values):
        result = _evaluate_item(call, results, errors, index, value)
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
def _evaluate_group(
    ctx: Context,
    leaves: list[Any],
    batch: tuple[BatchEvaluatorFunc, tuple[list[int], list[Any]]],
) -> Generator[Any, Any, None]:
    evaluator, (indices, values) = batch
    result = yield evaluator(ctx, values)
    for index, value in zip(indices, result, strict=True):
        leaves[index] = value


@continuation
def eval_tree(ctx: Context, tree: Any) -> Generator[Any, Any, Any]:
    """Evaluate registered leaves and reconstruct every Tree container.

    Ordinary leaves and evaluator results retain their identities. Results are
    not recursively evaluated. Return an awaitable only for asynchronous work.
    Evaluators match the exact leaf type; subclasses require registration.
    Evaluator groups are independent batches and may execute concurrently.
    Effective tree rules and evaluator mappings are captured before traversal.
    """
    rules = ctx.get(DATA_TREE_REF).resolve(ctx.scope)
    evaluators = ctx.get(EVALUATORS_REF).resolve(ctx.scope)
    leaves, tree_def = TreeEngine.flatten(tree, rules=rules)
    eval_groups: dict[BatchEvaluatorFunc, tuple[list[int], list[Any]]] = {}
    for i, leaf in enumerate(leaves):
        if (evaluator := evaluators.get(type(leaf))) is not None:
            if evaluator not in eval_groups:
                eval_groups[evaluator] = ([], [])
            indices, values = eval_groups[evaluator]
            indices.append(i)
            values.append(leaf)
    batches = list(eval_groups.items())

    yield _batch(batches, partial(_evaluate_group, ctx, leaves))
    return TreeEngine.unflatten(tree_def, leaves)


# --- Evaluator Implementations ---
# Ref
def ref_evaluator(
    ctx: Context, refs: Sequence[Ref[Any]]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    """Read and await stored values, which may change the Ref's value type."""
    return _batch(refs, ctx.get)


@continuation
def _evaluate_node(ctx: Context, node: Node[_T]) -> Generator[Any, Any, _T]:
    child = ctx.derive()
    try:
        return (yield node(child))
    finally:
        yield child.dispose()


def node_evaluator(
    ctx: Context, nodes: Sequence[Node[Any]]
) -> Sequence[Any] | Awaitable[Sequence[Any]]:
    """Evaluate every sibling and report failures after all children settle.

    Calls execute inline before their asynchronous results are scheduled.
    Each child disposes in finally, on success or failure. Cleanup failures
    replace evaluation failures using Python's exception chaining. Child failure
    never cancels siblings. Caller cancellation follows asyncio propagation and
    may leave Context-owned cleanup running after the evaluator exits.
    """

    return _batch(nodes, partial(_evaluate_node, ctx))
