# 生命周期（Lifecycle）

每个 Context 创建一个私有的 `Lifecycle(ctx, dispose_mode=...)`，管理自身 effect、释放策略和释放状态。`ctx.dispose_mode` 是转发到 Lifecycle 的只读 property，不重复存储。父子树只由 Context 保存；Lifecycle 通过自己的 `ctx` 查找祖先和子级。子级释放登记为父级的内部 effect，遵循父级的释放策略。所有 effect 结束后，包括清理失败时，Lifecycle 调用 Context 的数据释放方法；应用根还会将 Store 从 Schema 注销。Lifecycle 没有独立的 parent 或 finalizer 配置。应用应通过 `ctx.effect()` 登记清理，而不是重写 `Context.dispose()`。

`ctx.children` 按创建顺序返回直接子级的 tuple 快照。子级在异步清理期间仍然挂靠父级，释放结束后才移除，失败时也会移除。已释放的 Context 没有子级，但保留原来的 `parent` 引用。Scope 继承与这棵归属树相互独立。

Slyme 使用一张持续存在的 `Node` 图，而不再区分定义树与执行树。调用装饰后的函数会创建可变 Node；调用这个 Node 时，会使用它的当前参数直接执行。

## 构建与修改

```python
from slyme.context import Context, Schema
from slyme.node import Auto, node

R = Schema(
    {
        "user": {"age": Schema.leaf(), "name": Schema.leaf()},
        "a": Schema.leaf(),
        "b": Schema.leaf(),
        "items": Schema.leaf(),
    }
)


@node
def process(ctx: Context, /, *, timeout: int = 30, data: list):
    return timeout, data


task = process(data=Auto([R.resolve("user.age"), R.resolve("user.name")]))
task.set("timeout", 60)
```

Node 参数和 wrapper 可以在两次调用之间修改。修改不需要重新编译整张图，并会从下一次调用开始生效。

## 实时调用与显式分支

每次 Node 或 Wrapper 调用开始时，Slyme 会：

1. 对已保存的绑定取浅快照，并应用本次调用的关键字覆盖；
2. 分离普通值与显式包装的 Auto 树；
3. 构建 wrapper chain，在每次用户函数调用前求值 Auto 参数；
4. 将静态参数容器直接传给用户函数。

参数绑定使用浅快照，不做深拷贝。在 Node 或 Wrapper 内修改非 Auto 的 `list`、`dict` 或其他叶子，会直接修改该元素上的实时参数，并被后续调用观察到。每个 Auto 参数都会按 Tree 规则遍历并重建容器，无论其中是否包含可求值叶子。普通叶子和 evaluator 返回值仍然共享。

需要另一张可独立配置的图时，应重新调用对应的 Node factory 或 组装函数。`context.fork()` 创建由当前 Context 管理的生命周期子级，并默认共享 `context.scope`。子级需要独立局部数据层和实时 Scope C3 查找时，应使用 `context.fork(scope=context.scope.fork())`；所选业务子树的字段都采用 `assign` 模式时，可以创建新根并声明相应路径，再用 `snapshot.update(context.get("app").flatten())` 物化值；`register` 字段需要在新 owner 上显式调用 `register()`。这些操作都不会复制应用值。

Context 拥有子 Context，以及通过 `effect()`、`register()` 和 `declare()` 注册的 cleanup。其 `dispose_mode` 决定直接归属项采用串行还是并发清理。`dispose()` 同步完成时返回 `None`，遇到异步清理则返回 awaitable；两者均可使用 `await await_result(ctx.dispose())` 完成。Context 不会自动拥有使用它的任意 task，应用应先停止并等待这些 task，再释放 Context。

`dispose()` 调用时立即标记释放中并运行同步部分，异步部分需要等待后才调度。提前清理的登记项在完成前仍由 Context 持有，owner 释放会加入同一次清理；移除它不会改变其余登记项的释放顺序。

串行清理等待每项结束后才调用下一项；并发清理先调用所有 disposer，再一起等待异步结果。两种模式均按登记逆序访问各项，同步操作直接执行，无需事件循环。某项失败或自身取消不会跳过其余归属项或 Scope 释放。Context 按登记逆序汇总失败，不依赖完成顺序；后续释放调用观察同一个最终结果，不重复执行 cleanup。Lifecycle 使用 `once` 和 `SharedAwaitable` 共享执行及结果。调用和等待都会检查生命周期重入，包括通过先前取得的完成句柄进行等待。

取消 setup 或释放的等待者不会取消共享操作，调用方仍须等待完成并处理失败。Slyme 不会仅为抑制 asyncio 的未观察异常诊断而提取后台异常；这些诊断不能替代应用的错误处理。

## 清理分组

`Context()`、`fork()` 和 `derive()` 接受 `dispose_mode="sequential" | "parallel"`，每个新 Context 均默认使用 `"sequential"`。策略不可修改，也不继承父级配置。它只影响清理，不影响 setup 或 Node 执行。无论子级采用何种内部策略，父级都会等待子级完整清理。

```python
root = Context()
root.declare(R)
plugins = root.fork(dispose_mode="parallel")
plugin_a = plugins.fork()
plugin_b = plugins.fork()
```

这里 plugin_a 与 plugin_b 并发清理，各自内部保持 LIFO。根声明和框架默认配置保留到两者结束。继续 fork 可以任意嵌套清理策略，不增加 Scope 继承层。操作归属于调用它的 Context，不存在隐式的当前分组或 disposer 转移。

并发的兄弟项必须允许清理重叠。共享依赖应先登记到串行父级，再创建消费者，或者显式建立等待关系。清理期间允许读取，并不阻止其他并发 effect 撤销字段。Context 不会根据数据可见性推导插件卸载顺序。

不同 cleanup 可以等待同一个提前 disposer，它只执行一次，所有等待者共享完成结果。跨组等待必须无环。操作的前置条件应放在其共享清理内部，而不是分散在各调用方；也不能另有绕过这些条件的独立清理入口。共享等待不会统计使用者，也不会等所有调用方到达。每个等待分支都能观察到清理失败，因此同一失败可能出现在多个嵌套异常组中。

## Auto 值

静态参数值和从 `Context` 取得的值都保持普通 Python 可变语义：

```python
ctx = Context()
ctx.declare(R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=Auto([R.resolve("a"), R.resolve("b")]))(
    ctx
)  # Auto 生成求值后的 list [1, 2]
process(data=Auto(R.resolve("items")))(ctx)  # data 是 Context 中保存的 list
```

Auto Ref 会从 `ctx` 读取值；每个 Auto 子 Node 则使用由父 Context 管理、绑定到独立 child Scope 的 Context 执行。子调用和同步清理内联执行；异步结果在被等待时并发调度，结果保持输入顺序。每个子节点无论成功还是失败，都在自己的 `finally` 中释放 Context，不等待其他 sibling。在调用者未取消的情况下，求值会等待所有子节点及其清理完成，再报告错误或执行父函数。

Auto 会尝试执行所有 sibling 和所有 evaluator 组，包括同步求值已经失败的情况。Evaluator 组互相独立，可以并发执行；Ref 查找失败不会阻止 Node 求值。子节点失败或取消不会取消其他 sibling，求值会等待所有节点结束。错误组成嵌套异常组：外层按 evaluator 组首次出现的顺序排列，内置 evaluator 的组按其输入顺序排列。组内只包含失败，每个求值组的消息列出失败项的原始输入索引，不提供部分成功结果。子 Context 的清理错误作为该子节点的错误；如果 Node 本身也失败，Python 的 `finally` 语义会将 Node 异常保留为清理错误的 `__context__`。Node 和 Wrapper 通过异常记录的 `__cause__` 保存普通异常组；包含非 `Exception` 控制异常的组原样传播。作为普通返回值的异常对象仍是数据。某个子节点一直不结束，求值就会一直等待；超时和 abort 策略由应用负责。

取消外层求值 Task 时，asyncio 会将取消传播给尚未完成的子任务，不汇总部分求值结果。每个子节点都会进入自己的 `finally` 清理。等待清理期间的取消可能使求值先退出，而由 Context 管理的清理继续在后台运行。同步子节点在事件循环线程内直接执行，执行期间无法被打断。Task 取消不保证底层网络、线程或进程中的工作已经停止；这些行为由应用适配层负责。

Auto child 返回的值不得依赖其子 Context 拥有的资源，因为这些资源会在父函数运行前关闭；返回这类值时应显式转移所有权，或者使用生命周期更长的 Context。子 Context 仍会共享可变 leaf 对象，也无法撤销没有注册 cleanup 的文件、网络请求或其他外部副作用。

显式编排采用不同语义。直接调用 Node 或使用 `sequential_exec(ctx, children)` 时会传入指定的 Context 本身，因此这些步骤会有意观察到彼此的局部写入。

直接等待 `await await_result(ctx.dispose())` 的调用者取消时，已调度的清理不会取消，但该调用者可能先退出。应用退出前应再次等待同一次释放，观察保留的结果。父 Context 只能等待仍由它持有的子 Context 的清理。Auto 不返回临时子 Context；求值取消后，后台清理失败可能既不会传给调用者，也不会传给之后才开始的父 Context 释放。

Context 创建、外部输入校验和输出提取由应用代码负责。使用 `node(ctx)` 执行图，或者通过 `await node.acall(ctx)` 获得始终可等待的结果。`await ctx.adispose()` 同样只适配释放的返回值，不改变执行与所有权规则。
