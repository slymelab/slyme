# Node

`@node` 将函数转换为可变、可直接调用的 `Node` 对象工厂。Node 可以修改 `Context`、执行副作用、协调子 Node，也可以返回供 `Auto` 注入的派生值。

## 定义与创建 Node

Node 函数必须恰好有一个非 keyword-only 运行时参数，所有构建参数必须是 keyword-only：

```python
from slyme.context import Context, Ref, RefFactory
from slyme.node import Auto, node

refs = RefFactory({"input": {"x": ...}, "output": {"total": ...}})


@node
def add(ctx: Context, *, x: Auto[int], y: Auto[int], output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add(x=refs.input.x, y=2, output=refs.output.total)
```

运行时参数的名称与类型标注都不是必需的；Slyme 仅根据参数数量及 keyword-only 位置区分运行时参数和构建参数。
不支持变长参数 `*args` 和 `**kwargs`：运行时参数数量与构建参数名称都必须显式声明。

## 执行

自行管理 Context 时直接调用 Node：

```python
ctx = Context()
ctx.set(refs.input.x, 3)
result = task(ctx)  # 5
```

需要输入处理、`Arg` 校验、CLI 解析或输出提取时，以 `run()` 作为应用边界：

```python
result = task.run(inputs={refs.input.x: 3}, outputs=refs.output.total)
```

现在没有 Def/Exec 转换和 `prepare()` 阶段。每次调用都会校验并解析当前参数与 wrapper。

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

`Auto` 会递归解析已注册的 evaluator 叶子，例如 `Ref` 和 `Node`；没有 `Auto` 时，这些对象会原样传入。

缺失的必需构建参数以 `UNDEFINED` 表示，并在 Node 调用时被拒绝。使用 `UNSET` 可以显式请求声明的默认值。

## 动态修改

Node 和 Wrapper 参数是真实的实例属性，并在两次调用之间始终可变：

```python
root.child.value = 10
assert root(Context()) == 11
```

参数名不能与 `run`、`func`、`specs` 或 `wrappers` 等框架属性冲突。参数不能删除；应赋予其他值或 `UNDEFINED`。

调用时，静态参数容器会直接传给用户函数，因此对容器的修改会更新 Node 或 Wrapper 上的实时参数。包含 evaluator 叶子的 Auto 参数则会用解析结果重建。

需要显式结构隔离时使用 `clone()`：

```python
branch = root.clone()
branch.child.value = 20
assert root.child.value == 10
```

克隆会创建新的 Node、Wrapper 和已注册参数 PyTree 容器。未注册叶子仍然共享；如果业务对象也需要隔离，应由应用单独复制。

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

Wrapper 按洋葱模型组合，并在调用时读取实时参数。它们同样从 `NodeElement` 继承 `clone()`。

## Node 结构校验 {#node-struct}

`check_node_structure(root)` 检查物理 Node 图，并校验 wrapper 位置及同步/异步包含关系。除非显式关闭，`@builder` 会自动调用它。

## 顺序组合

同步声明式顺序组合使用 `sequential(nodes=[...])`，混合异步执行使用 `async_sequential(nodes=[...])`。对应的 `*_exec` helper 用于在高阶 Node 内执行已有 iterable。
