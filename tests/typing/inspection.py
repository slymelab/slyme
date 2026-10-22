from __future__ import annotations

from collections.abc import Mapping

from typing_extensions import assert_type

from slyme.context import (
    INSPECTION_INTERPRETERS_REF,
    INSPECTION_RAW_VALUE_TYPES_REF,
    INSPECTION_TREE_RULES_REF,
    INSPECTION_TYPE_NAMES_REF,
    Context,
)
from slyme.context.inspection import resolve_inspection_options
from slyme.inspection import (
    InspectionGraph,
    InspectionObject,
    InspectionReference,
    Interpretation,
    Interpreter,
    InterpreterResolver,
    inspect,
)
from slyme.tree import TreeRules


def check_inspection_types(
    ctx: Context, value: object, type_names: Mapping[type, str]
) -> None:
    document = inspect(value, **resolve_inspection_options(ctx))
    assert_type(document, InspectionGraph)
    assert_type(
        inspect(value, tree_rules=TreeRules(), raw_value_types=None), InspectionGraph
    )
    options = resolve_inspection_options(ctx)
    assert_type(
        inspect(
            value,
            tree_rules=options["tree_rules"],
            interpreters=options["interpreters"],
            type_names=options["type_names"],
            raw_value_types=None,
        ),
        InspectionGraph,
    )
    assert_type(
        inspect(value, **resolve_inspection_options(ctx), budget=100, max_depth=2),
        InspectionGraph,
    )
    assert_type(document["root"], InspectionReference)
    assert_type(document["objects"][document["root"]["$ref"]]["value"], object)
    assert_type(
        inspect(value, **resolve_inspection_options(ctx), env=ctx), InspectionGraph
    )
    assert_type(ctx.get(INSPECTION_TREE_RULES_REF).resolve(ctx.scope), TreeRules)
    assert_type(
        ctx.get(INSPECTION_INTERPRETERS_REF).resolve(ctx.scope),
        dict[type, Interpreter],
    )
    assert_type(ctx.get(INSPECTION_TYPE_NAMES_REF).resolve(ctx.scope), dict[type, str])
    assert_type(
        ctx.get(INSPECTION_RAW_VALUE_TYPES_REF).resolve(ctx.scope), dict[type, None]
    )
    assert_type(resolve_inspection_options(ctx)["raw_value_types"], dict[type, None])
    assert_type(
        inspect(value, tree_rules=TreeRules(), type_names=type_names), InspectionGraph
    )

    def select(value: object, env: object) -> bool:
        return False

    resolver: InterpreterResolver = select
    assert_type(
        inspect(
            value, **resolve_inspection_options(ctx), interpreter_resolver=resolver
        ),
        InspectionGraph,
    )
    assert_type(Interpretation(value, env=None).env, object)
    record: InspectionObject = {
        "kind": "object",
        "type": "example.value",
        "entries": [],
    }
    assert_type(record["type"], str)
