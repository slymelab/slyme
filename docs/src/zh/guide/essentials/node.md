# Node

`@node` 将函数转换为可变、可直接调用的 `Node` 对象工厂。Node 可以修改 `Context`、执行副作用、协调子 Node，也可以返回供 `Auto` 注入的派生值。

## 定义与创建 Node

Node 工厂只接受关键字绑定。执行时按位置传入 Context，并按关键字传入合并后的绑定；具体如何接收由用户函数自身的签名决定：

```python
from slyme.context import Context, Ref, Schema
from slyme.node import Auto, node

R = Schema({"input": {"x": Schema.leaf()}, "output": {"total": Schema.leaf()}})


@node
def add(ctx: Context, *, x: int, y: int, output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add(x=Auto(R.resolve("input.x")), y=2, output=R.resolve("output.total"))
```

Slyme 不分析函数签名或解析类型注解。函数可以声明 `*args` 和 `**kwargs`，由 Python 正常绑定位置 Context 和传入的关键字。Node 的框架参数 `ctx` 为仅位置参数，因此可以独立传递名为 `ctx` 的业务关键字。

`@node` 和 `@node()` 都返回普通工厂函数，每次调用创建独立的 Node。无需定义工厂时，可以用 `create_node()` 直接创建实例：

```python
from slyme.node import create_node

inline = create_node(lambda ctx, *, value: value + 1, {"value": 3})
```

`create_node(func, params=None, *, wrappers=None)` 通过 mapping 接收业务绑定，组装配置独立传入。每个实例复制自己的参数 mapping 和 wrapper 列表，但保持内部值与 Wrapper 对象的引用。构造时不执行函数，也不求值 Auto 绑定。组装时通过 `create_node(..., wrappers=[...])` 或 `task.add_wrappers(...)` 添加 wrappers；`@node` 不接收 wrappers。装饰后的工厂保留函数元信息，通过 `.__wrapped__` 可获得原函数。

## 执行

自行管理 Context 时直接调用 Node：

```python
ctx = Context()
ctx.declare(R)
ctx.set(R.resolve("input.x"), 3)
result = task(ctx)  # 5
```

Context 创建、外部输入处理和输出提取均由调用方负责。Node 核心执行既不会推导 Schema，也不会隐式创建 Context。

现在没有 Def/Exec 转换和 `prepare()` 阶段。每次调用都会绑定并解析当前参数与 wrapper。

## 同步与异步组合

Node 的返回类型是 `T | Awaitable[T]`，取决于实际执行的 Auto、Wrapper、用户函数及临时 Context 清理。纯同步调用直接返回；普通 `def` 也可以返回 awaitable，不需要声明执行模式。同步父函数可以等待异步 Auto 注入后再运行。

```python
from slyme.utils.execution import await_result


async def execute(ctx):
    try:
        return await await_result(task(ctx))
    finally:
        await await_result(ctx.dispose())
```

`await_result()` 接受普通值或 awaitable。普通调用在结果传给适配函数前发生，所以同步工作和错误仍会立即执行或抛出。适配函数不会创建 task 或调度异步工作。

`await_result()` 只等待外层执行结果，不递归等待容器中的数据。异步 continuation 在被等待或调度前不会执行；同步前缀可能已运行。同步应用可以在应用入口使用 `asyncio.run(await_result(task(ctx)))`，已有事件循环内应 await，不创建嵌套事件循环。框架不会把阻塞函数自动放入线程。

用户函数内部的调用仍需显式处理返回值：同步代码不能把未知的 `child(ctx)` 结果直接用于计算。可以使用 `async def` 和 `await await_result(child(ctx))`，或驱动下面的生成器。直接返回的 awaitable 表示执行；若要将其作为数据传递，应装入普通容器。

### 生成器组合

`slyme.utils.execution` 的 `@continuation` 将生成器函数转换为可直接调用的同步或异步函数。yield 普通值时立即将值送回；yield awaitable 时返回尚未调度的异步剩余流程。等待产生的异常会在暂停的 `yield` 位置抛回，因此循环、分支和 `try/except/finally` 只需写一份：

```python
from slyme.node import Node
from slyme.utils.execution import continuation


@node
@continuation
def increment_child(ctx, *, child: Node[int]):
    value = yield child(ctx)
    return value + 1
```

`@continuation()` 等价于 `@continuation`。装饰器保留函数元信息与参数类型，每次调用都会创建新的生成器，不共享执行状态。叠加使用时，将它放在 `@node` 或 `@wrapper` 内侧：先适配执行，再定义工厂。也可以不加图装饰器，直接调用装饰后的函数。

调用装饰后的函数会立即执行同步前缀，同步错误也立即抛出。通过 `await await_result(execute(...))` 等待返回的剩余流程，继续异步工作。只在同步应用入口使用 `asyncio.run(await_result(execute(...)))`。驱动器不启动事件循环，也不调度 task。已有生成器仍可直接交给 `run(generator)`；装饰器内部委托给它。

只检查显式交给 `yield` 的值；容器和裸生成器仍是普通数据。每次 yield 只等待外层结果，生成器最终 return 的值不隐式等待。需要将操作完成纳入生成器的异常处理时，使用 `return (yield operation())`。通过 `yield from` 委托未装饰的生成器，或 yield 经 continuation 装饰的函数调用结果。Node 不会自动驱动作为返回值的生成器。

将生成器的独占推进权交给 `run()`；耗尽和重入遵循 Python 的生成器协议。驱动器不缓存结果、不屏蔽取消、不汇总异常，也不拥有资源。取消会在暂停的 yield 位置抛回，遵循生成器的异常处理逻辑。等待剩余流程时，`finally` 可以 yield 异步清理；但丢弃剩余流程不会完成这些清理。

执行顺序、并发和结果收集由调用方决定。逐项 yield 调用的循环会等待当前项完成后再继续。并发实现可以先调用各项、保存返回的 awaitable，再 yield 一个异步聚合操作。若调用方可能还没有运行中的事件循环，应在该异步操作内部创建 `gather()` 或 Task。枚举或调用期间的同步异常遵循调用方的 `try/except/finally`，驱动器不额外定义 batch 策略。

Auto 独立拥有全部完成后汇总的求值策略。它先内联调用每个 evaluator 组和子节点，再等待异步结果；只有异步结果会调度为 Task。单项失败不会取消 sibling。每个子节点无论成功还是失败，都在自己的 `finally` 中释放 Context。错误按输入顺序组成嵌套异常组，各组消息包含失败项的输入索引；只有整个批次成功时才返回结果。Node 和 Wrapper 会将普通异常（包括普通异常组）包装成异常记录，由 `__cause__` 保存原始异常；已有 Node 异常记录及非 `Exception` 控制异常原样传播。取消批次遵循 asyncio 的传播规则，不再汇总部分结果，并可能在 Context 管理的清理结束前返回，详见 [Auto 生命周期](./lifecycle.md#auto-值)。Context 独立拥有递归 LIFO 清理策略；两个消费者共用生成器驱动器，不共用执行策略 API。

## 参数与 Auto

函数参数描述最终接收的值。构造或调用时，将需要求值的参数树包装为 `Auto(...)`：

```python
@node
def parent(ctx, *, child: int):
    return child + 1


@node
def child(ctx, *, value: int):
    return value


root = parent(child=Auto(child(value=4)))
assert root(Context()) == 5
```

`Auto` 会递归解析已注册的 evaluator 叶子，例如 `Ref` 和 `Node`。Ref 会直接读取传入的 Context；每个产生值的子 Node 则获得由父 Context 管理、绑定到独立 `ctx.scope.fork()` 的子 Context。父 Node 继续执行前，Slyme 会 dispose 该 Context 及其 effect，因此 child Scope 的写入与由 Context 管理的注册不会泄漏到父级或其他 Auto 子 Node。返回值、对共享 leaf 对象的修改、disposer 未交给子 Context 管理的直接注册，以及没有注册 cleanup 的外部副作用并不会被隔离。没有 `Auto` 时，Ref 和 Node 对象会原样传入。

框架只保存显式传入的绑定。未绑定参数使用函数原生默认值；缺少必需参数或传入未知参数时，由 Python 在实际调用函数时抛出 `TypeError`，并保留在 Node 或 Wrapper 的异常记录中。因此参数错误被发现前，Auto 工作可能已经执行。Slyme 不扫描或求值函数默认值：动态输入应显式绑定，而不是把 `Auto(...)` 放进函数默认值。普通未包装参数内部的 Auto 对象也是普通数据。

## 动态修改

Node 和 Wrapper 参数通过显式参数 API 在两次调用之间保持可变：

```python
root.get("child").value.set("value", 10)
assert root(Context()) == 11
assert root(Context(), child=20) == 21  # 不执行已绑定的子 Node。
```

使用 `get(name)` 读取绑定，使用 `set(name, value)` 保存任意绑定，使用 `delete(name)` 删除绑定。删除不存在的绑定不做任何操作。读取不存在的 key 会抛出 `KeyError`，除非通过 `get(name, default)` 提供回退值；回退值可以是包括 `None` 在内的任意值。返回回退值不会新增绑定，已有值（包括 `None`）始终优先。这些操作不会读取函数默认值。实时、只读的 `params` mapping 只包含已保存的绑定。`node(ctx, **kwargs)` 在 Auto 求值前，为本次调用浅覆盖绑定，不修改 `params`，也不合并嵌套容器。参数名可以与框架属性重合，因为参数不会投影为对象属性。

工厂和可变绑定接受动态关键字名称与值，包括部分绑定和 Auto 树。类型声明保留执行结果类型，但不会根据底层函数参数逐项静态检查绑定。

调用时，非 Auto 参数会直接传给用户函数，因此对其容器的修改会更新 Node 或 Wrapper 上的实时参数。每个 Auto 参数都会按 Tree 规则遍历并重建容器，包括只有普通值的子树。普通叶子与 evaluator 返回值保持原对象 identity；这不是深拷贝。

需要另一张可独立配置的图时，应重新调用 Node factory 或 组装函数。可变应用值是否共享或复制由其自身语义决定；Slyme 不会猜测哪些引用需要复制。

## Wrapper

`@wrapper` 和 `@wrapper()` 定义可复用的工厂；`create_wrapper(func, params=None)` 直接创建拥有独立浅拷贝绑定的 Wrapper。

`@wrapper` 工厂同样只接受关键字绑定。执行时依次按位置传入 Context、被包装的 Node 和下一层 callable，再传入合并后的关键字绑定。用户函数可以使用具名参数或 `*args`/`**kwargs` 接收。框架的 `Wrapper.__call__` 参数为仅位置参数，直接调用时可以独立覆盖名为 `ctx`、`wrapped` 或 `call_next` 的业务关键字。

```python
from collections.abc import Callable
from slyme.node import Node, wrapper
from slyme.utils.execution import continuation


@wrapper
@continuation
def trace(ctx, wrapped: Node, call_next: Callable, *, name: str):
    print(name, "start")
    try:
        return (yield call_next(ctx))
    finally:
        print(name, "end")


task.add_wrappers(trace(name="add"))
```

Wrapper 按洋葱模型组合，并在调用时读取实时参数。上例等待同步或异步调用完成，无论成功还是失败都会执行 `finally`。仅转发结果的 Wrapper 可以直接返回 `call_next(ctx)`，不需要 `@continuation`；`async def` Wrapper 也可以使用 `await await_result(call_next(ctx))`。框架不会改写用户的 `try/finally`。

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` 组装同样的洋葱链，但不执行它。它对 wrapper 顺序取快照，第一个 wrapper 在最外层，返回接收 Context 的 callable。Wrapper 参数保持实时读取；各 wrapper 自行决定是否及多少次调用下一层、传入哪个 Context，以及返回值类型。空 wrapper iterable 原样返回 `terminal`。Node 执行内部使用此方法；从外部包装 Node 时，传入 `call_next=task` 也会运行该 Node 已有的 wrappers。

## 组合结构

Node 与 Wrapper 参数可以保存任意值和嵌套 Tree，包括其他 Node 或 Wrapper。Slyme 不对整张对象图施加统一的合法性检查；两者采用相同的直接结果或 awaitable 执行协议。

## 顺序组合

声明式顺序组合使用 `sequential(nodes=[...])`，已有 iterable 使用 `sequential_exec(ctx, nodes)`。两者均在上一步完成后运行下一步，全部完成后返回 `None`，所有步骤同步时保持同步。失败会停止执行并传播失败 Node 的异常，不收集此前的结果。各步骤共享传入的 Context，有意观察之前的局部写入，与 Auto 子 Node 求值不同。
