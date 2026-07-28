# Annotated Downstream Example

Read this example before writing Slyme application code. Adapt domain names and module layout, but preserve the signature, state, evaluation, and lifecycle rules explained by the comments.

```python
from collections.abc import Callable, Sequence
from time import perf_counter
from typing import Optional

from slyme.builder import builder
from slyme.cli import parse_and_inject
from slyme.context import ARG, Arg, Context, R, Ref
from slyme.node import Auto, Node, expression, node, sequential, wrapper


# A Ref is a path, not the value at that path. R is the preferred RefFactory.
# Calling the final R segment attaches metadata while preserving the same path.
INPUT_ITEMS = R.input.items(
    metadata={
        ARG: Arg(
            type=list[str],
            required=True,
            help="Items to normalize",
            aliases=["-i"],
        )
    }
)
OUTPUT_ITEMS = R.output.items


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
    items_ = list(dict.fromkeys(items))
    # When a Ref and concrete value coexist, name the value with one trailing `_`.
    return ctx.set(output, items_)


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
    payload_ = {**payload, "written": True}
    return ctx.set(output, payload_)


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
def build_pipeline(*, prefix: str = "normalized:") -> Node:
    # A builder runs at build time: instantiate and compose Def objects only.
    normalize_and_store = store_items(
        items=normalize_items(items=INPUT_ITEMS, prefix=prefix),
        output=OUTPUT_ITEMS,
    ).add_wrappers(timing(label="normalize"))

    payload = {
        # All of these are leaves in one Auto PyTree.
        "items": OUTPUT_ITEMS,              # RefFactory -> Context value
        "count_source": OUTPUT_ITEMS,       # another independently resolved leaf
        "labels": ("slyme", R.config.mode), # constant + RefFactory in a tuple
        "version": 1,                       # literal remains unchanged
    }

    return sequential(
        nodes=[
            normalize_and_store,
            write_payload(payload=payload, output=R.output.payload),
        ]
    )


def run(argv: Optional[Sequence[str]] = None) -> Context:
    node_def = build_pipeline()

    # Register CLI refs explicitly. `node=node_def` can discover dependencies in
    # versions where node-tree scanning is verified, but extra_refs is portable.
    # parse_and_inject returns a new Context when `context` is provided.
    ctx = parse_and_inject(
        context=Context().set(R.config.mode, "batch"),
        extra_refs=[INPUT_ITEMS],
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

## Focused Test Shape

```python
def test_pipeline() -> None:
    original = Context().update({
        INPUT_ITEMS: [" B ", "a", "a"],
        R.config.mode: "test",
    })

    result = build_pipeline(prefix="x:").prepare()(original)

    assert result.get(OUTPUT_ITEMS) == ["x:b", "x:a"]
    assert result.get(R.output.payload)["items"] == ["x:b", "x:a"]
    assert not original.exists(OUTPUT_ITEMS)  # Copy-on-write preserved the input.
```
