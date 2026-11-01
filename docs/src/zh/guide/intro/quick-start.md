# 快速开始

下面构建一个小型流水线：一个 Node 生成 prompts，另一个 Node 将响应写入 Context。

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

其中：

- `@node` 同时用于有副作用和产生值的 Node。
- `Auto` 会在调用 `call_llm` 前求值 formatter Node。
- `wrap(layer, task)` 将高阶 Node 组装到目标外层。
- `build()` 是组装并返回 Node 图的普通 Python 函数。
- 应用代码显式创建 Context、提供外部输入、调用 Node 并读取输出。

Node 的参数绑定只读，但引用的参数容器仍然可以修改；不再存在 Def/Exec 或显式 prepare 阶段。需要另一张可独立配置的图时，应再次调用 组装函数。

业务函数使用默认的 `inject_ctx=False`，只有计时层需要 `inject_ctx=True`。`.to()` 写入成功结果，
并原样返回。本例计时层假设同步子调用；需要支持异步时应 await 或 yield wrapped 调用。
