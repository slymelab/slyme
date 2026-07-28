# Annotated Downstream Example

Read this example before writing Slyme application code. Adapt domain names and module layout, but preserve the signature, state, evaluation, and lifecycle rules explained by the comments.

```python
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import NamedTuple, Optional

from slyme.builder import builder
from slyme.cli import parse_and_inject
from slyme.context import ARG, Arg, Context, R, Ref
from slyme.node import Auto, Node, expression, node, sequential, wrapper


class PipelineRefs(NamedTuple):
    input_items: Ref[list[str]]
    output_items: Ref[list[str]]
    output_payload: Ref[dict[str, object]]
    mode: Ref[str]


def create_pipeline_refs() -> PipelineRefs:
    # Create concrete Refs at the assembly boundary, not as module globals.
    # Calling an R path produces a Ref and can attach metadata.
    return PipelineRefs(
        input_items=R.input.items(
            metadata={
                ARG: Arg(
                    type=list[str],
                    required=True,
                    help="Items to normalize",
                    aliases=["-i"],
                )
            }
        ),
        output_items=R.output.items(),
        output_payload=R.output.payload(),
        mode=R.config.mode(),
    )


@expression
def normalize_items(
    ctx: Context,  # Runtime-supplied parameter.
    /,             # @expression requires exactly one positional-only parameter.
    *,
    items: Auto[list[str]],  # Every build-time parameter is keyword-only.
    prefix: str = "",        # A plain value is static build-time configuration.
) -> list[str]:
    # `items` is already a concrete list. Auto resolved its bound Ref/expression
    # against the incoming ctx before this body began; do not call ctx.get(items).
    return [f"{prefix}{item.strip().lower()}" for item in items]


@node
def store_items(
    ctx: Context,
    /,
    *,
    items: Auto[list[str]],
    output: Ref[list[str]],
) -> Context:
    # `items` is a value because it is Auto; `output` remains a Ref because the
    # node decides where to write. Context is immutable, so return the new one.
    output_ = list(dict.fromkeys(items))
    # `output` points to the same Context path whose concrete value is `output_`.
    return ctx.set(output, output_)


@node
def write_payload(
    ctx: Context,
    /,
    *,
    payload: Auto[dict[str, object]],
    output: Ref[dict[str, object]],
) -> Context:
    # Auto supports PyTrees: Ref/R and expression leaves are evaluated deeply;
    # constants stay unchanged; dict/list/tuple structure is reconstructed.
    output_ = {**payload, "written": True}
    return ctx.set(output, output_)


@wrapper
def timing(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], Context],
    /,  # @wrapper requires these three positional-only parameters in this order.
    *,
    label: str,
) -> Context:
    started = perf_counter()
    try:
        # Continue the onion chain and retain the Context it returns.
        ctx = call_next(ctx)
        return ctx
    finally:
        print(f"{label} ({type(wrapped).__name__}): {perf_counter() - started:.3f}s")


@builder
def build_pipeline(*, refs: PipelineRefs, prefix: str = "normalized:") -> Node:
    # A builder runs at build time: instantiate and compose Def objects only.
    normalize_and_store = store_items(
        items=normalize_items(items=refs.input_items, prefix=prefix),
        output=refs.output_items,
    ).add_wrappers(timing(label="normalize"))

    payload = {
        # All of these are leaves in one Auto PyTree.
        "items": refs.output_items,        # Ref -> Context value
        "labels": ("slyme", refs.mode),    # constant + Ref in a tuple
        "version": 1,                      # literal remains unchanged
    }

    return sequential(
        nodes=[
            normalize_and_store,
            write_payload(payload=payload, output=refs.output_payload),
        ]
    )


def run(argv: Optional[Sequence[str]] = None) -> Context:
    refs = create_pipeline_refs()
    node_def = build_pipeline(refs=refs)

    # Register CLI refs explicitly. `node=node_def` can discover dependencies in
    # versions where node-tree scanning is verified, but extra_refs is portable.
    # parse_and_inject returns a new Context when `context` is provided.
    ctx = parse_and_inject(
        context=Context().set(refs.mode, "batch"),
        extra_refs=[refs.input_items],
        cli_args=list(argv) if argv is not None else None,
    )

    # Prepare once after all build-time changes. Reuse the immutable Exec.
    node_exec = node_def.prepare()
    return node_exec(ctx)
```

## Auto Timing in Higher-Order Nodes

Use a `Ref`, not `Auto`, when a value must be reread after a child changes the context:

```python
@node
def repeat(
    ctx: Context,
    /,
    *,
    steps: Sequence[Node],
    latest: Ref[float],
) -> Context:
    for step in steps:
        ctx = step(ctx)
        latest_ = ctx.get(latest)  # Reads from the new Context on every iteration.
        print(latest_)
    return ctx
```

If `latest` were `Auto[float]`, Slyme would resolve it once from the context entering `repeat`; it would not change inside the loop.

Prefer a sequence-shaped extension point when no per-child inspection is needed:

```python
from slyme.node import sequential_exec


@node
def run_stages(
    ctx: Context,
    /,
    *,
    stages: Sequence[Node],
) -> Context:
    # Callers may provide any reusable zero/one/many-stage composition.
    return sequential_exec(ctx, stages)
```

## Focused Test Shape

```python
def test_pipeline() -> None:
    refs = create_pipeline_refs()
    original = Context().update({
        refs.input_items: [" B ", "a", "a"],
        refs.mode: "test",
    })

    result = build_pipeline(refs=refs, prefix="x:").prepare()(original)

    assert result.get(refs.output_items) == ["x:b", "x:a"]
    assert result.get(refs.output_payload)["items"] == ["x:b", "x:a"]
    assert not original.exists(refs.output_items)  # Copy-on-write preserved input.
```
