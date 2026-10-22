from __future__ import annotations

from collections.abc import Mapping

from typing_extensions import assert_type

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


def check_inspection_types(value: object, type_names: Mapping[type, str]) -> None:
    document = inspect(value, tree_rules=TreeRules())
    assert_type(document, InspectionGraph)
    assert_type(document["root"], InspectionReference)
    assert_type(document["objects"][document["root"]["$ref"]]["value"], object)
    assert_type(
        inspect(value, tree_rules=TreeRules(), budget=100, max_depth=2),
        InspectionGraph,
    )
    assert_type(
        inspect(value, tree_rules=TreeRules(), type_names=type_names), InspectionGraph
    )

    def select(value: object, env: object) -> bool:
        return False

    def interpret(value: object, env: object) -> Interpretation:
        return Interpretation(value, env=env)

    interpreter: Interpreter = interpret
    resolver: InterpreterResolver = select
    assert_type(
        inspect(
            value,
            tree_rules=TreeRules(),
            interpreters={object: interpreter},
            interpreter_resolver=resolver,
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
