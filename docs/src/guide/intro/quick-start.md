# Quick Start

This example builds a small pipeline in which one Node derives prompts and another stores responses in Context.

```python
from collections.abc import Callable
from time import monotonic

from slyme.builder import builder
from slyme.context import Context, Ref, Schema, ref
from slyme.node import Auto, Node, node, wrapper


R = Schema(
    {
        "input": {
            "articles": ref(),
        },
        "output": {"responses": ref()},
    }
)


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
    formatter = format_prompts(articles=R.resolve("input.articles"))
    return call_llm(
        prompts=formatter,
        output=R.resolve("output.responses"),
    ).add_wrappers(timing(name="llm"))


ctx = Context(
    {
        R.resolve("input.articles"): [
            {"title": "Slyme", "content": "Composable Python Nodes"}
        ]
    },
    schema=R,
)
build()(ctx)
print(ctx.get(R.resolve("output.responses")))
```

The important pieces are:

- `@node` creates both effectful and value-producing Nodes.
- `Auto` resolves the formatter Node before invoking `call_llm`.
- `@wrapper` surrounds a mounted Node with middleware behavior.
- `@builder` requires the outer result to be a `Node` or `AsyncNode`: `None` raises a missing-return `ValueError`, any other wrong root raises `TypeError`, and the parameter graph is not recursively validated.
- Application code creates a Context, supplies external inputs, calls the Node, and reads outputs explicitly.

Nodes remain mutable and static parameter containers stay live during calls; there is no Def/Exec or explicit prepare phase. Call the Builder again when another independently configurable graph is required.
