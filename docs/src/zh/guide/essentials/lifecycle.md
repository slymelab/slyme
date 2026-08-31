# 生命周期（Lifecycle）

Slyme 使用一张持续存在的 `Node` 图，而不再区分定义树与执行树。调用装饰后的函数会创建可变 Node；调用这个 Node 时，会使用它的当前参数直接执行。

## 构建与修改

```python
from slyme.context import Context, RefFactory
from slyme.node import Auto, node

refs = RefFactory({"user": {"age": ..., "name": ...}, "a": ..., "b": ..., "items": ...})


@node
def process(ctx: Context, /, *, timeout: int = 30, data: Auto[list]):
    return timeout, data


task = process(data=[refs.user.age, refs.user.name])
task.timeout = 60
```

Node 参数和 wrapper 可以在两次调用之间修改。修改不需要重新编译整张图，并会从下一次调用开始生效。

## 实时调用与显式克隆

每次 Node 或 Wrapper 调用开始时，Slyme 会：

1. 读取并校验对象的当前参数；
2. 分离静态值与需要 Auto 求值的值；
3. 临时生成求值计划与 wrapper chain；
4. 将静态参数容器直接传给用户函数。

调用过程不再创建隐式冻结快照。在 Node 或 Wrapper 内修改静态 `list`、`dict` 或其他叶子，会直接修改该元素上的实时参数，并被后续调用观察到。包含 `Ref` 或子 Node 的 Auto 结构仍会用求值结果重建，因为求值本身会生成结果树。

需要独立的 Node/Wrapper 与参数 PyTree 结构时调用 `node.clone()`；需要独立 ContextData 结构时调用 `context.clone()`。两者都会保留未注册叶子的对象身份，因此属于结构克隆，而不是任意对象的深拷贝。

## Auto 值

静态参数值和从 `Context` 取得的值都保持普通 Python 可变语义：

```python
ctx = Context()
ctx.update({refs.a: 1, refs.b: 2, refs.items: [1, 2]})

process(data=[refs.a, refs.b])(ctx)  # Auto 生成求值后的 list [1, 2]
process(data=refs.items)(ctx)  # data 是 Context 中保存的 list
```

自行管理 Context 时直接调用 `node(ctx)`；需要 Slyme 准备外部输入、校验 `Arg` 元数据并提取输出时，以 `node.run(...)` 作为应用边界。
