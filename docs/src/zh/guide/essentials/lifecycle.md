# 生命周期（Lifecycle）

Slyme 使用一张持续存在的 `Node` 图，而不再区分定义树与执行树。调用装饰后的函数会创建可变 Node；调用这个 Node 时，会使用它的当前参数直接执行。

## 构建与修改

```python
from slyme.context import Context, Schema, ref
from slyme.node import Auto, node

R = Schema({"user": {"age": ref(), "name": ref()}, "a": ref(), "b": ref(), "items": ref()})


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

需要另一张可独立配置的图时，应重新调用对应的 Node factory 或 Builder。需要拥有空局部层、同时实时读取父级 C3 层次时使用 `context.fork()`；需要把当前可见 binding 物化为新应用根时使用 `Context(context.flatten(), schema=context.schema)`。两种操作都不会复制应用值。

## Auto 值

静态参数值和从 `Context` 取得的值都保持普通 Python 可变语义：

```python
ctx = Context(schema=R)
ctx.update({R.resolve("a"): 1, R.resolve("b"): 2, R.resolve("items"): [1, 2]})

process(data=[R.resolve("a"), R.resolve("b")])(ctx)  # Auto 生成求值后的 list [1, 2]
process(data=R.resolve("items"))(ctx)  # data 是 Context 中保存的 list
```

Auto Ref 会从 `ctx` 读取值；每个 Auto 子 Node 则使用独立的 `ctx.fork()` 执行。子 Node 返回后，其局部写入会被丢弃，也不会与其他 Auto 子 Node 的局部写入发生竞争。fork 仍会共享可变 leaf 对象，也不会撤销文件、网络请求或其他外部副作用。

显式编排采用不同语义。直接调用 Node 或使用 `sequential_exec(ctx, children)` 时会传入指定的 Context 本身，因此这些步骤会有意观察到彼此的局部写入。

Context 创建、外部输入校验和输出提取由应用代码负责。核心执行只有一个入口：直接调用 `node(ctx)`。
