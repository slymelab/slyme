"""Inspect object structure and synchronous, environment-dependent interpretations."""

from __future__ import annotations

from .core import (
    InspectionEntry,
    InspectionGraph,
    InspectionObject,
    InspectionReference,
    Interpretation,
    Interpreter,
    InterpreterResolver,
    inspect,
)

__all__ = [
    "inspect",
    "Interpretation",
    "Interpreter",
    "InterpreterResolver",
    "InspectionGraph",
    "InspectionEntry",
    "InspectionObject",
    "InspectionReference",
]
