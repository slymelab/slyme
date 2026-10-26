# 快速开始

下面构建一个小型流水线：一个 Node 生成 prompts，另一个 Node 将响应写入 Context。

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

其中：

- `@node` 同时用于有副作用和产生值的 Node。
- `Auto` 会在调用 `call_llm` 前求值 formatter Node。
- `@wrapper` 为挂载的 Node 添加中间件行为。
- `build()` 是组装并返回 Node 图的普通 Python 函数。
- 应用代码显式创建 Context、提供外部输入、调用 Node 并读取输出。

Node 的参数绑定只读，但引用的参数容器仍然可以修改；不再存在 Def/Exec 或显式 prepare 阶段。需要另一张可独立配置的图时，应再次调用 组装函数。
