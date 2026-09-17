# 生命周期（Lifecycle）

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

需要另一张可独立配置的图时，应重新调用对应的 Node factory 或 组装函数。`context.fork()` 创建由当前 Context 管理的生命周期子级，并默认共享 `context.scope`。子级需要独立局部数据层和实时 Scope C3 查找时，应使用 `context.fork(scope=context.scope.fork())`；需要把当前可见 binding 物化为新应用根时使用 `Context(context.flatten(), schema=context.schema)`。这些操作都不会复制应用值。

Context 拥有子 Context，以及通过 `effect()`、`add()` 和 `declare()` 注册的 cleanup，按直接归属项的后进先出顺序递归释放。`dispose()` 同步完成时返回 `None`，遇到异步清理则返回 awaitable；两者均可使用 `await await_result(ctx.dispose())` 完成。Context 不会自动拥有使用它的任意 task，应用应先停止并等待这些 task，再释放 Context。

`dispose()` 调用时立即标记释放中并运行同步部分，异步部分需要等待后才调度。提前清理的登记项在完成前仍由 Context 持有，owner 释放会加入同一次清理；移除它不会改变其余登记项的释放顺序。

每项清理完成后才开始下一项，包括异步清理。某项失败或自身取消不会跳过其余归属项或 Scope 释放。Context 将归属项清理错误组成异常组，只按清理执行顺序保存失败；后续释放调用观察同一个最终结果，不重复执行 cleanup。Context 的生成器循环负责编排清理，独立的共享完成对象负责防止等待者取消中断 cleanup，并支持重复等待。

## Auto 值

静态参数值和从 `Context` 取得的值都保持普通 Python 可变语义：

```python
ctx = Context(schema=R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=Auto([R.resolve("a"), R.resolve("b")]))(ctx)  # Auto 生成求值后的 list [1, 2]
process(data=Auto(R.resolve("items")))(ctx)  # data 是 Context 中保存的 list
```

Auto Ref 会从 `ctx` 读取值；每个 Auto 子 Node 则使用由父 Context 管理、绑定到独立 child Scope 的 Context 执行。子调用和同步清理内联执行；异步结果在被等待时并发调度，结果保持输入顺序。每个子节点无论成功还是失败，都在自己的 `finally` 中释放 Context，不等待其他 sibling。在调用者未取消的情况下，求值会等待所有子节点及其清理完成，再报告错误或执行父函数。

Auto 会尝试执行所有 sibling 和所有 evaluator 组，包括同步求值已经失败的情况。Evaluator 组互相独立，可以并发执行；Ref 查找失败不会阻止 Node 求值。子节点失败或取消不会取消其他 sibling，求值会等待所有节点结束。错误组成嵌套异常组：外层按 evaluator 组首次出现的顺序排列，内置 evaluator 的组按其输入顺序排列。组内只包含失败，每个求值组的消息列出失败项的原始输入索引，不提供部分成功结果。子 Context 的清理错误作为该子节点的错误；如果 Node 本身也失败，Python 的 `finally` 语义会将 Node 异常保留为清理错误的 `__context__`。Node 和 Wrapper 通过异常记录的 `__cause__` 保存普通异常组；包含非 `Exception` 控制异常的组原样传播。作为普通返回值的异常对象仍是数据。某个子节点一直不结束，求值就会一直等待；超时和 abort 策略由应用负责。

取消外层求值 Task 时，asyncio 会将取消传播给尚未完成的子任务，不汇总部分求值结果。每个子节点都会进入自己的 `finally` 清理。等待清理期间的取消可能使求值先退出，而由 Context 管理的清理继续在后台运行。同步子节点在事件循环线程内直接执行，执行期间无法被打断。Task 取消不保证底层网络、线程或进程中的工作已经停止；这些行为由应用适配层负责。

Auto child 返回的值不得依赖其子 Context 拥有的资源，因为这些资源会在父函数运行前关闭；返回这类值时应显式转移所有权，或者使用生命周期更长的 Context。子 Context 仍会共享可变 leaf 对象，也无法撤销没有注册 cleanup 的文件、网络请求或其他外部副作用。

显式编排采用不同语义。直接调用 Node 或使用 `sequential_exec(ctx, children)` 时会传入指定的 Context 本身，因此这些步骤会有意观察到彼此的局部写入。

直接等待 `await await_result(ctx.dispose())` 的调用者取消时，已调度的清理不会取消，但该调用者可能先退出。应用退出前应再次等待同一次释放，观察保留的结果。父 Context 只能等待仍由它持有的子 Context 的清理。Auto 不返回临时子 Context；求值取消后，后台清理失败可能既不会传给调用者，也不会传给之后才开始的父 Context 释放。

Context 创建、外部输入校验和输出提取由应用代码负责。使用 `node(ctx)` 执行图，或者通过 `await node.acall(ctx)` 获得始终可等待的结果。`await ctx.adispose()` 同样只适配释放的返回值，不改变执行与所有权规则。
