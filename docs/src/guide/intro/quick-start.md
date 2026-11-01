# Quick Start

This example builds a small pipeline in which one Node derives prompts and another stores responses in Context.

```python
from time import monotonic

from slyme.context import Context, Schema, ref
from slyme.node import AnyNode, auto, node, wrap


@node
def format_prompts(articles: list[dict]) -> list[str]:
    return [f"Summarize {item['title']}: {item['content']}" for item in articles]


@node
def call_llm(prompts: list[str]) -> list[str]:
    return [f"Response: {prompt}" for prompt in prompts]


@node(inject_ctx=True)
def timing(ctx: Context, wrapped: AnyNode[list[str]], name: str):
    started = monotonic()
    try:
        return wrapped(ctx)
    finally:
        print(f"{name}: {monotonic() - started:.4f}s")


def build() -> AnyNode[list[str]]:
    formatter = format_prompts.partial(articles=auto(ref("input.articles")))
    task = call_llm.partial(prompts=auto(formatter)).to(ref("output.responses"))
    return wrap(timing.partial(name="llm"), task)


ctx = Context()
try:
    ctx.declare(
        {
            "input": {"articles": Schema.leaf()},
            "output": {"responses": Schema.leaf()},
        }
    )
    ctx.set(
        "input.articles", [{"title": "Slyme", "content": "Composable Python Nodes"}]
    )
    responses = build()(ctx)
    assert ctx.get("output.responses") is responses
    print(responses)
finally:
    ctx.dispose()
```

The important pieces are:

- `@node` creates both effectful and value-producing Nodes.
- `Auto` resolves the formatter Node before invoking `call_llm`.
- `wrap(layer, task)` binds a higher-order Node around a target.
- `build()` is an ordinary Python function that assembles and returns the Node graph.
- Application code creates a Context, supplies external inputs, calls the Node, and reads outputs explicitly.

Node bindings are read-only; referenced parameter containers can still change during calls; there is no Def/Exec or explicit prepare phase. Call the assembly function again when another independently configurable graph is required.

The business functions use the default `inject_ctx=False`; only timing needs `inject_ctx=True`.
`.to()` publishes the successful result without changing it. This timing example
assumes synchronous children; for mixed execution, await or yield the wrapped call.
