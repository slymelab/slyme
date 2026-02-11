"""
Core node module, consolidating base definitions and functional APIs.
"""

import inspect
import types
from abc import ABC, abstractmethod
from enum import Enum
from functools import partial, update_wrapper
from dataclasses import dataclass
from typing import (
    TypeVar,
    Callable,
    Any,
    Union,
    overload,
    Annotated,
    get_type_hints,
    get_origin,
    get_args,
    Mapping,
    Optional,
    Generic,
    Iterable,
    cast,
)
from typing_extensions import ParamSpec, Concatenate, Self
from slyme.utils.common import enrich_exception
from slyme.utils.pytree import (
    PyTreeEngine,
    PYTREE_ENGINE_REGISTRY,
    PyTreeAux,
    AttributeKey,
    MappingKey,
)
from slyme.context import Context, Ref
from .exception import (
    NodeTerminate,
    NodeExceptionRecord,
    NodeException,
    NodeExpressionExceptionRecord,
    NodeWrapperExceptionRecord,
)

__all__ = [
    "spec",
    "ref_spec",
    "node",
    "expression",
    "wrapper",
    "NodeElement",
    "Node",
    "NodeDef",
    "NodeExec",
    "NodeExpression",
    "NodeExpressionDef",
    "NodeExpressionExec",
    "NodeWrapper",
    "NodeWrapperDef",
    "NodeWrapperExec",
    "STOP",
    "NODE_PYTREE_ENGINE",
]

_P = ParamSpec("_P")
_R = TypeVar("_R")
_T = TypeVar("_T")

# Update: NodeFunc now must return Context to align with the chain protocol.
NodeFunc = Callable[Concatenate[Context, _P], Context]
ExpressionFunc = Callable[Concatenate[Context, _P], _R]
# Wrapper definition: (ctx, wrapped, call_next, ...args) -> Context
# call_next definition: (Context) -> Context
WrapperFunc = Callable[
    Concatenate[Context, "Node", Callable[[Context], Context], _P], Context
]

_Missing = Enum("_Missing", ["MARK"])
_MISSING = _Missing.MARK
Stop = Enum("Stop", ["MARK"])
STOP = Stop.MARK


# --- Spec Definitions ---


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


# --- Spec Factory Functions ---


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


# --- Inspection & Signature Analysis ---


@dataclass(frozen=True)
class _SignatureAnalysis:
    pos_only_params: list[inspect.Parameter]
    public_signature: inspect.Signature
    specs: Mapping[str, Spec]


def _resolve_spec(param: inspect.Parameter, hint: Any) -> Spec:
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


def _analyze_signature(func: Callable) -> _SignatureAnalysis:
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
                specs[p.name] = _resolve_spec(p, type_hints.get(p.name))
            else:
                kind_name = str(p.kind)
                raise TypeError(
                    f"Invalid parameter '{p.name}' of kind {kind_name}. "
                    f"Functional nodes strict rules:\n"
                    f"  1. Runtime args (e.g. ctx) must be POSITIONAL_ONLY (before '/').\n"
                    f"  2. Config args must be KEYWORD_ONLY (after '*')."
                )

    public_signature = sig.replace(parameters=public_params)
    return _SignatureAnalysis(
        pos_only_params=pos_only_params,
        public_signature=public_signature,
        specs=types.MappingProxyType(specs),
    )


def _process_kwargs(
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


# --- Node Class Definitions ---


class NodeElement(ABC):
    """
    Base class for all node-related entities.
    Abstract definition that separates API from implementation.
    """

    _func: Callable
    _specs: Mapping[str, Spec]
    _kwargs: dict[str, Any]

    @abstractmethod
    def prepare(self) -> "NodeElement":
        """
        Prepare the node for execution.
        Converts the Definition structure into an Execution structure.
        """
        pass

    def __getitem__(self, key: str) -> Any:
        return self._kwargs[key]

    def __repr__(self) -> str:
        return get_render_string(self)

    def extra_repr(self) -> str:
        return ""

    def type_repr(self) -> str:
        return self._func.__name__


# --- Node Family ---


class Node(NodeElement):
    """
    Abstract base class for NodeDef and NodeExec.
    """

    _func: NodeFunc
    wrappers: Union[list["NodeWrapper"], tuple["NodeWrapper", ...]]

    @abstractmethod
    def __call__(self, ctx: Context) -> Context:
        pass


class NodeDef(Node):
    """
    Mutable definition of a Node. Allows modification during build time.
    """

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: Optional[Iterable["NodeWrapper"]] = None,
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "wrappers", list(wrappers) if wrappers else [])
        object.__setattr__(self, "_kwargs", kwargs)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        self._kwargs[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __call__(self, ctx: Context) -> Context:
        raise RuntimeError(
            f"Cannot execute {type(self).__name__}. "
            f"Please call `.prepare()` to obtain a generic `{NodeExec.__name__}` first."
        )

    def prepare(self) -> "NodeExec":
        # Use the specialized FREEZE_PYTREE_ENGINE to perform a deep transform
        # of the structure (List -> Tuple, Dict -> MappingProxy, Def -> Exec).
        # We map strict identity because the transformation happens in the 'unflatten' phase
        # of the registered types in FREEZE_PYTREE_ENGINE.
        return FREEZE_PYTREE_ENGINE.map(lambda x: x, self)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"Cannot delete attribute '{name}' on {type(self).__name__}")

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "wrappers":
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(
                f"Cannot set attribute '{name}' on {type(self).__name__}. "
                "Only 'wrappers' can be modified."
            )


class NodeExec(Node):
    """
    Immutable execution version of a Node.
    """

    # composed_func signature: (Context) -> Context
    _composed_func: Callable[[Context], Context]

    def __init__(
        self,
        /,
        *,
        func: NodeFunc,
        specs: Mapping[str, Spec],
        wrappers: tuple["NodeWrapper", ...],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "wrappers", wrappers)
        object.__setattr__(self, "_kwargs", types.MappingProxyType(kwargs))

        # --- Composition Logic (Onion Model) ---
        
        # 1. Inner Core: Bind kwargs to the user function.
        # Signature: (Context) -> Context
        # Since NodeFunc is now strictly defined to return Context, no adapter is needed.
        chain: Callable[[Context], Context] = partial(func, **kwargs)

        # 2. Build the middleware chain.
        # Wrappers are applied from inside out (reversed order of list).
        for wrapper in reversed(wrappers):
            # wrapper is NodeWrapperExec which is callable: (ctx, wrapped, call_next) -> Context
            # We partially apply `wrapped` (self) and `call_next` (current chain head)
            # to create the new chain head: (Context) -> Context
            chain = partial(wrapper, wrapped=self, call_next=chain)

        object.__setattr__(self, "_composed_func", chain)

    def prepare(self) -> Self:
        return self

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(
            f"{type(self).__name__} is immutable and does not support item assignment."
        )

    def __delattr__(self, name: str) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __call__(self, ctx: Context) -> Context:
        try:
            # Execute the pre-composed chain
            return self._composed_func(ctx)
        # Node Interrupts
        except (NodeTerminate, NodeExpressionExceptionRecord) as e:
            if e.source_node is None:
                e.source_node = self
            raise
        # Direct Node Exceptions
        except NodeException:
            raise
        # General Exceptions
        except Exception as e:
            raise NodeExceptionRecord(exception_node=self, exception=e)


# --- Expression Family ---


class NodeExpression(NodeElement, Generic[_R]):
    """
    Abstract base class for NodeExpressionDef and NodeExpressionExec.
    """

    _func: ExpressionFunc

    @abstractmethod
    def __call__(self, ctx: Context) -> _R:
        pass


class NodeExpressionDef(NodeExpression[_R]):
    """
    Mutable definition of a NodeExpression.
    """

    def __init__(
        self,
        /,
        *,
        func: ExpressionFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        self._kwargs[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __call__(self, ctx: Context) -> _R:
        raise RuntimeError(
            f"Cannot execute {type(self).__name__}. "
            f"Please call `.prepare()` to obtain a generic `{NodeExpressionExec.__name__}` first."
        )

    def prepare(self) -> "NodeExpressionExec[_R]":
        return FREEZE_PYTREE_ENGINE.map(lambda x: x, self)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"Cannot delete attribute '{name}' on {type(self).__name__}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot set attribute '{name}' on {type(self).__name__}. "
            "Internal structure is protected."
        )


class NodeExpressionExec(NodeExpression[_R]):
    """
    Immutable execution version of a NodeExpression.
    """

    # composed_func signature: (Context) -> _R
    _composed_func: Callable[[Context], _R]

    def __init__(
        self,
        /,
        *,
        func: ExpressionFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", types.MappingProxyType(kwargs))
        # Optimization: Pre-bind kwargs using partial
        object.__setattr__(self, "_composed_func", partial(func, **kwargs))

    def prepare(self) -> Self:
        return self

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(
            f"{type(self).__name__} is immutable and does not support item assignment."
        )

    def __delattr__(self, name: str) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __call__(self, ctx: Context) -> _R:
        try:
            return self._composed_func(ctx)
        except NodeException:
            raise
        except Exception as e:
            raise NodeExpressionExceptionRecord(exception_node=self, exception=e)


# --- Wrapper Family ---


class NodeWrapper(NodeElement):
    """
    Abstract base class for NodeWrapperDef and NodeWrapperExec.
    """

    _func: WrapperFunc

    @abstractmethod
    def __call__(
        self,
        ctx: Context,
        wrapped: Node,
        call_next: Callable[[Context], Context],
    ) -> Context:
        pass


class NodeWrapperDef(NodeWrapper):
    """
    Mutable definition of a NodeWrapper.
    """

    def __init__(
        self,
        /,
        *,
        func: WrapperFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", kwargs)

    def __setitem__(self, key: str, value: Any) -> None:
        if key not in self._specs:
            raise KeyError(
                f"Invalid key '{key}'. Parameters must be defined in the specs."
            )
        self._kwargs[key] = value

    def update(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            self[k] = v

    def __call__(
        self,
        ctx: Context,
        wrapped: Node,
        call_next: Callable[[Context], Context],
    ) -> Context:
        raise RuntimeError(
            f"Cannot execute {type(self).__name__}. "
            f"Please call `.prepare()` to obtain a generic `{NodeWrapperExec.__name__}` first."
        )

    def prepare(self) -> "NodeWrapperExec":
        return FREEZE_PYTREE_ENGINE.map(lambda x: x, self)

    def __delattr__(self, name: str) -> None:
        raise AttributeError(f"Cannot delete attribute '{name}' on {type(self).__name__}")

    def __setattr__(self, name: str, value: Any) -> None:
        raise AttributeError(
            f"Cannot set attribute '{name}' on {type(self).__name__}. "
            "Internal structure is protected."
        )


class NodeWrapperExec(NodeWrapper):
    """
    Immutable execution version of a NodeWrapper.
    """

    # composed_func signature: (ctx, wrapped, call_next) -> Context
    _composed_func: Callable[
        [Context, Node, Callable[[Context], Context]],
        Context,
    ]

    def __init__(
        self,
        /,
        *,
        func: WrapperFunc,
        specs: Mapping[str, Spec],
        kwargs: dict[str, Any],
    ):
        object.__setattr__(self, "_func", func)
        object.__setattr__(self, "_specs", specs)
        object.__setattr__(self, "_kwargs", types.MappingProxyType(kwargs))
        # Optimization: Pre-bind kwargs using partial
        object.__setattr__(self, "_composed_func", partial(func, **kwargs))

    def prepare(self) -> Self:
        return self

    def __setitem__(self, key: str, value: Any) -> None:
        raise TypeError(
            f"{type(self).__name__} is immutable and does not support item assignment."
        )

    def __delattr__(self, name: str) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __setattr__(self, name: str, value: Any) -> None:
        raise TypeError(f"{type(self).__name__} is immutable.")

    def __call__(
        self,
        ctx: Context,
        wrapped: Node,
        call_next: Callable[[Context], Context],
    ) -> Context:
        try:
            return self._composed_func(ctx, wrapped, call_next)
        except NodeException:
            raise
        except Exception as e:
            raise NodeWrapperExceptionRecord(
                exception_node=self, wrapped_node=wrapped, exception=e
            )


# --- PyTree Engine Configuration ---

# 1. Standard Engine: Preserves Types (Def -> Def, List -> List)
NODE_PYTREE_ENGINE = PyTreeEngine("node_engine")
PYTREE_ENGINE_REGISTRY.register(NODE_PYTREE_ENGINE, key="node_engine")

# 2. Freeze Engine: Transforms Types (Def -> Exec, List -> Tuple)
FREEZE_PYTREE_ENGINE = PyTreeEngine("freeze_engine")


# --- Flatten Logic (Shared) ---
# Flattening logic is structurally identical for Def and Exec,
# we can reuse the logic to extract children.

def _flatten_node_def(obj: NodeDef) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    rich_keys = [AttributeKey("wrappers")]
    for k, v in obj._kwargs.items():
        children.append(v)
        rich_keys.append(MappingKey(k))
    metadata = {"func": obj._func, "specs": obj._specs}
    return tuple(children), PyTreeAux(
        keys=tuple(rich_keys), metadata=metadata, cls=NodeDef
    )

def _flatten_element_def(
    obj: Union[NodeExpressionDef, NodeWrapperDef]
) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(obj._kwargs.keys())
    children = tuple(obj._kwargs.values())
    rich_keys = tuple(MappingKey(k) for k in keys)
    metadata = {"func": obj._func, "specs": obj._specs}
    return children, PyTreeAux(keys=rich_keys, metadata=metadata, cls=type(obj))


# --- Unflatten Logic for Standard Engine (Preserves Type) ---

def _unflatten_node_def(children: Iterable[Any], aux: PyTreeAux) -> Any:
    children_iter = iter(children)
    keys_iter = iter(aux.keys)
    _ = next(keys_iter)
    wrappers = next(children_iter)
    kwargs = {}
    for key in keys_iter:
        raw_key = cast("MappingKey", key).key
        val = next(children_iter)
        kwargs[raw_key] = val
    return NodeDef(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        kwargs=kwargs,
    )

def _unflatten_element_def(children: Iterable[Any], aux: PyTreeAux) -> Any:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return aux.cls(func=aux.metadata["func"], specs=aux.metadata["specs"], kwargs=kwargs)


# --- Unflatten Logic for Freeze Engine (Transforms Type) ---
# This is the "Magic" part: Def -> Exec conversion happens here during reconstruction.

def _unflatten_def_to_exec_node(children: Iterable[Any], aux: PyTreeAux) -> NodeExec:
    children_iter = iter(children)
    keys_iter = iter(aux.keys)
    
    # 1. Wrappers: Already converted to tuple by FREEZE_PYTREE_ENGINE recursion
    _ = next(keys_iter)
    wrappers = cast(tuple, next(children_iter))

    # 2. Kwargs: Recursively frozen children
    kwargs = {}
    for key in keys_iter:
        raw_key = cast("MappingKey", key).key
        val = next(children_iter)
        kwargs[raw_key] = val
        
    return NodeExec(
        func=aux.metadata["func"],
        specs=aux.metadata["specs"],
        wrappers=wrappers,
        kwargs=kwargs,
    )

def _unflatten_def_to_exec_expression(children: Iterable[Any], aux: PyTreeAux) -> NodeExpressionExec:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeExpressionExec(
        func=aux.metadata["func"], 
        specs=aux.metadata["specs"], 
        kwargs=kwargs
    )

def _unflatten_def_to_exec_wrapper(children: Iterable[Any], aux: PyTreeAux) -> NodeWrapperExec:
    raw_keys = [cast("MappingKey", k).key for k in aux.keys]
    kwargs = dict(zip(raw_keys, children))
    return NodeWrapperExec(
        func=aux.metadata["func"], 
        specs=aux.metadata["specs"], 
        kwargs=kwargs
    )

# --- Standard Container Freezing Logic ---

def _flatten_list(l: list) -> tuple[Iterable[Any], PyTreeAux]:
    return iter(l), PyTreeAux()

def _unflatten_to_tuple(children: Iterable[Any], _: PyTreeAux) -> tuple:
    return tuple(children)

def _flatten_dict(d: dict) -> tuple[Iterable[Any], PyTreeAux]:
    keys = tuple(d.keys())
    rich_keys = tuple(MappingKey(k) for k in keys)
    children = (d[k] for k in keys)
    return children, PyTreeAux(keys=rich_keys)

def _unflatten_to_mapping_proxy(children: Iterable[Any], aux: PyTreeAux) -> types.MappingProxyType:
    raw_keys = [k.key for k in cast("Iterable[MappingKey]", aux.keys)]
    return types.MappingProxyType(dict(zip(raw_keys, children)))


# --- Registrations ---

# 1. NODE_PYTREE_ENGINE (Standard: Def -> Def)
NODE_PYTREE_ENGINE.register(NodeDef, _flatten_node_def, _unflatten_node_def, strict=True)
NODE_PYTREE_ENGINE.register(NodeExpressionDef, _flatten_element_def, _unflatten_element_def, strict=True)
NODE_PYTREE_ENGINE.register(NodeWrapperDef, _flatten_element_def, _unflatten_element_def, strict=True)

# Also register Exec types for completeness (Exec -> Exec), needed if we inspect an already prepared tree
def _flatten_node_exec(obj: NodeExec) -> tuple[Iterable[Any], PyTreeAux]:
    children = [obj.wrappers]
    rich_keys = [AttributeKey("wrappers")]
    for k, v in obj._kwargs.items():
        children.append(v)
        rich_keys.append(MappingKey(k))
    metadata = {"func": obj._func, "specs": obj._specs}
    return tuple(children), PyTreeAux(keys=tuple(rich_keys), metadata=metadata, cls=NodeExec)

def _unflatten_node_exec(children: Iterable[Any], aux: PyTreeAux) -> Any:
    # Logic similar to _unflatten_node_def but creates Exec
    # This is for NODE_PYTREE_ENGINE (Exec -> Exec)
    children_iter = iter(children)
    keys_iter = iter(aux.keys)
    _ = next(keys_iter)
    wrappers = next(children_iter)
    kwargs = {}
    for key in keys_iter:
        raw_key = cast("MappingKey", key).key
        val = next(children_iter)
        kwargs[raw_key] = val
    return NodeExec(func=aux.metadata["func"], specs=aux.metadata["specs"], wrappers=wrappers, kwargs=kwargs)

NODE_PYTREE_ENGINE.register(NodeExec, _flatten_node_exec, _unflatten_node_exec, strict=True)
# (Omitting Exec->Exec registration for Expression/Wrapper for brevity, logic is similar)


# 2. FREEZE_PYTREE_ENGINE (Transformer: Def -> Exec, List -> Tuple, Dict -> Proxy)
# Custom Containers
FREEZE_PYTREE_ENGINE.register(list, _flatten_list, _unflatten_to_tuple)
FREEZE_PYTREE_ENGINE.register(dict, _flatten_dict, _unflatten_to_mapping_proxy)

# Def -> Exec Transformations
FREEZE_PYTREE_ENGINE.register(NodeDef, _flatten_node_def, _unflatten_def_to_exec_node, strict=True)
FREEZE_PYTREE_ENGINE.register(NodeExpressionDef, _flatten_element_def, _unflatten_def_to_exec_expression, strict=True)
FREEZE_PYTREE_ENGINE.register(NodeWrapperDef, _flatten_element_def, _unflatten_def_to_exec_wrapper, strict=True)

# Passthrough for already frozen types (Exec -> Exec, Tuple -> Tuple)
# We can register the default container handlers to ensure recursive traversal continues
FREEZE_PYTREE_ENGINE._register_defaults() 
# Overwrite list/dict defaults which we just registered above? No, register_defaults checks priority.
# Since we manually registered list/dict, they take precedence in TypeRegistry.
# But we need tuple to work recursively.
# (PyTreeEngine._register_defaults registers tuple, list, dict)
# Since we manually registered list/dict, we are good. Tuple uses default behavior (Tuple -> Tuple).


# --- Functional Factory & Decorators ---
class FunctionalFactory(Generic[_T, _P]):
    def __init__(self, cls: type[_T], func: Callable, analysis: _SignatureAnalysis):
        update_wrapper(self, func)
        self._cls = cls
        self._func = func
        self._specs = analysis.specs
        self.__signature__ = analysis.public_signature

    def __call__(self, *_: _P.args, **kwargs: _P.kwargs) -> _T:
        """
        Create the node instance using keyword arguments.
        """
        # Process kwargs
        with enrich_exception(f"for '{self._func.__name__}'"):
            final_kwargs = _process_kwargs(self._specs, kwargs)

        # Determine extra args based on class
        extra_args = {}
        if issubclass(self._cls, Node) and "wrappers" in final_kwargs:
            extra_args["wrappers"] = final_kwargs.pop("wrappers")

        return self._cls(
            func=self._func, specs=self._specs, kwargs=final_kwargs, **extra_args
        )

    @overload
    def bind(self, /, *_: _P.args, **overrides: _P.kwargs) -> _T: ...
    @overload
    def bind(self, /, *scopes: Mapping[str, Any], **overrides: Any) -> _T: ...
    def bind(self, /, *scopes: Mapping[str, Any], **overrides: Any) -> _T:
        """
        Create the node instance by binding parameters from scopes and overrides.
        """
        # 1. Start with explicit overrides
        final_kwargs = dict(overrides)

        # 2. Iterate through required parameters defined in the specs
        for name in self._specs.keys():
            if name in final_kwargs:
                continue
            # Look in scopes (reverse order)
            for scope in reversed(scopes):
                if name in scope:
                    final_kwargs[name] = scope[name]
                    break

        return self(**final_kwargs)


def _node(func: NodeFunc[_P], /) -> FunctionalFactory[NodeDef, _P]:
    analysis = _analyze_signature(func)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@node '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return FunctionalFactory(NodeDef, func, analysis)


@overload
def node(
    func: _Missing = _MISSING, /
) -> Callable[[NodeFunc[_P]], FunctionalFactory[NodeDef, _P]]: ...
@overload
def node(func: NodeFunc[_P], /) -> FunctionalFactory[NodeDef, _P]: ...
def node(
    func: Union[NodeFunc[_P], _Missing] = _MISSING, /
) -> Union[
    Callable[[NodeFunc[_P]], FunctionalFactory[NodeDef, _P]],
    FunctionalFactory[NodeDef, _P],
]:
    if func is _MISSING:
        return partial(_node)
    else:
        return _node(func)


def _expression(
    func: ExpressionFunc[_P, _R], /
) -> FunctionalFactory[NodeExpressionDef[_R], _P]:
    analysis = _analyze_signature(func)
    if len(analysis.pos_only_params) != 1:
        raise TypeError(
            f"@expression '{func.__name__}' requires exactly 1 positional-only argument (ctx), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return FunctionalFactory(NodeExpressionDef, func, analysis)


@overload
def expression(
    func: _Missing = _MISSING, /
) -> Callable[[ExpressionFunc[_P, _R]], FunctionalFactory[NodeExpressionDef[_R], _P]]: ...
@overload
def expression(
    func: ExpressionFunc[_P, _R], /
) -> FunctionalFactory[NodeExpressionDef[_R], _P]: ...
def expression(
    func: Union[ExpressionFunc[_P, _R], _Missing] = _MISSING, /
) -> Union[
    Callable[[ExpressionFunc[_P, _R]], FunctionalFactory[NodeExpressionDef[_R], _P]],
    FunctionalFactory[NodeExpressionDef[_R], _P],
]:
    if func is _MISSING:
        return partial(_expression)
    else:
        return _expression(func)


def _wrapper(func: WrapperFunc[_P], /) -> FunctionalFactory[NodeWrapperDef, _P]:
    analysis = _analyze_signature(func)
    # Wrapper signature: (ctx, wrapped, call_next, **kwargs)
    # The first 3 arguments should be positional-only.
    if len(analysis.pos_only_params) != 3:
        raise TypeError(
            f"@wrapper '{func.__name__}' requires exactly 3 positional-only arguments (ctx, wrapped, call_next), "
            f"but found {len(analysis.pos_only_params)}."
        )
    return FunctionalFactory(NodeWrapperDef, func, analysis)


@overload
def wrapper(
    func: _Missing = _MISSING, /
) -> Callable[[WrapperFunc[_P]], FunctionalFactory[NodeWrapperDef, _P]]: ...
@overload
def wrapper(func: WrapperFunc[_P], /) -> FunctionalFactory[NodeWrapperDef, _P]: ...
def wrapper(func: Union[WrapperFunc[_P], _Missing] = _MISSING, /) -> Union[
    Callable[[WrapperFunc[_P]], FunctionalFactory[NodeWrapperDef, _P]],
    FunctionalFactory[NodeWrapperDef, _P],
]:
    if func is _MISSING:
        return partial(_wrapper)
    else:
        return _wrapper(func)


from .render import get_render_string
