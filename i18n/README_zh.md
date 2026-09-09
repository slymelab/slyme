<div align="center">
  <img src="https://raw.githubusercontent.com/slymelab/slyme/main/assets/images/logo.jpg" alt="Slyme Logo" width="200" style="max-width: 50%;" />

  <p><em>SLYME Lets You Mold Everything.</em></p>

  <p>
    <a href="https://pypi.org/project/slyme/"><img src="https://img.shields.io/pypi/v/slyme.svg?label=PyPI" alt="PyPI version"></a>
    <img src="https://img.shields.io/badge/python-3.10%2B-blue" alt="Python version" />
    <a href="https://slymelab.github.io/slyme/"><img src="https://img.shields.io/badge/docs-latest-blue.svg" alt="Documentation"></a>
    <a href="https://github.com/slymelab/slyme/blob/main/LICENSE"><img src="https://img.shields.io/github/license/slymelab/slyme" alt="License"></a>
  </p>

  <p>
    <a href="https://github.com/slymelab/slyme/blob/main/README.md">English</a> |
    <b>简体中文</b>
  </p>
</div>

## 关于 Slyme

Slyme（发音为 /slaɪm/）是一个高度可组合的函数式执行框架。它使开发者能够基于简单、可复用的函数无缝构建任意复杂的执行流程，无需掌握繁琐的 API 或语法。

无论是构建复杂的 LLM 流水线、执行 DAG，还是创建通用的数据处理流程，Slyme 都提供了结构化、函数式且深度符合 Python 习惯的基础。

## 安装

Slyme 需要 **Python 3.10+**。您可以通过 pip 直接安装：

```bash
pip install slyme
```

*（注意：Slyme 非常轻量，其唯一的主要依赖是 `typing_extensions`。）*

## 快速开始

以下示例展示了如何使用 Slyme 的核心原语（`@node`、`@wrapper` 和 `@builder`）构建简单的执行流水线：

```python
from time import time
from collections.abc import Callable
from slyme.builder import builder
from slyme.context import Context, Ref, Schema
from slyme.node import node, wrapper, Auto, Node


R = Schema(
    {
        "input": {
            "articles": Schema.leaf(list),
        },
        "output": {"responses": Schema.leaf()},
    }
)


# 1. 定义执行节点
@node
def llm_api(ctx: Context, /, *, prompts: Auto[list[str]], responses: Ref[list[str]]):
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    ctx.set(responses, responses_)


# 2. 定义产生值的节点
@node
def format_prompts(ctx: Context, /, *, articles: Auto[list[dict]]) -> list[str]:
    return [
        f"Summarize: {article['title']}. Content: {article['content']}"
        for article in articles
    ]


# 3. 定义中间件包装器（例如性能计时）
@wrapper
def timing(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], object],
    /,
    *,
    prefix: str,
):
    start_time = time()
    result = call_next(ctx)
    end_time = time()
    print(f"[{prefix}] Finished successfully in {end_time - start_time:.4f} seconds.")
    return result


# 4. 在构建时组装流水线
@builder
def build_pipeline():
    return llm_api(
        responses=R.resolve("output.responses"),
        prompts=format_prompts(
            articles=R.resolve("input.articles"),
        ),
    ).add_wrappers(timing(prefix="LLM API Call"))


# 5. 在运行时执行
if __name__ == "__main__":
    ctx = Context(
        {
            R.resolve("input.articles"): [
                {"title": "Article 1", "content": "Content 1"},
                {"title": "Article 2", "content": "Content 2"},
            ]
        },
        schema=R,
    )
    build_pipeline()(ctx)
    print(ctx.get(R.resolve("output.responses")))
```

## Context 生命周期、Scope 可见性与 Compose

Context 根持有实时 `Schema` 引用，并拥有应用数据存储与生命周期树。每个 Context 最多有一个 parent，并绑定一个不可变 `Scope`。`Context.fork()` 创建由当前 Context 管理的子 Context，默认共享当前 Scope；需要独立的局部可见层时，应传入 `scope=ctx.scope.fork()`。读取沿绑定 Scope 的 C3 顺序查找，写入只针对该 Scope。`Compose` 同样按 Scope 保存有序值。`effect()`、`add()`、`declare()` 和 `contribute()` 会把同步 cleanup 交给调用它们的 Context 管理，`async_effect()` 则注册异步 cleanup。完全同步的树使用 `dispose()`，可能存在异步 cleanup 时使用 `await async_dispose()`。一棵 Context 树及其可变的 Schema 和 Compose 对象只归属于一个线程；异步执行时也只归属于一个事件循环，框架不会通过线程身份检查主动执行这一约束。worker 线程或进程应只接收普通输入值，并把结果返回 owner 线程后再修改 Context。注册操作会返回可用于提前移除的精确 disposer：

```python
from slyme.context import Compose, Context, Schema

R = Schema({"hooks": Schema.leaf(replaceable=False)})
root = Context(schema=R)
hooks = Compose[str, tuple[str, ...]].collect()
root.add(R.resolve("hooks"), hooks)
agent = root.fork(scope=root.scope.fork(name="agent"))

root.contribute(R.resolve("hooks"), "root")
agent.contribute(R.resolve("hooks"), "agent")
assert hooks.resolve(agent.scope) == ("agent", "root")

agent.dispose()
assert hooks.resolve(root.scope) == ("root",)
root.dispose()
```

## 核心优势

**原生 Python 开发体验：** 消除了繁重的面向对象样板代码。您只需掌握基本的 Python 函数和原生数据结构（字典、列表、元组）即可快速上手。

**无限可组合性：** 通过完全解耦构建任意复杂的执行流程。得益于 PyTree 增强，节点 containment 关系可以直接通过原生 Python 结构表示。

**显式生命周期与可见性：** Context 提供单 parent 的生命周期归属，Scope 提供独立的 C3 可见性。每个 Context 路径、结构角色和替换策略都由共享 Schema 声明；`flatten()` 暴露可见的 Ref 到 value 映射，`Compose` 则沿 Scope 层次管理有序、可撤销的组合值。

**无缝协作：** 高度解耦的节点通过显式 Context 路径与 Compose 对象通信。这允许团队独立开发功能并编写单元测试，减少“胶水代码”和深层系统耦合。

## 文档

要深入了解 Slyme 的架构，包括 Context 管理、依赖注入和 Node 生命周期，请参阅我们的官方文档站点。

**👉 [阅读 Slyme 官方文档](https://slymelab.github.io/slyme/)**

## 许可证

本项目采用 Apache-2.0 许可证。
