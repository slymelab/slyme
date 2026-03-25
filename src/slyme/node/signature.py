# Copyright 2026 Slymer-Tech
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

import sys
import inspect
import types
from enum import Enum
from dataclasses import dataclass
from typing import (
    Callable,
    Any,
    Union,
    Annotated,
    get_type_hints,
    get_origin,
    get_args,
    Mapping,
    Sequence,
    TypeVar,
)
from collections import ChainMap
from slyme.utils.exception import enrich_exception
from slyme.context import Context

__all__ = [
    "spec",
    "Auto",
]
T = TypeVar("T")
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
EvaluatorFunc = Callable[[Context], Any]


# Spec Definitions
@dataclass(frozen=True)
class Spec:
    """
    Dependency injection metadata for functional node parameters.
    """

    default: Union[Any, _Missing] = _MISSING
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING
    auto_eval: Union[bool, _Missing] = _MISSING

    def __post_init__(self):
        if self.default is not _MISSING and self.default_factory is not _MISSING:
            raise ValueError(
                "`default` and `default_factory` cannot be set at the same time."
            )

    def _build(self, value: Any = _MISSING) -> Any:
        if value is not _MISSING:
            return value
        if self.default is not _MISSING:
            return self.default
        if self.default_factory is not _MISSING:
            return self.default_factory()
        raise ValueError("Missing required parameter.")

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
    default: Union[Any, _Missing] = _MISSING,
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING,
    auto_eval: Union[bool, _Missing] = _MISSING,
) -> Any:
    return Spec(default=default, default_factory=default_factory, auto_eval=auto_eval)


# Some syntactic sugar
Auto = Annotated[T, Spec(auto_eval=True)]


# Inspection & Signature Analysis
@dataclass(frozen=True)
class SignatureAnalysis:
    pos_only_params: tuple[inspect.Parameter, ...]
    public_signature: inspect.Signature
    specs: Mapping[str, Spec]

    def __post_init__(self):
        if not isinstance(self.specs, types.MappingProxyType):
            object.__setattr__(self, "specs", types.MappingProxyType(self.specs))


def _collect_specs_from_hint(hint: Any) -> list[Spec]:
    """Recursively collect Spec annotations from a type hint."""
    # NOTE: Compatibility for Python < 3.11: get_type_hints auto-wraps parameters
    # with None defaults in Optional. Safely unwrap this outer Optional/Union
    # to expose the underlying Annotated type.
    if sys.version_info < (3, 11):
        if get_origin(hint) is Union:
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


def resolve_spec(param: inspect.Parameter, hint: Any) -> Spec:
    if hint is None:
        collected_specs = []
    else:
        collected_specs = _collect_specs_from_hint(hint)

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


def analyze_signature(
    func: Callable, *, resolve_type_hints: bool = True
) -> SignatureAnalysis:
    """
    Analyze the function signature to separate runtime parameters and config parameters.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    pos_only_params = []
    public_params = []  # Used for factory signature
    specs: dict[str, Spec] = {}

    if resolve_type_hints:
        type_hints = get_type_hints(func, include_extras=True)
    else:
        type_hints = {}

    for p in params:
        with enrich_exception(f"in definition of '{func.__name__}'"):
            if p.kind == inspect.Parameter.POSITIONAL_ONLY:
                pos_only_params.append(p)
            elif p.kind == inspect.Parameter.KEYWORD_ONLY:
                public_params.append(p)
                # Spec Resolution Logic: Always returns a Spec object now
                specs[p.name] = resolve_spec(p, type_hints.get(p.name))
            else:
                kind_name = str(p.kind)
                raise TypeError(
                    f"Invalid parameter '{p.name}' of kind {kind_name}. "
                    f"Functional nodes strict rules:\n"
                    f"  1. Runtime args (e.g. ctx) must be POSITIONAL_ONLY (before '/').\n"
                    f"  2. Config args must be KEYWORD_ONLY (after '*')."
                )

    public_signature = sig.replace(parameters=public_params)
    return SignatureAnalysis(
        pos_only_params=tuple(pos_only_params),
        public_signature=public_signature,
        specs=types.MappingProxyType(specs),
    )


def process_kwargs(
    specs: Mapping[str, Spec],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    """
    Validate and process kwargs:
    1. Check for unexpected arguments.
    2. Check for missing required arguments.
    3. Inject Spec-resolved values.
    """
    allowed_names = set(specs.keys())
    input_names = set(kwargs.keys())

    # 1. Strict Subset Check
    unknown_args = input_names - allowed_names
    if unknown_args:
        raise TypeError(
            f"Got unexpected keyword argument(s) {list(unknown_args)}. "
            f"Allowed arguments: {list(allowed_names)}."
        )

    # 2. Apply Specs
    final_kwargs = {}
    for name, spec_obj in specs.items():
        value = kwargs.get(name, _MISSING)
        with enrich_exception(f"for parameter '{name}'"):
            value = spec_obj._build(value)
        final_kwargs[name] = value

    return final_kwargs


def resolve_arguments(
    specs: Mapping[str, Spec],
    scopes: Sequence[Mapping[str, Any]],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Resolve arguments from scopes and overrides based on specs.
    """
    # 1. Create a unified lookup map: overrides > last scope > ... > first scope
    # ChainMap looks up keys in the first mapping, then the second, and so on.
    unified_map = ChainMap(overrides, *reversed(scopes))
    # 2. Iterate through required parameters defined in the specs
    return {name: unified_map[name] for name in specs if name in unified_map}
