# Quick Start

This example builds a small pipeline in which one Node derives prompts and another stores responses in Context.

```python
from collections.abc import Callable
from time import monotonic

from slyme.context import Context, Ref, Schema
from slyme.node import Node, node, wrapper, auto


R = Schema(
    {
        "input": {
            "articles": Schema.leaf(),
        },
        "output": {"responses": Schema.leaf()},
    }
)


@node
def format_prompts(ctx, *, articles: list[dict]) -> list[str]:
    return [
        f"Summarize {article['title']}: {article['content']}" for article in articles
    ]


@node
def call_llm(
    ctx,
    *,
    prompts: list[str],
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


def build() -> Node:
    formatter = format_prompts.partial(articles=auto(R.resolve("input.articles")))
    return call_llm.partial(
        prompts=auto(formatter),
        output=R.resolve("output.responses"),
    ).replace(wrappers=(timing.partial(name="llm"),))


ctx = Context()
ctx.declare(R)
ctx.update(
    {
        R.resolve("input.articles"): [
            {"title": "Slyme", "content": "Composable Python Nodes"}
        ]
    }
)
build()(ctx)
print(ctx.get(R.resolve("output.responses")))
```

The important pieces are:

- `@node` creates both effectful and value-producing Nodes.
- `Auto` resolves the formatter Node before invoking `call_llm`.
- `@wrapper` surrounds a mounted Node with middleware behavior.
- `build()` is an ordinary Python function that assembles and returns the Node graph.
- Application code creates a Context, supplies external inputs, calls the Node, and reads outputs explicitly.

Node bindings are read-only; referenced parameter containers can still change during calls; there is no Def/Exec or explicit prepare phase. Call the assembly function again when another independently configurable graph is required.
