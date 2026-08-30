# 生命周期（Lifecycle）

Slyme 使用一张持续存在的 `Node` 图，而不再区分定义树与执行树。调用装饰后的函数会创建可变 Node；调用这个 Node 时，则基于它的局部快照执行。

## 构建与修改

```python
from slyme.context import Context, R
from slyme.node import Auto, node


@node
def process(ctx: Context, /, *, timeout: int = 30, data: Auto[list]):
    return timeout, data


task = process(data=[R.user.age, R.user.name])
task.timeout = 60
```

Node 参数和 wrapper 可以在两次调用之间修改。修改不需要重新编译整张图，并会从下一次调用开始生效。

## 调用局部快照

每次 Node 或 Wrapper 调用开始时，Slyme 会：

1. 复制当前对象的参数映射；
2. 递归冻结普通 Python 容器（`list` 转为 `tuple`，`dict` 转为只读映射）；
3. 将 Node 类对象视为叶子，不递归 prepare 组合图；
4. 校验参数，临时生成 Auto 求值计划与 wrapper chain；
5. 执行用户函数。

该快照在本次调用期间保持稳定；并发或后续修改只会被后续调用观察到。未来的 Slot 关系与 Node 图即使形成环，也不会被递归准备过程追踪。

## Auto 值

容器冻结只作用于作为 Node 参数传入的结构，不会冻结从 `Context` 取出的值：

```python
ctx = Context()
ctx.update({R.a: 1, R.b: 2, R.items: [1, 2]})

process(data=[R.a, R.b])(ctx)  # data 为 (1, 2)
process(data=R.items)(ctx)  # data 是 Context 中保存的 list
```

自行管理 Context 时直接调用 `node(ctx)`；需要 Slyme 准备外部输入、校验 `Arg` 元数据并提取输出时，以 `node.run(...)` 作为应用边界。
