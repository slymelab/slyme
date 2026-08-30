# Quick Start

This example builds a small pipeline in which one Node derives prompts and another stores responses in Context.

```python
from collections.abc import Callable
from time import monotonic

from slyme.builder import builder
from slyme.context import ARG, Arg, Context, R, Ref
from slyme.node import Auto, Node, node, wrapper


@node
def format_prompts(ctx, *, articles: Auto[list[dict]]) -> list[str]:
    return [
        f"Summarize {article['title']}: {article['content']}" for article in articles
    ]


@node
def call_llm(
    ctx,
    *,
    prompts: Auto[list[str]],
    output: Ref[list[str]],
) -> None:
    ctx.set(output, [f"Response: {prompt}" for prompt in prompts])


@wrapper
def timing(ctx, wrapped: Node, call_next: Callable, *, name: str):
    started = monotonic()
    try:
        return call_next(ctx)
    finally:
        print(f"{name}: {monotonic() - started:.4f}s")


@builder
def build() -> Node:
    formatter = format_prompts(
        articles=R.input.articles(metadata={ARG: Arg(type=list[dict], required=True)})
    )
    return call_llm(
        prompts=formatter,
        output=R.output.responses,
    ).add_wrappers(timing(name="llm"))


responses = build().run(
    inputs={
        R.input.articles: [{"title": "Slyme", "content": "Composable Python Nodes"}]
    },
    outputs=R.output.responses,
)
print(responses)
```

The important pieces are:

- `@node` creates both effectful and value-producing Nodes.
- `Auto` resolves the formatter Node before invoking `call_llm`.
- `@wrapper` surrounds a mounted Node with middleware behavior.
- `@builder` assembles and validates the physical Node structure.
- `run()` prepares application inputs and extracts outputs. Direct `node(ctx)` calls are also supported.

Nodes remain mutable and static parameter containers stay live during calls; there is no Def/Exec or explicit prepare phase. Call `.clone()` when a branch needs an independent Node/Wrapper and parameter-PyTree structure.
