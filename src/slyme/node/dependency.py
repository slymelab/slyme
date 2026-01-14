from dataclasses import dataclass, field
from typing import Any, Protocol, Union
from typing_extensions import Self
from slyme.utils.registry import Registry, TypeRegistry
from slyme.context import Context
from .base import NodeComponent, Node, NodeElement

# Registry definition
DEPENDENCY_REGISTRY: Registry[type["NodeDependencyChecker"]] = Registry(
    "node_dependency"
)


class _DependencyCheckerFunc(Protocol):
    def __call__(self, checker, node, ctx, /, **kwargs) -> Any: ...


class NodeDependencyChecker:
    """
    Base class for dependency checkers.
    """

    registry: TypeRegistry[NodeComponent, _DependencyCheckerFunc]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        # Each subclass (Strategy) gets its own isolated TypeRegistry.
        cls.registry = TypeRegistry(f"TypeRegistryOf{cls.__name__}")

    def check(
        self,
        node: NodeComponent,
        ctx: Context,
        node_cls: Union[type[NodeComponent], None] = None,
        /,
        **kwargs,
    ) -> Any:
        return self._check(node, ctx, node_cls, **kwargs)

    def _check(
        self,
        node: NodeComponent,
        ctx: Context,
        node_cls: Union[type[NodeComponent], None] = None,
        /,
        **kwargs,
    ) -> Any:
        """
        Dispatch to the specific node handler.
        Uses node_cls for lookup if provided, otherwise type(node).
        """
        return self.registry.lookup(node_cls if node_cls is not None else type(node))(
            self, node, ctx, **kwargs
        )


@dataclass
class VanillaDependencyInfo:
    """Strategy-specific data structure: Bag of Keys."""

    requires: set[str] = field(default_factory=set)
    produces: set[str] = field(default_factory=set)

    def update(self, other: "VanillaDependencyInfo") -> Self:
        self.requires.update(other.requires)
        self.produces.update(other.produces)
        return self


@dataclass
class VanillaDependencyReport:
    """Strategy-specific report."""

    missing_keys: set[str]
    context_keys: set[str]
    all_produced: set[str]
    all_required: set[str]

    @property
    def valid(self) -> bool:
        return len(self.missing_keys) == 0

    @property
    def message(self) -> str:
        if self.valid:
            return "Dependency Check Passed (Vanilla)."
        return (
            f"Dependency Check Failed (Vanilla).\n"
            f"Missing Keys: {sorted(list(self.missing_keys))}"
        )


@DEPENDENCY_REGISTRY(key="vanilla")
class VanillaDependencyChecker(NodeDependencyChecker):

    def check(
        self,
        node: NodeComponent,
        ctx: Context,
        node_cls: Union[type[NodeComponent], None] = None,
        /,
        **kwargs,
    ) -> VanillaDependencyReport:
        # 1. Collect dependencies (returns VanillaDependencyInfo)
        # Pass node_cls to the initial _check call to support explicit type dispatch
        dep_info: VanillaDependencyInfo = self._check(node, ctx, node_cls, **kwargs)

        # 2. Extract existing keys from Context
        context_data = ctx.collect_leaves()
        context_keys = set(context_data.keys())

        # 3. Calculate missing keys (Set arithmetic)
        available_keys = dep_info.produces | context_keys
        missing_keys = dep_info.requires - available_keys

        return VanillaDependencyReport(
            missing_keys=missing_keys,
            context_keys=context_keys,
            all_produced=dep_info.produces,
            all_required=dep_info.requires,
        )


def _resolve_dep_info(node: NodeElement, /):
    info = VanillaDependencyInfo()

    keys_dict = node.store_keys
    for _, key_obj in keys_dict.items():
        if isinstance(key_obj, RequiresKey):
            info.requires.add(key_obj.path)

        if isinstance(key_obj, ProducesKey):
            info.produces.add(key_obj.path)

    return info


@VanillaDependencyChecker.registry(key=NodeComponent)
def _(
    checker: VanillaDependencyChecker,
    node: NodeComponent,
    ctx: Context,
    /,
    **kwargs,
) -> VanillaDependencyInfo:
    return _resolve_dep_info(node)


@VanillaDependencyChecker.registry(key=Node)
def _(
    checker: VanillaDependencyChecker,
    node: Node,
    ctx: Context,
    /,
    **kwargs,
) -> VanillaDependencyInfo:
    # FIXME: Node Search
    info: VanillaDependencyInfo = checker._check(node, ctx, NodeComponent, **kwargs)
    for wrapper in node.node_wrappers:
        info.update(_resolve_dep_info(wrapper))
    return info


@VanillaDependencyChecker.registry(key=NodeList)
def _(
    checker: VanillaDependencyChecker,
    node: NodeList,
    ctx: Context,
    /,
    **kwargs,
) -> VanillaDependencyInfo:
    # 1. Base info
    # FIXME: Node Search
    info: VanillaDependencyInfo = checker._check(node, ctx, Node, **kwargs)
    # 2. Merge children info
    for child in node:
        # Normal recursion uses child's own type
        info.update(checker._check(child, ctx, **kwargs))
    return info
