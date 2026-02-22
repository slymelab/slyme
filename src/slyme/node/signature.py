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
    Optional,
    Sequence,
)
from slyme.utils.exception import enrich_exception
from slyme.context import Ref

__all__ = [
    "spec",
    "ref_spec",
]
_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK


# Spec Definitions
@dataclass(frozen=True)
class Spec:
    """
    Dependency injection metadata for functional node parameters.
    """

    default: Union[Any, _Missing] = _MISSING
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING

    def __post_init__(self):
        if self.default is not _MISSING and self.default_factory is not _MISSING:
            raise ValueError(
                "Cannot specify both `default` and `default_factory` in Spec."
            )

    def resolve(self, value: Any = _MISSING) -> Any:
        """
        Resolve the final value for the parameter.
        """
        if value is not _MISSING:
            return value
        if self.default is not _MISSING:
            return self.default
        if self.default_factory is not _MISSING:
            return self.default_factory()
        raise ValueError("Missing required parameter.")


@dataclass(frozen=True)
class RefSpec(Spec):
    """
    Specialized Spec for Ref parameters, allowing metadata injection and type enforcement.
    """

    metadata: Optional[Mapping[str, Any]] = None

    def __post_init__(self):
        super().__post_init__()
        if self.metadata is not None:
            object.__setattr__(self, "metadata", types.MappingProxyType(self.metadata))

    def resolve(self, value: Union[_Missing, Ref] = _MISSING) -> Ref:
        # 1. Resolve the value using the base Spec logic (handling defaults)
        value = super().resolve(value)
        # 2. Type check: Ensure the resolved value is strictly a Ref
        if not isinstance(value, Ref):
            raise TypeError(
                f"The resolved value for this parameter must be an instance of 'Ref', "
                f"but got '{type(value).__name__}'."
            )
        # 3. Metadata injection: Update the Ref's metadata if specified in Spec
        if self.metadata is not None:
            return value.update_metadata(self.metadata)
        return value


# Spec Factory Functions
def spec(
    default: Union[Any, _Missing] = _MISSING,
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING,
) -> Any:
    """
    Factory function for creating a _Spec instance.
    Returns Any to bypass type checker errors when assigned as a default value.
    """
    return Spec(default=default, default_factory=default_factory)


def ref_spec(
    default: Union[Any, _Missing] = _MISSING,
    default_factory: Union[Callable[[], Any], _Missing] = _MISSING,
    *,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Any:
    """
    Factory function for creating a _RefSpec instance.
    Returns Any to bypass type checker errors when assigned as a default value.
    """
    return RefSpec(default=default, default_factory=default_factory, metadata=metadata)


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
    spec_obj: Union[_Missing, Spec] = _MISSING
    # 1. Check for Annotated
    if get_origin(hint) is Annotated:
        args = get_args(hint)
        if len(args) != 2:
            raise TypeError(
                f"Invalid Annotated metadata for parameter '{param.name}'."
                f"Currently, only a single `Spec` metadata is allowed, but found {len(args) - 1} items."
            )
        candidate = args[1]
        if not isinstance(candidate, Spec):
            raise TypeError(
                f"Invalid Annotated metadata for parameter '{param.name}'."
                f"Expected explicit `Spec` instance, but got {type(candidate).__name__}. "
                f"Other metadata types are strictly forbidden (for now)."
            )
        spec_obj = candidate

    # 2. Conflict Check and Collection
    if spec_obj is not _MISSING:
        # If Spec is defined in Annotated, strictly forbid standard default values.
        if param.default is not inspect.Parameter.empty:
            raise TypeError(
                f"Parameter '{param.name}' has a semantic conflict. "
                f"It defines a `Spec` in `Annotated` but also has a standard default value. "
                f"Please remove the standard default value assignment."
            )
        return spec_obj
    elif isinstance(param.default, Spec):
        # Spec is specified through func default value.
        return param.default
    elif param.default is not inspect.Parameter.empty:
        # Create a new Spec using default value.
        return Spec(default=param.default)
    else:
        # Return an empty Spec for standard keyword arguments
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
            value = spec_obj.resolve(value)
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
    # 1. Start with explicit overrides
    final_kwargs = dict(overrides)

    # 2. Iterate through required parameters defined in the specs
    for name in specs.keys():
        if name in final_kwargs:
            continue
        # Look in sources (reverse order)
        for source in reversed(sources):
            if name in source:
                final_kwargs[name] = source[name]
                break

    return final_kwargs
