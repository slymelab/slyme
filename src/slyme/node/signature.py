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

import inspect
import sys
import types
import typing
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import Enum
from typing import (
    Annotated,
    Any,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
)

from slyme.context import Context
from slyme.utils.exception import enrich_exception

__all__ = [
    "spec",
    "Auto",
    "UNSET",
    "UNDEFINED",
]
T = TypeVar("T")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
Unset = Enum("Unset", ["MARK"])
UNSET = Unset.MARK
Undefined = Enum("Undefined", ["MARK"])
UNDEFINED = Undefined.MARK
EvaluatorFunc = Callable[[Context], Any]


# Spec Definitions
@dataclass(frozen=True)
class Spec:
    """
    Dependency injection metadata for functional node parameters.
    """

    default: Any | _Missing = _MISSING
    default_factory: Callable[[], Any] | _Missing = _MISSING
    auto_eval: bool | _Missing = _MISSING

    def __post_init__(self):
        if self.default is not _MISSING and self.default_factory is not _MISSING:
            raise ValueError(
                "`default` and `default_factory` cannot be set at the same time."
            )

    def _build(self, value: Any = _MISSING) -> Any:
        if value is not _MISSING and value is not UNSET:
            result = value
        elif self.default is not _MISSING:
            result = self.default
        elif self.default_factory is not _MISSING:
            result = self.default_factory()
        else:
            return UNDEFINED
        return result

    def should_eval(self, value: Any) -> bool:
        if self.auto_eval is _MISSING:
            raise ValueError(
                "`auto_eval` should not be `_MISSING` when `should_eval` is called."
            )
        if not self.auto_eval:
            return False
        from slyme.node.eval import contains_eval_type

        return contains_eval_type(value)


def spec(
    default: Any | _Missing = _MISSING,
    default_factory: Callable[[], Any] | _Missing = _MISSING,
    auto_eval: bool | _Missing = _MISSING,
) -> Any:
    return Spec(default=default, default_factory=default_factory, auto_eval=auto_eval)


# Some syntactic sugar
Auto = Annotated[T, Spec(auto_eval=True)]


# Inspection & Signature Analysis
@dataclass(frozen=True)
class SignatureAnalysis:
    runtime_params: tuple[inspect.Parameter, ...]
    public_signature: inspect.Signature
    specs: Mapping[str, Spec]

    def __post_init__(self):
        if not isinstance(self.specs, types.MappingProxyType):
            object.__setattr__(self, "specs", types.MappingProxyType(self.specs))


def _collect_specs_from_hint(hint: Any, default_is_none: bool) -> list[Spec]:
    """Recursively collect Spec annotations from a type hint."""
    # NOTE: Compatibility for Python < 3.11: get_type_hints auto-wraps parameters
    # with None defaults in Optional. Safely unwrap this outer union to expose
    # the underlying Annotated type.
    if sys.version_info < (3, 11):
        if default_is_none and get_origin(hint) in (
            types.UnionType,
            typing.Union,
        ):
            args = get_args(hint)
            if len(args) == 2 and type(None) in args:
                hint = args[0] if args[1] is type(None) else args[1]

    specs = []
    current = hint

    while get_origin(current) is Annotated:
        args = get_args(current)
        for item in args[1:]:
            if isinstance(item, Spec):
                specs.append(item)
        current = args[0]

    return specs


def _resolve_spec(param: inspect.Parameter, hint: Any) -> Spec:
    if hint is None:
        collected_specs = []
    else:
        collected_specs = _collect_specs_from_hint(hint, param.default is None)

    if param.default is not inspect.Parameter.empty:
        if isinstance(param.default, Spec):
            collected_specs.append(param.default)
        else:
            collected_specs.append(Spec(default=param.default))

    merged_values: dict[str, Any] = {
        "default": _MISSING,
        "default_factory": _MISSING,
        "auto_eval": _MISSING,
    }

    for s in collected_specs:
        for field in merged_values:
            if (val := getattr(s, field)) is _MISSING:
                continue
            if merged_values[field] is not _MISSING:
                raise ValueError(
                    f"Conflict: Multiple definitions for '{field}' in parameter '{param.name}'."
                )
            merged_values[field] = val

    if merged_values["auto_eval"] is _MISSING:
        merged_values["auto_eval"] = False

    return Spec(**merged_values)


def _validate_parameter_kind(param: inspect.Parameter) -> None:
    if param.kind not in (
        inspect.Parameter.VAR_POSITIONAL,
        inspect.Parameter.VAR_KEYWORD,
    ):
        return

    prefix = "*" if param.kind == inspect.Parameter.VAR_POSITIONAL else "**"
    raise TypeError(
        f"Variadic parameter '{prefix}{param.name}' is not supported. "
        "Node and Wrapper runtime parameters must be declared explicitly, "
        "and build parameters must be keyword-only."
    )


def analyze_signature(
    func: Callable, *, resolve_type_hints: bool = True
) -> SignatureAnalysis:
    """
    Analyze the function signature to separate runtime parameters and config parameters.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    runtime_params = []
    public_params = []  # Used for factory signature
    specs: dict[str, Spec] = {}

    for p in params:
        try:
            _validate_parameter_kind(p)
        except Exception as error:
            enrich_exception(error, f"in definition of '{func.__name__}'")
            raise

    if resolve_type_hints:
        type_hints = get_type_hints(func, include_extras=True)
    else:
        type_hints = {}

    for p in params:
        try:
            if p.kind == inspect.Parameter.KEYWORD_ONLY:
                public_params.append(p)
                # Spec Resolution Logic: Always returns a Spec object now
                specs[p.name] = _resolve_spec(p, type_hints.get(p.name))
            else:
                runtime_params.append(p)
        except Exception as error:
            enrich_exception(error, f"in definition of '{func.__name__}'")
            raise

    public_signature = sig.replace(parameters=public_params)
    return SignatureAnalysis(
        runtime_params=tuple(runtime_params),
        public_signature=public_signature,
        specs=types.MappingProxyType(specs),
    )
