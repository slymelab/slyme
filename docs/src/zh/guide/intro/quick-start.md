# 快速开始

下面构建一个小型流水线：一个 Node 生成 prompts，另一个 Node 将响应写入 Context。

```python
from collections.abc import Callable
from time import monotonic

from slyme.builder import builder
from slyme.context import ARG, Arg, Context, Ref, Schema
from slyme.node import Auto, Node, node, wrapper


R = Schema(
    {
        "input": {
            "articles": Ref(metadata={ARG: Arg(type=list[dict], required=True)}),
        },
        "output": {"responses": ...},
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
    formatter = format_prompts(articles=R.input.articles)
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

其中：

- `@node` 同时用于有副作用和产生值的 Node。
- `Auto` 会在调用 `call_llm` 前求值 formatter Node。
- `@wrapper` 为挂载的 Node 添加中间件行为。
- `@builder` 要求最外层结果是 `Node` 或 `AsyncNode`：`None` 会触发表示遗漏返回值的 `ValueError`，其他错误根对象会触发 `TypeError`，参数图则不会被递归校验。
- `run()` 准备应用输入并提取输出，也可以直接调用 `node(ctx)`。

Node 始终可变，调用期间静态参数容器也保持实时状态；不再存在 Def/Exec 或显式 prepare 阶段。需要另一张可独立配置的图时，应再次调用 Builder。
