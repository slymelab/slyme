# Node

`@node` 把普通 Python 函数变成可调用、可绑定参数的 Node。默认 `inject_ctx=False`；
只有函数需要执行上下文时才写 `inject_ctx=True`。无论哪一种，Node 的调用方式始终是
`task(ctx, **params)`。

```python
from slyme.context import Context, Schema, ref
from slyme.node import node


@node
def add(x: int, y: int = 2) -> int:
    return x + y


ctx = Context()
try:
    ctx.declare({"input": Schema.leaf(), "total": Schema.leaf()})
    ctx.set("input", 3)
    task = add.auto_partial(x=ref("input")).to(ref("total"))
    assert task(ctx) == 5
    assert ctx.get("total") == 5
    assert add.func(x=3) == 5
finally:
    ctx.dispose()
```

原函数接受普通参数，返回普通对象。直接使用 `def add(x, y=2)` 这样的原生签名即可，
不需要加 `*` 把业务参数声明成 keyword-only；Node 当前按关键字传入这些业务参数。`.func` 就是原函数；直接调用它会绕过参数绑定、
Auto 和结果写入。Slyme 不通过参数名、签名反射或类型注解猜测是否注入 Context。

## 显式使用 Context

资源安装、子节点编排、可见性管理和中途状态写入使用 `@node(inject_ctx=True)`：

```python
@node(inject_ctx=True)
def increment(ctx: Context, counter):
    value = ctx.get(counter) + 1
    ctx.set(counter, value)
    return value
```

`inject_ctx` 配置的是**原函数**是否接收 Context，不改变 Node 的调用接口。开启后将 Context
作为第一个位置参数传入，业务参数始终按关键字传入。Node 调用入口的 Context 是仅位置
参数，因此业务关键字也可以叫 `ctx`。框架不会隐式创建执行 Context。

推荐工厂为 `node`、`auto` 和 `ref`。`Node`、`Ref` 仍用于类型注解和直接构造；Auto 标记类型
是内部实现。通过 `field: Ref[int] = ref("count")` 提供预期类型，不使用 `ref[int](...)`。
`ref` 只描述路径，不声明或读取数据。

## 参数绑定与配置

`@node`、`@node()` 和 `node(func)` 都返回 Node；工厂支持仅关键字配置 `inject_ctx`、`params`、
`result_to`、`metadata`。直接构造 `Node` 时，`func` 也是仅关键字参数。

| 操作 | 含义 |
| --- | --- |
| `task.partial(**params)` | 合并业务参数，返回新 Node |
| `task.auto_partial(**params)` | 将每个传入值包成 `auto(value)` 后合并绑定 |
| `task(ctx, **overrides)` | 执行；当次参数覆盖已绑定参数 |
| `task.get(name, default=...)` | 读取未求值的绑定 |
| `task.replace(params=..., result_to=..., metadata=...)` | 整体替换提供的配置字段 |
| `task.to(result_to)` | `replace(result_to=result_to)` 的简写 |

replace 中省略字段表示保留，None 表示清空。函数和 Context 模式在构造后固定。
这些配置操作都不会执行函数或求值 Auto。只保存显式绑定，缺省参数沿用 Python 原生默认值。

`task.auto_partial(x=value)` 等价于 `task.partial(x=auto(value))`，每次调用才求值。
后续 partial 和调用参数会先覆盖已有绑定，因此被覆盖的来源不会执行。字符串仍是普通值；
读取 Context 数据需要显式使用 `ref("path")`。

`func`、`inject_ctx`、`params`、`result_to`、`metadata` 都是只读属性。params 和 metadata 在 Node
创建时形成浅层只读 mapping 快照。`decorate = node(params=bindings)` 暂时保留配置引用；
调用 `decorate(func)` 之前修改 bindings 会影响新 Node，之后修改则不会影响已创建的 Node。
复用同一个装饰器时，每次创建 Node 都获取当时的快照。
嵌套业务数据和 `result_to` Tree 保留原对象，不递归冻结。不要在共享执行期间修改
`result_to` Tree。Node 按身份比较；不提供参数 set/delete 接口。

metadata 是名称到 `Metadata` 的映射，不参与业务参数求值。业务参数仍可以叫 metadata，
通过 `partial(metadata=value)` 绑定即可。

## 返回值绑定

单个 Ref 保存完整返回值；Ref Tree 按对应路径选择返回值：

```python
@node
def summarize(text: str):
    return {"summary": text[:80], "length": len(text)}


whole = summarize.to(ref("result"))
selected = summarize.to({"summary": ref("answer")})
plain = selected.to(None)
```

目标路径需要事先声明，也接受字符串路径。Ref 叶子保存对应位置的完整对象，包括字典、
列表或 None。未选择的返回字段被忽略；选择的字段缺失则报错，不会静默跳过。
使用执行 Context 的数据 Tree 规则完成投影。

输出在**本层 Node 成功完成后**写入，包括等待异步结果，然后返回**同一个原始结果对象**。
原函数失败或取消不会发布本层输出；已经完成的子节点写入不会回滚。
根据目标字段已声明的 mode 分成赋值组和注册组，同一棵输出 Tree 可以混合两者。
注册归执行 Context 所有，在它释放时撤销；不会自动调用返回对象的清理方法，也不会延长
Auto 子节点的资源生命周期。如果需要提前撤销的 disposer，仍显式使用 `ctx.register`。

先检查全部选中的返回值路径和目标声明。重复 register 目标（包括字符串与 Ref 指向同一路径）
在任何写入前报错；重复 assign 目标只保留最后一个值。
输出 Tree 表达映射关系，不表达交错写入顺序：先调用 `register_many`，再调用 `set_many`，
空组跳过。注册失败会撤销本组已安装的注册，赋值组不会开始。
两组之间不提供整体事务：赋值失败不会撤销已经成功的注册或已完成的赋值。
同一 identity 中重复注册仍会冲突。需要中间写入或特定交错顺序时，显式调用 Context 操作。

遇到条件返回的不同结构，可以保存完整结果，或用一个 `inject_ctx=True` Node 通过普通分支显式写入。
中间状态、回滚信息和失败时也需要保留的数据同样使用显式 Context 操作。

## 高阶组合

Node 可以通过普通参数接收其他 Node。包裹当前节点的高阶 Node，约定用 `wrapped` 指向
紧邻的内层节点：

```python
from slyme.execution import continuation
from slyme.node import AnyNode, wrap


@node(inject_ctx=True)
@continuation
def trace(ctx: Context, wrapped: AnyNode, name: str):
    print(name, "start")
    try:
        return (yield wrapped(ctx))
    finally:
        print(name, "end")


traced = wrap(trace.partial(name="add"), task)
```

`wrap(outer, inner, task)` 等价于
`outer.partial(wrapped=inner.partial(wrapped=task))`，全部参数从外到内排列，最后一个是目标。
至少需要一个 Node；`wrap(task)` 原样返回 task。
`wrap(outer, inner, task)` 与 `wrap(outer, wrap(inner, task))` 的嵌套关系一致。
各层自行决定是否调用、调用几次、传什么 Context、返回什么结果。不再有独立 Wrapper 类或
执行协议；其他组合可以使用 `primary`、`fallback`、`children` 等明确的业务参数名。

**先绑定目标的业务参数，再组装。**组装后的调用参数和 partial 只作用于最外层，不会隐式穿透、
创建新作用域或移动输出配置。更换内部节点可以重新组装，或替换显式 wrapped 绑定。
内层 `.to(...)` 在内层完成时写入；外层 `.to(...)` 在外层完成时写入，包括短路返回值。

## 同步与异步

Node 根据函数、Auto 和清理行为返回 `R | Awaitable[R]`。同步工作立即执行；不知道是否异步时，
使用 `slyme.execution` 的 `await await_result(task(ctx))`。它不调度任务、不把阻塞工作自动放入线程。
异步应用使用普通 async/await，并在 `finally` 中通过
`await await_result(ctx.dispose())` 等待清理完成。

`@continuation` 将生成器函数变为同步／异步通用函数。`value = yield operation()` 接收立即值或
异步结果，并将失败抛回 yield；循环、分支和 try/finally 仍是普通 Python。需要 Context 的编排
在外侧加 `@node(inject_ctx=True)`。未加 continuation 的生成器仍是普通返回数据。

需要在清理前完成子调用时使用 `return (yield child(ctx))`；直接 return awaitable 会先离开生成器。
`yield child.__call__.flat_call(ctx)` 在驱动器的显式栈上执行子 Node。continuation 的 `.flat_call`
和 `.flat_start` 分别提供调用和启动语义，本身不拥有 Context 生命周期，详见[生命周期](./lifecycle.md)。

Node 原样传播异常，外层捕获 `wrapped(ctx)` 的失败时收到的是子节点的原始异常。
Auto 可能聚合多个失败，Node 原样传播这些异常组；取消异常也原样传播。

## 参数与 Auto

普通 Node 和 Ref 参数保持原对象。显式绑定 `auto(value)` 才按 Tree 规则解析已注册的求值叶子。
每个 Auto 子 Node 在独立的派生 Context 中执行，并在值交给父函数前释放它。Ref 直接读取传入的
Context。需要保留写入或资源时，显式调用 child(ctx)，不要依赖 Auto 的临时作用域。

默认参数不扫描 Auto；普通未标记参数内的 Auto 也是数据。非 Auto 容器保持身份，Auto 容器按
Tree 规则重建，但普通叶子和求值结果保持身份。每次调用都会重新求值，包括重试中的 wrapped 调用。
Auto 等待全部求值结束并聚合错误，不暴露部分成功结果。

`sequential_exec(ctx, nodes)` 在同一 Context 中依次执行并丢弃结果；可绑定版本是
`sequential.partial(nodes=stages)`。其他流程直接使用 Python 控制结构。

## 类型与迁移

`Node[P, R]` 的 P 描述**完整原函数签名**：inject_ctx=True 时包含 Context；R 为完成结果。
`AnyNode[R]` 等价于 `Node[..., R]`，只擦除参数。通常让装饰器推断即可。
`.func` 保留完整参数类型，partial/auto_partial/replace/to 也保留它。参数绑定和调用覆盖仍是动态关键字：
ParamSpec 无法表示任意关键字子集及 Auto 替代。inject_ctx 配置与函数签名是否一致由开发者明确保证，
不进行静态推断或运行时反射检查。

旧版本迁移：

- 仍接收 Context 的函数加 `inject_ctx=True`；纯业务函数移除未使用的 Context 参数。
- Wrapper 改为接收关键字 wrapped 的 `@node(inject_ctx=True)`；call_next 改为 wrapped，使用 wrap 组装。
- 先绑定业务参数再 wrap；删除 wrappers 字段、Wrapper.compose 和专用 Wrapper 异常记录用法。
- 显式 `Node[P, R]` 注解在适用时把 Context 纳入 P。
