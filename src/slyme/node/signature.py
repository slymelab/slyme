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
)
from collections import ChainMap
from slyme.utils.exception import enrich_exception

__all__ = [
    "field",
]
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


# Spec Definitions
@dataclass(frozen=True)
class Field:
    """Build-time configuration for functional node parameters."""
    default: Union[Any, _Missing] = _MISSING
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING

    def resolve(self, value: Any = _MISSING) -> Any:
        if value is not _MISSING:
            return value
        if self.default is not _MISSING:
            return self.default
        if self.default_factory is not _MISSING:
            return self.default_factory()
        raise ValueError("Missing required parameter.")


def field(
    default: Union[Any, _Missing] = _MISSING,
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING,
) -> Any:
    return Field(default=default, default_factory=default_factory)


@dataclass(frozen=True)
class Spec:
    """
    Dependency injection metadata for functional node parameters.
    """
    # NOTE: ``Field`` and ``field`` here are not from ``dataclasses``.
    field: Field = field()


# Inspection & Signature Analysis
@dataclass(frozen=True)
class SignatureAnalysis:
    pos_only_params: tuple[inspect.Parameter, ...]
    public_signature: inspect.Signature
    specs: Mapping[str, Spec]

    def __post_init__(self):
        if not isinstance(self.specs, types.MappingProxyType):
            object.__setattr__(self, "specs", types.MappingProxyType(self.specs))


def resolve_spec(param: inspect.Parameter, hint: Any) -> Spec:
    if param.default is not inspect.Parameter.empty:
        if isinstance(param.default, Field):
            return Spec(field=param.default)
        return Spec(field=field(default=param.default))
    return Spec()


def analyze_signature(func: Callable) -> SignatureAnalysis:
    """
    Analyze the function signature to separate runtime parameters and config parameters.
    """
    sig = inspect.signature(func)
    params = list(sig.parameters.values())

    pos_only_params = []
    public_params = []  # Used for factory signature
    specs: dict[str, Spec] = {}

    type_hints = get_type_hints(func, include_extras=True)

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
            value = spec_obj.field.resolve(value)
        final_kwargs[name] = value

    return final_kwargs


def resolve_arguments(
    specs: Mapping[str, Spec],
    sources: Sequence[Mapping[str, Any]],
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    """
    Resolve arguments from sources and overrides based on specs.
    """
    # 1. Create a unified lookup map: overrides > last source > ... > first source
    # ChainMap looks up keys in the first mapping, then the second, and so on.
    unified_map = ChainMap(overrides, *reversed(sources))
    # 2. Iterate through required parameters defined in the specs
    return {name: unified_map[name] for name in specs if name in unified_map}
