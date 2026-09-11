# 生命周期（Lifecycle）

Slyme 使用一张持续存在的 `Node` 图，而不再区分定义树与执行树。调用装饰后的函数会创建可变 Node；调用这个 Node 时，会使用它的当前参数直接执行。

## 构建与修改

```python
from slyme.context import Context, Schema
from slyme.node import Auto, node

R = Schema({"user": {"age": Schema.leaf(), "name": Schema.leaf()}, "a": Schema.leaf(), "b": Schema.leaf(), "items": Schema.leaf()})


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
3. 临时生成求值计划与 wrapper chain；
4. 将静态参数容器直接传给用户函数。

调用过程不再创建隐式冻结快照。在 Node 或 Wrapper 内修改静态 `list`、`dict` 或其他叶子，会直接修改该元素上的实时参数，并被后续调用观察到。包含 `Ref` 或子 Node 的 Auto 结构仍会用求值结果重建，因为求值本身会生成结果树。

需要另一张可独立配置的图时，应重新调用对应的 Node factory 或 组装函数。`context.fork()` 创建由当前 Context 管理的生命周期子级，并默认共享 `context.scope`。子级需要独立局部数据层和实时 Scope C3 查找时，应使用 `context.fork(scope=context.scope.fork())`；需要把当前可见 binding 物化为新应用根时使用 `Context(context.flatten(), schema=context.schema)`。这些操作都不会复制应用值。

Context 会拥有其子 Context，以及通过 `effect()`、`async_effect()`、`add()` 和 `declare()` 注册的 cleanup。每个 Context 都按后进先出顺序处理自己的直接归属项，并递归销毁子级。Context 不会自动拥有仅仅使用它的任意 task；应用必须先停止并等待这些 task，再 dispose 它们使用的 Context。`dispose()` 处理完全同步的子树，`await async_dispose()` 同时处理同步与异步 cleanup。注册操作返回的 disposer 可用于提前清理，但不会改变生命周期归属模型。

同步释放会在清理任何资源前，检查整棵子树是否包含异步 cleanup。提前清理的登记项在完成前仍由 Context 持有；移除它不会改变其余登记项的释放顺序。

## Auto 值

静态参数值和从 `Context` 取得的值都保持普通 Python 可变语义：

```python
ctx = Context(schema=R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=[R.resolve("a"), R.resolve("b")])(ctx)  # Auto 生成求值后的 list [1, 2]
process(data=R.resolve("items"))(ctx)  # data 是 Context 中保存的 list
```

Auto Ref 会从 `ctx` 读取值；每个 Auto 子 Node 则使用由父 Context 管理、绑定到独立 child Scope 的 Context 执行。同步求值会在继续前 dispose 子 Context，并在 setup 前拒绝 `async_effect()`；异步求值会先等待子 Context cleanup 完成，再传播取消。异步求值遇到同步 child 时，会在事件循环线程内直接执行，并且只能在 child 返回后响应取消。如果某个 Auto child 失败，它仍是主错误，已取消 sibling 及其 cleanup 的失败则通过异常 cause 保留。如果取消是唯一的主结果、但 cleanup 失败，则抛出 cleanup 失败并把取消挂在 cause 上，避免 `asyncio` 的取消状态隐藏该失败。Auto child 返回的值不得依赖其子 Context 拥有的资源，因为这些资源会在父函数运行前关闭；返回这类值时应显式转移所有权，或者使用生命周期更长的 Context。子 Context 仍会共享可变 leaf 对象，也无法撤销没有注册 cleanup 的文件、网络请求或其他外部副作用。

显式编排采用不同语义。直接调用 Node 或使用 `sequential_exec(ctx, children)` 时会传入指定的 Context 本身，因此这些步骤会有意观察到彼此的局部写入。

直接等待 `async_dispose()` 的调用者被取消时，cleanup 不会被取消，但该调用者可能在 cleanup 完成前返回。应用退出前应再次等待 `async_dispose()`，或者保留另一个 waiter，以观察被保存的最终结果。

Context 创建、外部输入校验和输出提取由应用代码负责。核心执行只有一个入口：直接调用 `node(ctx)`。
