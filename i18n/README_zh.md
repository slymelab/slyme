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

`Auto(tree)` 显式标记需要求值的参数绑定，在每次调用时求值已注册叶子并重建 Tree 容器，包括只有普通值的容器。普通叶子和 evaluator 返回值保持原对象 identity；非 Auto 参数直接传递。`eval_tree(ctx, tree)` 提供相同的求值行为。

Tree handler 和 Auto evaluator 按精确类型匹配，子类需要显式注册。遍历工具位于 `slyme.utils.tree`。

Node 和 Wrapper 工厂只保存传入的关键字绑定。函数原生默认值在实际调用时生效；`node(ctx, **kwargs)` 在 Auto 求值前为本次调用覆盖已保存的绑定。使用 `get`、`set`、`delete` 管理绑定。

## 安装

Slyme 需要 **Python 3.10+**。您可以通过 pip 直接安装：

```bash
pip install slyme
```

*（注意：Slyme 非常轻量，其唯一的主要依赖是 `typing_extensions`。）*

## 快速开始

以下示例使用 `@node`、`@wrapper` 和组装图的普通函数构建简单的执行流水线：

```python
from time import time
from collections.abc import Callable
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
def llm_api(ctx: Context, /, *, prompts: list[str], responses: Ref[list[str]]):
    responses_ = [f"Response to the prompt: {prompt}" for prompt in prompts]
    ctx.set(responses, responses_)


# 2. 定义产生值的节点
@node
def format_prompts(ctx: Context, /, *, articles: list[dict]) -> list[str]:
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
def build_pipeline() -> Node[None]:
    return llm_api(
        responses=R.resolve("output.responses"),
        prompts=Auto(format_prompts(
            articles=Auto(R.resolve("input.articles")),
        )),
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

类型化 API 通过类型注解约束参数和回调类型，包括同步／异步回调及 `Literal` 选项；请使用静态类型检查器检查这些调用。Slyme 在运行时检查动态 Schema 声明、结构冲突和生命周期约束。可复用的 Node 图通过普通 Python 函数组装。

执行或清理可能异步时，从 `slyme.utils.execution` 导入 `await_result`，使用 `await await_result(task(ctx))` 和 `await await_result(ctx.dispose())`。需要在同一个函数中组合两种模式时，使用同模块的 `@continuation` 并 yield 这些调用。纯同步应用仍可直接调用 `task(ctx)` 和 `ctx.dispose()`。

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` 构建从外到内的 callable 链，不执行它。Wrapper 顺序采用快照，参数仍实时读取；每个 wrapper 控制对下一层的调用，并可返回同步或异步结果。

需要混合执行时，编写普通生成器并传给 `slyme.utils.execution` 的 `run()`。每个 `value = yield operation()` 都可以接收同步或异步结果。驱动器同步执行到第一个被 yield 的 awaitable，再返回尚未调度的异步剩余流程。循环、分支和 `try/except/finally` 留在生成器内。返回模式不确定时，使用 `await await_result(run(generator))`。驱动器不调度 task、不汇总错误，也不拥有资源生命周期。

Auto 独立管理全部完成后汇总的求值策略，Context 独立管理递归 LIFO 清理。两者都通过异常组报告失败，不提供部分成功结果。Auto 按输入索引排列错误，Context 按清理执行顺序排列错误；正常返回的异常对象仍是普通数据。Node 和 Wrapper 会将普通异常（包括普通异常组）包装成异常记录，由 `__cause__` 保存原始异常；已有 Node 异常记录及非 `Exception` 控制异常原样传播。

`slyme.utils.exception.exception_group(message, excs)` 将非空异常序列组成异常组，同一模块导出 `ExceptionGroup` 和 `BaseExceptionGroup` 供捕获异常组。Python 3.11+ 使用原生异常组；Python 3.10 使用提供 `message` 和有序 `exceptions` 的简单兼容类，不支持 `except*`、子组操作或分组堆栈显示。仅包含 `Exception` 的组可以被 `except Exception` 捕获；包含取消或其他非 `Exception` 异常的组则不会被捕获。

## Context 生命周期、Scope 可见性与 Compose

`ctx.install("provide", provide)` 在 `$.methods.provide` 安装由生命周期管理的扩展方法。调用 `ctx.provide(...)` 会将访问方 Context 作为函数的第一个参数；`ctx.get("$.methods.provide")` 返回原始函数。方法遵循 Scope 可见性，不能替换原生成员，install 返回精确撤销本次安装的 disposer。参见[扩展方法](../docs/src/zh/guide/essentials/context.md#methods)。

使用 `schema.resolve_entry(path)` 查询单个字段，通过 `schema.entries` tuple 枚举 root、container 和 leaf entry。公开的 `RefEntry` 提供 `ref`、`config` 和 `alive`；metadata 通过 `entry.config.metadata` 读取。配置采用 `slyme.context` 导出的公开类型 `RefConfig`、`RefLeafConfig` 和 `RefContainerConfig`。

`set` 和 `delete` 修改单个本地路径。`update` 和 `drop` 会在应用修改前校验整个批次；预检失败时 binding 保持不变，实际应用修改时发生的失败不会触发回滚。

`Schema.leaf()` 默认使用 `mode="assign"`，允许 `set()`/`delete()`；声明 `mode="register"` 后，改用 `ctx.register(ref, value)` 及其 disposer 撤销。两种模式互不允许对方的写入操作。删除 container（包括 `ctx.delete("")`）时，任何 `register` 后代都会使操作在修改数据前报错。模式约束的是绑定，而不是所存对象是否可变。

Context 根持有实时 `Schema` 引用，并拥有应用数据存储与生命周期树。每个 Context 最多有一个 parent，并绑定一个不可变 `Scope`。`Context.fork()` 创建由当前 Context 管理的子 Context，默认共享当前 Scope；需要独立的局部可见身份时，应传入 `scope=ctx.scope.fork()`。`Scope.fork()` 只创建单 parent 子级，显式构造 `Scope(parents=(...))` 时则支持 C3 多继承。读取沿绑定 Scope 的 C3 顺序查找，写入绑定到该 Scope 的 leaf 局部 identity。`Compose.derive(parents=..., binding=...)` 和静态 `Compose.derive_many(parents=..., bindings=...)` 创建配置好的 Scope，后者将多个 Compose 配置在同一个 Scope 上，两者都不管理生命周期。所有 derive 绑定值接受 ScopeBinding 或 Identity，不接受 None；直接传 Identity 等价于 ScopeBinding(identity=identity)。ScopeBinding 和 Identity 默认均不阻断，阻断需显式设置 blocked=True。`ctx.derive(label=..., parents=..., bindings=...)` 则创建子 Context 和一个新 Scope，配置指定字段与 Compose，并接管数据生命周期；省略 parents 时继承当前 Scope，显式 parents 可以是一个 Scope 或 tuple，`()` 表示无父级。Scope 构造与 derive 接口均为 keyword-only，Scope 存储的 parents 始终为 tuple。`effect()` 管理同步或异步 setup 和 cleanup，`register()` 与 `declare()` 管理同步注册清理。`dispose()` 在同步完成时返回 `None`，否则返回剩余清理的 awaitable。两种情况统一使用 `await await_result(ctx.dispose())`，其中 `await_result` 从 `slyme.utils.execution` 导入。一棵 Context 树及其可变的 Schema 和 Compose 对象只归属于一个线程；异步执行时也只归属于一个事件循环，框架不会通过线程身份检查主动执行这一约束。worker 线程或进程应只接收普通输入值，并把结果返回 owner 线程后再修改 Context。注册操作会返回可用于提前移除的精确 disposer：

```python
from slyme.context import Compose, Context, Schema

class ValueLayer(dict):
    def register(self, token, /, value):
        self[token] = value

        def dispose():
            del self[token]

        return dispose

R = Schema({"hooks": Schema.leaf(mode="register")})
root = Context()
root.declare(R)
hooks = Compose(
    factory=ValueLayer,
    query=lambda layers: tuple(value for layer in layers for value in layer.values()),
)
root.register(R.resolve("hooks"), hooks)
agent = root.derive(label="agent")

root.effect(lambda: hooks.register(root.scope, "root"))
agent.effect(lambda: agent.get(R.resolve("hooks")).register(agent.scope, "agent"))
assert hooks.resolve(agent.scope) == ("agent", "root")

agent.dispose()
assert hooks.resolve(root.scope) == ("root",)
root.dispose()
```

`Compose(*, factory, query=None)` 为每个活跃 Identity 创建一个应用定义的 Layer。`ComposeLayer` 协议只要求同步的 `register(token, /, *args, **kwargs)` 方法，返回同步 disposer，不要求继承该协议。Compose 只注入 token，业务参数原样转发；由 Compose 保证每个 Layer disposer 至多执行一次，并管理登记记录和空层清理。`resolve(scope, query=None)` 使用默认或本次指定的查询函数；`layers(scope=None, local=False)` 惰性返回 Layer，不传 Scope 时返回全部活跃层。最后一次登记撤销后回收层，与层的计算结果是否为空无关。默认 Tree/Eval 层禁止在相同 Identity 内重复登记 class；覆盖继承的 handler 需要独立层。

Scope viewer、共享的 Context-binding identity 和 Schema 声明直接记录持有关系。内部登记和清理方法负责移除空的持有记录及其数据；Compose 按唯一 token 移除登记，并在最终登记撤销后删除层。只有向调用方提供显式撤销能力的操作才返回 disposer。后续登记可以复用 Scope 或 identity，但不会恢复已清除的数据。

释放开始时，会在执行任何 cleanup 前禁止整棵所属 Context 子树的修改。每个 Context 在自身释放完成前仍可读取；子树之外的 Context 即使共享 Scope，也不受影响。

## 核心优势

**原生 Python 开发体验：** 消除了繁重的面向对象样板代码。您只需掌握基本的 Python 函数和原生数据结构（字典、列表、元组）即可快速上手。

**无限可组合性：** 通过完全解耦构建任意复杂的执行流程。得益于 Tree 增强，节点 containment 关系可以直接通过原生 Python 结构表示。

**显式生命周期与可见性：** Context 提供单 parent 的生命周期归属，Scope 提供独立的 C3 可见性。每个 Context 路径、结构角色和写入模式都由共享 Schema 声明；`flatten()` 暴露可见的 Ref 到 value 映射，`Compose` 则沿 Scope 层次管理有序、可撤销的组合值。

**无缝协作：** 高度解耦的节点通过显式 Context 路径与 Compose 对象通信。这允许团队独立开发功能并编写单元测试，减少“胶水代码”和深层系统耦合。

## 文档

要深入了解 Slyme 的架构，包括 Context 管理、依赖注入和 Node 生命周期，请参阅我们的官方文档站点。

**👉 [阅读 Slyme 官方文档](https://slymelab.github.io/slyme/)**

## 许可证

本项目采用 Apache-2.0 许可证。
