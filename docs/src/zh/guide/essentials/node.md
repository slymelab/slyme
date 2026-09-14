# Node

`@node` 将函数转换为可变、可直接调用的 `Node` 对象工厂。Node 可以修改 `Context`、执行副作用、协调子 Node，也可以返回供 `Auto` 注入的派生值。

## 定义与创建 Node

Node 函数必须恰好有一个非 keyword-only 运行时参数，所有构建参数必须是 keyword-only：

```python
from slyme.context import Context, Ref, Schema
from slyme.node import Auto, node

R = Schema({"input": {"x": Schema.leaf()}, "output": {"total": Schema.leaf()}})


@node
def add(ctx: Context, *, x: Auto[int], y: Auto[int], output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add(x=R.resolve("input.x"), y=2, output=R.resolve("output.total"))
```

运行时参数的名称与类型标注都不是必需的；Slyme 仅根据参数数量及 keyword-only 位置区分运行时参数和构建参数。
不支持变长参数 `*args` 和 `**kwargs`：运行时参数数量与构建参数名称都必须显式声明。

## 执行

自行管理 Context 时直接调用 Node：

```python
ctx = Context(schema=R)
ctx.set(R.resolve("input.x"), 3)
result = task(ctx)  # 5
```

Context 创建、外部输入处理和输出提取均由调用方负责。Node 核心执行既不会推导 Schema，也不会隐式创建 Context。

现在没有 Def/Exec 转换和 `prepare()` 阶段。每次调用都会绑定并解析当前参数与 wrapper。

## 同步与异步组合

Node 的返回类型是 `T | Awaitable[T]`，取决于实际执行的 Auto、Wrapper、用户函数及临时 Context 清理。纯同步调用直接返回；普通 `def` 也可以返回 awaitable，不需要声明执行模式。同步父函数可以等待异步 Auto 注入后再运行。

```python
async def execute(ctx):
    try:
        return await task.acall(ctx)
    finally:
        await ctx.adispose()
```

`task.acall(ctx)` 和 `ctx.adispose()` 始终返回 awaitable。它们通过 `slyme.utils.continuation` 的 `await_result()` 委托给普通调用与释放方法；同步工作和同步错误仍在调用时立即发生。它们不会创建 task 或调度异步工作。

`await_result()` 只等待外层执行结果，不递归等待容器中的数据。异步 continuation 在被等待或调度前不会执行；同步前缀可能已运行。同步应用可以在应用入口使用 `asyncio.run(await_result(task(ctx)))`，已有事件循环内应 await，不创建嵌套事件循环。框架不会把阻塞函数自动放入线程。

用户函数内部的调用仍需显式处理返回值：同步代码不能把未知的 `child(ctx)` 结果直接用于计算。可以使用 `async def` 和 `await child.acall(ctx)`，或驱动下面的生成器。直接返回的 awaitable 表示执行；若要将其作为数据传递，应装入普通容器。

### 生成器组合

`slyme.utils.continuation` 的 `run(generator)` 驱动普通生成器控制流。yield 普通值时立即将值送回；yield awaitable 时返回尚未调度的异步剩余流程。等待产生的异常会在暂停的 `yield` 位置抛回，因此循环、分支和 `try/except/finally` 只需写一份：

```python
from slyme.node import Node
from slyme.utils.continuation import run


@node
def increment_child(ctx, *, child: Node[int]):
    def execute():
        value = yield child(ctx)
        return value + 1

    return run(execute())
```

创建生成器不会执行函数体。调用 `run()` 立即执行同步前缀，同步错误也立即抛出。通过 `await await_result(run(execute()))` 等待返回的剩余流程，继续异步工作。只在同步应用入口使用 `asyncio.run(await_result(run(execute())))`。驱动器不启动事件循环，也不调度 task。

只检查显式交给 `yield` 的值；容器和裸生成器仍是普通数据。每次 yield 只等待外层结果，生成器最终 return 的值不隐式等待。需要将操作完成纳入生成器的异常处理时，使用 `return (yield operation())`。通过 `yield from` 委托另一生成器，或显式 yield 它的 `run()` 结果。Node 不会自动驱动作为返回值的生成器。

将生成器的独占推进权交给 `run()`；耗尽和重入遵循 Python 的生成器协议。驱动器不缓存结果、不屏蔽取消、不汇总异常，也不拥有资源。取消会在暂停的 yield 位置抛回，遵循生成器的异常处理逻辑。等待剩余流程时，`finally` 可以 yield 异步清理；但丢弃剩余流程不会完成这些清理。

执行顺序、并发和结果收集由调用方决定。逐项 yield 调用的循环会等待当前项完成后再继续。并发实现可以先调用各项、保存返回的 awaitable，再 yield 一个异步聚合操作。若调用方可能还没有运行中的事件循环，应在该异步操作内部创建 `gather()` 或 Task。枚举或调用期间的同步异常遵循调用方的 `try/except/finally`，驱动器不额外定义 batch 策略。

Auto 独立拥有全部完成后汇总的求值策略。它先内联调用每个 evaluator 组和子节点，再等待异步结果；只有异步结果会调度为 Task。单项失败不会取消 sibling。错误通过 `slyme.utils.exception` 的嵌套 `BatchError` 报告，其按输入排序的 `Result(value=..., error=...)` 分别保存成功返回值与抛出的异常。取消批次遵循 asyncio 的传播规则，不再汇总部分结果；求值退出前会完成所拥有的子 Context 的清理。Context 独立拥有递归 LIFO 清理策略；两个消费者共用生成器驱动器，不共用执行策略 API。

## 参数与 Auto

每个构建参数都有一个 `Spec`。`Auto[T]` 是启用自动求值的简写：

```python
@node
def parent(ctx, *, child: Auto[int]):
    return child + 1


@node
def child(ctx, *, value: int):
    return value


root = parent(child=child(value=4))
assert root(Context()) == 5
```

`Auto` 会递归解析已注册的 evaluator 叶子，例如 `Ref` 和 `Node`。Ref 会直接读取传入的 Context；每个产生值的子 Node 则获得由父 Context 管理、绑定到独立 `ctx.scope.fork()` 的子 Context。父 Node 继续执行前，Slyme 会 dispose 该 Context 及其 effect，因此 child Scope 的写入与由 Context 管理的注册不会泄漏到父级或其他 Auto 子 Node。返回值、对共享 leaf 对象的修改、disposer 未交给子 Context 管理的直接注册，以及没有注册 cleanup 的外部副作用并不会被隔离。没有 `Auto` 时，Ref 和 Node 对象会原样传入。

缺失的必需构建参数以 `UNDEFINED` 表示，并在 Node 调用时被拒绝。使用 `UNSET` 可以显式请求声明的默认值。

## 动态修改

Node 和 Wrapper 参数通过显式参数 API 在两次调用之间保持可变：

```python
root.get("child").set("value", 10)
assert root(Context()) == 11
```

使用 `get(name)` 读取参数，使用 `set(name, value)` 替换参数，使用 `reset(name)` 恢复声明的默认值或 `UNDEFINED`。只读 `params` mapping 暴露全部当前参数。参数名可以与 `func`、`get`、`wrappers` 等框架 API 重合，因为参数不会投影为对象属性。

调用时，非 Auto 参数会直接传给用户函数，因此对其容器的修改会更新 Node 或 Wrapper 上的实时参数。每个 Auto 参数都会按 Tree 规则遍历并重建容器，包括只有普通值的子树。普通叶子与 evaluator 返回值保持原对象 identity；这不是深拷贝。

需要另一张可独立配置的图时，应重新调用 Node factory 或 组装函数。可变应用值是否共享或复制由其自身语义决定；Slyme 不会猜测哪些引用需要复制。

## Wrapper

`@wrapper` 函数必须恰好有三个非 keyword-only 运行时参数：Context、被包装的 Node 和下一层 callable。构建参数仍为 keyword-only。

```python
from collections.abc import Callable
from slyme.node import Node, wrapper


@wrapper
def trace(ctx, wrapped: Node, call_next: Callable, *, name: str):
    print(name, "start")
    result = call_next(ctx)
    print(name, "end")
    return result


task.add_wrappers(trace(name="add"))
```

Wrapper 按洋葱模型组合，并在调用时读取实时参数。上例只适用于同步执行：`call_next(ctx)` 返回 awaitable 时，后续语句会在异步完成前运行。仅转发结果的 Wrapper 可以直接返回它。需要依赖结果执行后续操作或在完成时清理资源，可以通过 `run()` 驱动生成器，在其中使用 `yield call_next(ctx)` 和 `try/finally`；也可以使用 `async def` 与 `await await_result(call_next(ctx))`。框架不会改写用户的 `try/finally`。

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` 组装同样的洋葱链，但不执行它。它对 wrapper 顺序取快照，第一个 wrapper 在最外层，返回接收 Context 的 callable。Wrapper 参数保持实时读取；各 wrapper 自行决定是否及多少次调用下一层、传入哪个 Context，以及返回值类型。空 wrapper iterable 原样返回 `terminal`。Node 执行内部使用此方法；从外部包装 Node 时，传入 `call_next=task` 也会运行该 Node 已有的 wrappers。

## 组合结构

Node 与 Wrapper 参数可以保存任意值和嵌套 Tree，包括其他 Node 或 Wrapper。Slyme 不对整张对象图施加统一的合法性检查；两者采用相同的直接结果或 awaitable 执行协议。

## 顺序组合

声明式顺序组合使用 `sequential(nodes=[...])`，已有 iterable 使用 `sequential_exec(ctx, nodes)`。两者均在上一步完成后运行下一步，全部完成后返回 `None`，所有步骤同步时保持同步。失败会停止执行，并抛出保存已尝试前缀的 `BatchError`。各步骤共享传入的 Context，有意观察之前的局部写入，与 Auto 子 Node 求值不同。
