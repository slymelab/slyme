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
def process(ctx: Context, /, *, timeout: int = 30, data: Auto[list]):
    return timeout, data


task = process(data=[R.resolve("user.age"), R.resolve("user.name")])
task.set("timeout", 60)
```

Node 参数和 wrapper 可以在两次调用之间修改。修改不需要重新编译整张图，并会从下一次调用开始生效。

## 实时调用与显式分支

每次 Node 或 Wrapper 调用开始时，Slyme 会：

1. 读取并校验对象的当前参数；
2. 分离静态值与需要 Auto 求值的值；
3. 构建 wrapper chain，在每次用户函数调用前求值 Auto 参数；
4. 将静态参数容器直接传给用户函数。

参数绑定使用浅快照，不做深拷贝。在 Node 或 Wrapper 内修改非 Auto 的 `list`、`dict` 或其他叶子，会直接修改该元素上的实时参数，并被后续调用观察到。每个 Auto 参数都会按 Tree 规则遍历并重建容器，无论其中是否包含可求值叶子。普通叶子和 evaluator 返回值仍然共享。

需要另一张可独立配置的图时，应重新调用对应的 Node factory 或 组装函数。`context.fork()` 创建由当前 Context 管理的生命周期子级，并默认共享 `context.scope`。子级需要独立局部数据层和实时 Scope C3 查找时，应使用 `context.fork(scope=context.scope.fork())`；需要把当前可见 binding 物化为新应用根时使用 `Context(context.flatten(), schema=context.schema)`。这些操作都不会复制应用值。

Context 拥有子 Context，以及通过 `effect()`、`add()` 和 `declare()` 注册的 cleanup，按直接归属项的后进先出顺序递归释放。`dispose()` 同步完成时返回 `None`，遇到异步清理则返回 awaitable；两者均可使用 `await await_result(ctx.dispose())` 完成。Context 不会自动拥有使用它的任意 task，应用应先停止并等待这些 task，再释放 Context。

`dispose()` 调用时立即标记释放中并运行同步部分，异步部分需要等待后才调度。提前清理的登记项在完成前仍由 Context 持有，owner 释放会加入同一次清理；移除它不会改变其余登记项的释放顺序。

## Auto 值

静态参数值和从 `Context` 取得的值都保持普通 Python 可变语义：

```python
ctx = Context(schema=R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=[R.resolve("a"), R.resolve("b")])(ctx)  # Auto 生成求值后的 list [1, 2]
process(data=R.resolve("items"))(ctx)  # data 是 Context 中保存的 list
```

Auto Ref 会从 `ctx` 读取值；每个 Auto 子 Node 则使用由父 Context 管理、绑定到独立 child Scope 的 Context 执行。求值保持同步，直到子调用或 cleanup 返回 awaitable；被等待后，未完成的 child 和剩余 sibling 并发执行，结果保持输入顺序。成功的子节点立即释放 Context。全部子任务结束后，会完成失败或被中断的释放，再报告错误或执行父函数。

Auto 会尝试执行所有 sibling，包括同步节点已经失败的情况。子节点失败或取消不会取消其他 sibling；求值会等待所有节点结束，类似 `asyncio.gather(..., return_exceptions=True)`。子节点错误按输入顺序收集，最终清理中的错误追加在后。第一个非取消错误作为主错误抛出，其他错误通过 cause 保留；只有取消时才传播取消。重复释放同一个 Context 不会重复报告其保存的同一个错误。作为普通返回值的异常对象仍是数据。某个子节点一直不结束，求值就会一直等待；超时和 abort 策略由应用负责。

取消外层求值 Task 时，asyncio 会将取消传播给尚未完成的子任务。Auto 会等待这些任务退出以及子 Context 清理完成，包括收到重复取消的情况。同步子节点在事件循环线程内直接执行，执行期间无法被打断。Task 取消不保证底层网络、线程或进程中的工作已经停止；这些行为由应用适配层负责。

Auto child 返回的值不得依赖其子 Context 拥有的资源，因为这些资源会在父函数运行前关闭；返回这类值时应显式转移所有权，或者使用生命周期更长的 Context。子 Context 仍会共享可变 leaf 对象，也无法撤销没有注册 cleanup 的文件、网络请求或其他外部副作用。

显式编排采用不同语义。直接调用 Node 或使用 `sequential_exec(ctx, children)` 时会传入指定的 Context 本身，因此这些步骤会有意观察到彼此的局部写入。

直接等待 `await await_result(ctx.dispose())` 的调用者取消时，已调度的清理不会取消，但该调用者可能先退出。应用退出前应再次等待同一次释放，观察保留的结果。Auto 则会先等子 Context 清理结束，再传播取消，包括清理期间的重复取消。

Context 创建、外部输入校验和输出提取由应用代码负责。使用 `node(ctx)` 执行图，或者通过 `await node.acall(ctx)` 获得始终可等待的结果。`await ctx.adispose()` 同样只适配释放的返回值，不改变执行与所有权规则。
