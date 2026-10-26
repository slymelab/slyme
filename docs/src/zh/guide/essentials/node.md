# Node

`@node` 将函数直接转换为可调用的 `Node` 对象。Node 可以更新 Context、执行副作用、组合子 Node，或返回供 Auto 注入的值。


推荐的四个函数入口是 `slyme.node` 的 `node/wrapper/auto` 和
`slyme.context` 的 `ref`。大写 `Node/Wrapper/Ref` 保留用于类型标注与
显式构造；`auto()` 返回的标记类型是内部实现；优先通过组合扩展行为，但不限制继承。

```python
from slyme.context import Ref, ref
from slyme.node import auto

count: Ref[int] = ref("count")
input_value = auto(count)
```

`ref` 保留泛型返回类型，可根据接收位置的预期类型推导。Python 普通函数目前
不支持 `ref[int](...)`，需要时采用上面的变量标注。`auto` 保留源值类型，
不会执行或复制源值。

## 定义与创建 Node

通过 `partial(**params)` 绑定关键字参数，不执行 Node。执行时按位置传入 Context，并按关键字传入合并后的绑定；具体如何接收由用户函数自身的签名决定：

```python
from slyme.context import Context, Ref, Schema
from slyme.node import node, auto

R = Schema({"input": {"x": Schema.leaf()}, "output": {"total": Schema.leaf()}})


@node
def add(ctx: Context, *, x: int, y: int, output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add.partial(x=auto(R.resolve("input.x")), y=2, output=R.resolve("output.total"))
```

Slyme 不分析函数签名或解析类型注解。函数可以声明 `*args` 和 `**kwargs`，由 Python 正常绑定位置 Context 和传入的关键字。Node 的框架参数 `ctx` 为仅位置参数，因此可以独立传递名为 `ctx` 的业务关键字。

`@node` 和 `@node()` 都直接返回 Node。也可以用 `node(func, params=..., metadata=...)` 包装已有函数，配置参数仅支持关键字传入：

```python
inline = node(lambda ctx, *, value: value + 1, params={"value": 3})
assert inline(Context()) == 4
```

`@node(params={...}, metadata={...})` 和 `@wrapper(params={...}, metadata={...})` 在配置装饰器时浅复制输入 mapping。创建对象和调用 `partial()` 都不会执行函数或求值 Auto；`.func` 保留原函数。原来的 `create_node` 和 `create_wrapper` 已移除。

metadata 接受来自 `slyme.context` 的 `Mapping[str, Metadata]`。每个实例通过 metadata 暴露只读快照，保留标记对象的身份；使用 `replace(metadata=...)` 派生新对象。metadata 不参与参数合并或 Auto 求值。同名业务参数通过 `task.partial(metadata=value)` 或 `params={"metadata": value}` 绑定。[自省](../slyme-in-depth/inspection.md) 会展示 metadata 的结构。

通过 `task.replace(wrappers=(...))` 派生使用新 Wrapper 序列的 Node。`wrappers` 是只读 tuple，内部 Wrapper 保持原引用；`func`、`params`、`metadata` 也都是只读属性。

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
from slyme.execution import await_result


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

`slyme.execution` 的 `@continuation` 将生成器函数转换为可直接调用的同步或异步函数。yield 普通值时立即将值送回；yield awaitable 时返回尚未调度的异步剩余流程。等待产生的异常会在暂停的 `yield` 位置抛回，因此循环、分支和 `try/except/finally` 只需写一份：

```python
from slyme.node import AnyNode
from slyme.execution import continuation


@node
@continuation
def increment_child(ctx, *, child: AnyNode[int]):
    value = yield child(ctx)
    return value + 1
```

`@continuation()` 等价于 `@continuation`。装饰器保留函数元信息与参数类型，每次调用都会创建新的生成器，不共享执行状态。叠加使用时，将它放在 `@node` 或 `@wrapper` 内侧：先适配执行，再创建 Node 或 Wrapper。也可以不加图装饰器，直接调用装饰后的函数。

调用装饰后的函数会立即执行同步前缀，同步错误也立即抛出。通过 `await await_result(execute(...))` 等待返回的剩余流程，继续异步工作。只在同步应用入口使用 `asyncio.run(await_result(execute(...)))`。驱动器不启动事件循环，也不调度 task。已有生成器仍可直接交给 `run(generator)`；装饰器内部委托给它。

只检查显式交给 `yield` 的值；容器和裸生成器仍是普通数据。每次 yield 只等待外层结果，生成器最终 return 的值不隐式等待。需要将操作完成纳入生成器的异常处理时，使用 `return (yield operation())`。通过 `yield from` 委托未装饰的生成器，或 yield 经 continuation 装饰的函数调用结果。Node 不会自动驱动作为返回值的生成器。

将生成器的独占推进权交给 `run()`；耗尽和重入遵循 Python 的生成器协议。驱动器不缓存结果、不屏蔽取消、不汇总异常，也不拥有资源。取消会在暂停的 yield 位置抛回，遵循生成器的异常处理逻辑。等待剩余流程时，`finally` 可以 yield 异步清理；但丢弃剩余流程不会完成这些清理。

执行顺序、并发和结果收集由调用方决定。逐项 yield 调用的循环会等待当前项完成后再继续。并发实现可以先调用各项、保存返回的 awaitable，再 yield 一个异步聚合操作。若调用方可能还没有运行中的事件循环，应在该异步操作内部创建 `gather()` 或 Task。枚举或调用期间的同步异常遵循调用方的 `try/except/finally`，驱动器不额外定义 batch 策略。

Auto 独立拥有全部完成后汇总的求值策略。它先内联调用每个 evaluator 组和子节点，再等待异步结果；只有异步结果会调度为 Task。单项失败不会取消 sibling。每个子节点无论成功还是失败，都在自己的 `finally` 中释放 Context。错误按输入顺序组成嵌套异常组，各组消息包含失败项的输入索引；只有整个批次成功时才返回结果。Node 和 Wrapper 会将普通异常（包括普通异常组）包装成异常记录，由 `__cause__` 保存原始异常；已有 Node 异常记录及非 `Exception` 控制异常原样传播。取消批次遵循 asyncio 的传播规则，不再汇总部分结果，并可能在 Context 管理的清理结束前返回，详见 [Auto 生命周期](./lifecycle.md#auto-值)。Context 独立拥有递归 LIFO 清理策略；两个消费者共用生成器驱动器，不共用执行策略 API。

## 参数与 Auto

函数参数描述最终接收的值。构造或调用时，将需要求值的参数树包装为 `auto(...)`：

```python
from slyme.node import auto

@node
def parent(ctx, *, child: int):
    return child + 1


@node
def child(ctx, *, value: int):
    return value


root = parent.partial(child=auto(child.partial(value=4)))
assert root(Context()) == 5
```

`Auto` 会递归解析已注册的 evaluator 叶子，例如 `Ref` 和 `Node`。Ref 会直接读取传入的 Context；每个产生值的子 Node 则获得由父 Context 管理、绑定到独立 `ctx.scope.fork()` 的子 Context。父 Node 继续执行前，Slyme 会 dispose 该 Context 及其 effect，因此 child Scope 的写入与由 Context 管理的注册不会泄漏到父级或其他 Auto 子 Node。返回值、对共享 leaf 对象的修改、disposer 未交给子 Context 管理的直接注册，以及没有注册 cleanup 的外部副作用并不会被隔离。没有 `Auto` 时，Ref 和 Node 对象会原样传入。

框架只保存显式传入的绑定。未绑定参数使用函数原生默认值；缺少必需参数或传入未知参数时，由 Python 在实际调用函数时抛出 `TypeError`，并保留在 Node 或 Wrapper 的异常记录中。因此参数错误被发现前，Auto 工作可能已经执行。Slyme 不扫描或求值函数默认值：动态输入应显式绑定，而不是把 `auto(...)` 放进函数默认值。普通未包装参数内部的 Auto 对象也是普通数据。

## 通过 partial 绑定参数

`partial(**params)` 浅合并参数并返回新的 Node 或 Wrapper。后一次绑定覆盖前一次；实际调用时传入的关键字仅覆盖本次执行：

```python
from slyme.node import auto

updated = root.partial(child=auto(child.partial(value=10)))
assert updated(Context()) == 11
assert root(Context()) == 5
assert updated(Context(), child=20) == 21
```

`get(name, default)` 读取已有绑定，`params` 提供只读 mapping。缺少绑定且未指定 default 时抛出 KeyError；函数默认值不存入 params。参数不再提供 `set/delete`，修改绑定需派生新对象；需要恢复函数默认值时，用 `replace(params=...)` 提供不含该项的 mapping，或用 `replace(params=None)` 清空全部绑定。

合并是浅层的，不合并嵌套 mapping。`partial(params=..., metadata=...)` 绑定的是普通业务参数。partial 不绑定执行 Context，Auto 在每次执行时根据当次 Context 求值。

调用时，非 Auto 参数会直接传给用户函数，因此对其容器的修改会更新 Node 或 Wrapper 上的实时参数。每个 Auto 参数都会按 Tree 规则遍历并重建容器，包括只有普通值的子树。普通叶子与 evaluator 返回值保持原对象 identity；这不是深拷贝。

需要另一张可独立配置的图时，应调用 `partial(...)` 或重新调用组装函数。可变应用值是否共享或复制由其自身语义决定；Slyme 不会猜测哪些引用需要复制。

## 替换配置

`partial(**params)` 浅合并业务绑定；`replace(params=..., wrappers=..., metadata=...)`
完整替换指定字段。两者始终返回新对象，并保留原函数；Wrapper.replace 没有
wrappers 字段。省略字段表示保留，传入 None 或空集合表示清空。
替换不会执行函数或求值 Auto。

```python
traced = task.replace(wrappers=(*task.wrappers, trace.partial(name="extra")))
unbound = traced.replace(params=None)
plain = unbound.replace(wrappers=(), metadata={})
```

示例假设 trace 已按下文定义。配置 mapping 在复制后通过只读视图暴露。
tuple 和未变更的不可变快照可以共享身份；调用者应依赖内容，不应依赖复制后的身份。
嵌套参数仍是普通 Python 对象，保持共享引用；这不是深冻结，也不自动保证并发安全。
Node/Wrapper 仍按对象身份比较，派生不会按内容去重。
参数 set/delete 和 add_wrappers 均不再提供。

## Wrapper

`@wrapper` 和 `@wrapper()` 直接返回 Wrapper；`wrapper(func, params=..., metadata=...)` 包装已有回调，`partial(**params)` 派生独立绑定。

Wrapper 的 `partial()` 同样只接受关键字绑定。执行时依次按位置传入 Context、被包装的 Node 和下一层 callable，再传入合并后的关键字绑定。用户函数可以使用具名参数或 `*args`/`**kwargs` 接收。框架的 `Wrapper.__call__` 参数为仅位置参数，直接调用时可以独立覆盖名为 `ctx`、`wrapped` 或 `call_next` 的业务关键字。

```python
from collections.abc import Callable
from slyme.node import AnyNode, wrapper
from slyme.execution import continuation


@wrapper
@continuation
def trace(ctx, wrapped: AnyNode, call_next: Callable, *, name: str):
    print(name, "start")
    try:
        return (yield call_next(ctx))
    finally:
        print(name, "end")


task = task.replace(wrappers=(trace.partial(name="add"),))
```

Wrapper 按洋葱模型组合，并在调用时求值 Auto 参数。上例等待同步或异步调用完成，无论成功还是失败都会执行 `finally`。仅转发结果的 Wrapper 可以直接返回 `call_next(ctx)`，不需要 `@continuation`；`async def` Wrapper 也可以使用 `await await_result(call_next(ctx))`。框架不会改写用户的 `try/finally`。

`Node[P, R]` 接受 `AnyWrapper[R]`：Wrapper 应保持最终结果类型，短路时也一样，但可以引入异步操作。这是静态类型约束，不进行运行时值类型检查。需要改变结果类型时，使用另一个 Node 组合原 Node。

`Wrapper.compose(wrappers, wrapped=task, call_next=terminal)` 组装同样的洋葱链，但不执行它。它对 wrapper 顺序取快照，第一个 wrapper 在最外层，返回接收 Context 且保持最终结果类型的 callable。组合链保留原来的 Wrapper 对象，派生 partial 不会替换它们；各 wrapper 自行决定是否及多少次调用下一层、传入哪个 Context。空 wrapper iterable 原样返回 `terminal`。Node 执行内部使用此方法；从外部包装 Node 时，传入 `call_next=task` 也会运行该 Node 已有的 wrappers。

## 组合结构

Node 与 Wrapper 参数可以保存任意值和嵌套 Tree，包括其他 Node 或 Wrapper。Slyme 不对整张对象图施加统一的合法性检查；两者采用相同的直接结果或 awaitable 执行协议。

## 顺序组合

声明式顺序组合使用 `sequential.partial(nodes=[...])`，已有 iterable 使用 `sequential_exec(ctx, nodes)`。两者均在上一步完成后运行下一步，全部完成后返回 `None`，所有步骤同步时保持同步。失败会停止执行并传播失败 Node 的异常，不收集此前的结果。各步骤共享传入的 Context，有意观察之前的局部写入，与 Auto 子 Node 求值不同。


## 静态类型

`Node[P, R]` 和 `Wrapper[P, R]` 使用“业务参数签名、最终结果”的顺序。
`P` 是 `ParamSpec`，保留原函数业务参数的名称、类型和默认值；Context 以及
Wrapper 的 wrapped/call_next 属于框架前置参数。通常让装饰器或创建函数推导
`P`，通过 `.func` 访问原函数时会检查完整签名。

只关心结果时使用 `AnyNode[R]` / `AnyWrapper[R]`，分别等价于
`Node[..., R]` / `Wrapper[..., R]`。原来的单参数 `Node[R]` / `Wrapper[R]`
类型标注应迁移到这两个别名；具体构造、绑定和调用方式见上文。
这些别名主动擦除业务参数签名，不是只读视图。

Wrapper 回调中的 `wrapped: AnyNode[R]`、
`call_next: Callable[[Context], R | Awaitable[R]]` 和自身的最终返回值共用一个
结果类型。应明确标注这些参数；通用转发回调可用 `TypeVar` 表达 `R`。
裸 `Node` / `Callable` 或显式 `Any` 会削弱推导和检查。构造接口保留这个类型关联，
不会为了仅从返回值推导类型而忽略回调输入的约束。

Python 的标准类型系统无法把任意 ParamSpec 转换成“每项可省略并接受 Auto”
的关键字参数映射。因此 `partial()` 接受动态关键字绑定，Node/Wrapper 调用覆盖及
`get/params` 保持动态类型；`partial()` 会静态拒绝位置参数。
这些入口不会证明必需参数已经绑定完整，也不会检查任意绑定值或拼写。
`auto(source)` 保留输入树类型，`.value` 具有源值类型，不承诺任意 evaluator 的结果类型。
没有类型检查器插件、代码生成、函数签名反射或额外运行时校验。
