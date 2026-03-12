from functools import partial
from typing import Any
from slyme.context import Context, Ref
from slyme.context.tree import CTX_EVAL_ENGINE
from .core import Expression
from .signature import EVALUATOR_REGISTRY, EvaluatorFunc

__all__ = [
    "eval_expression_tree",
]


def eval_expression_tree(ctx: Context, expression_tree: Any) -> Any:
    return CTX_EVAL_ENGINE.map(lambda expr: expr(ctx), expression_tree)


# Register eval funcs
@EVALUATOR_REGISTRY.register(key=Ref)
def ref_evaluator(ref_tree: Any, **kwargs: Any) -> EvaluatorFunc:
    def _evaluate(ctx: Context) -> Any:
        return ctx.extract(ref_tree)
    return _evaluate


@EVALUATOR_REGISTRY.register(key=Expression)
def expression_evaluator(expression_tree: Any, **kwargs: Any) -> EvaluatorFunc:
    return partial(eval_expression_tree, expression_tree=expression_tree)
