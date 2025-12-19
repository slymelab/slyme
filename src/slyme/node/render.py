import sys
from dataclasses import dataclass
from slyme.utils.typing import Any, Protocol, Iterator, Union, TextIO, Literal
from slyme.utils.registry import Registry, TypeRegistry
from slyme.utils.inspect import resolve_name
from . import NodeComponent, NodeContainer

RENDER_REGISTRY: Registry[type["NodeRender"]] = Registry("node_render")


@dataclass
class RenderInfo:
    classname: str
    attr_dict: dict[str, Any]


class _RenderFunc(Protocol):
    def __call__(self, render, node, /, **kwargs) -> Any: ...


class NodeRender:
    registry: TypeRegistry[NodeComponent, _RenderFunc]

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        cls.registry = TypeRegistry(f"TypeRegistryOf{resolve_name(cls)}")

    def render(
        self, node, node_cls: Union[type[NodeComponent], None] = None, /, **kwargs
    ) -> Any:
        return self._render(node, node_cls, **kwargs)

    def _render(
        self, node, node_cls: Union[type[NodeComponent], None] = None, /, **kwargs
    ) -> Any:
        """Can specify a different node_cls (usually a super class of node) to lookup"""
        return self.registry.lookup(node_cls if node_cls is not None else type(node))(
            self, node, **kwargs
        )


# Vanilla Render (default implementation)
@dataclass
class VanillaStyle:
    vertical: str
    branch: str
    corner: str
    space: str


VANILLA_STYLE_UNICODE = VanillaStyle(
    vertical="│   ",
    branch="├── ",
    corner="└── ",
    space="    ",
)


@RENDER_REGISTRY(key="vanilla")
class VanillaRender(NodeRender):
    def render(
        self,
        node,
        node_cls: Union[type[NodeComponent], None] = None,
        /,
        mode: Literal["print", "str", "iter"] = "print",
        file: TextIO = sys.stdout,
        prefix: str = "",
        style: VanillaStyle = VANILLA_STYLE_UNICODE,
    ) -> Union[None, str, Iterator[str]]:
        """"""
        iterator = self._render(node, node_cls, prefix=prefix, style=style)

        if mode == "iter":
            return iterator
        elif mode == "str":
            return "\n".join(iterator)
        elif mode == "print":
            for line in iterator:
                print(line, file=file)
        else:
            raise NotImplementedError(f"Unknown render mode: {mode}")


@VanillaRender.registry(key=NodeComponent)
def _(
    render: VanillaRender,
    node: NodeComponent,
    /,
    *,
    prefix: str = "",
    is_last: bool = True,
    is_root: bool = True,
    style: VanillaStyle,
    **kwargs,  # NOTE: For forward compatibility
) -> Iterator[str]:
    info = node._get_render_info()

    if is_root:
        connector = ""
    else:
        connector = style.corner if is_last else style.branch

    if info.attr_dict:
        attr_str = ", ".join(f"{k}={v}" for k, v in info.attr_dict.items())
    else:
        attr_str = ""

    yield f"{prefix}{connector}{info.classname}({attr_str})"


@VanillaRender.registry(key=NodeContainer)
def _(
    render: VanillaRender,
    node: NodeContainer,
    /,
    *,
    prefix: str = "",
    is_last: bool = True,
    is_root: bool = True,
    style: VanillaStyle,
    **kwargs,  # NOTE: For forward compatibility
) -> Iterator[str]:
    # FIXME: find a super class of NodeContainer, rather than directly choose NodeComponent
    yield from render._render(
        node,
        NodeComponent,
        prefix=prefix,
        is_last=is_last,
        is_root=is_root,
        style=style,
    )

    if is_root:
        child_prefix = prefix
    else:
        child_prefix = prefix + (style.space if is_last else style.vertical)

    count = len(node)
    for i, child in enumerate(node):
        is_last_child = i == count - 1

        yield from render._render(
            child,
            prefix=child_prefix,
            is_last=is_last_child,
            is_root=False,
            style=style,
        )
