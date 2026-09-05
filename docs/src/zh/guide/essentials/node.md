# Node

`@node` 将函数转换为可变、可直接调用的 `Node` 对象工厂。Node 可以修改 `Context`、执行副作用、协调子 Node，也可以返回供 `Auto` 注入的派生值。

## 定义与创建 Node

Node 函数必须恰好有一个非 keyword-only 运行时参数，所有构建参数必须是 keyword-only：

```python
from slyme.context import Context, Ref, Schema
from slyme.node import Auto, node

R = Schema({"input": {"x": ...}, "output": {"total": ...}})


@node
def add(ctx: Context, *, x: Auto[int], y: Auto[int], output: Ref[int]):
    result = x + y
    ctx.set(output, result)
    return result


task = add(x=R.input.x, y=2, output=R.output.total)
```

运行时参数的名称与类型标注都不是必需的；Slyme 仅根据参数数量及 keyword-only 位置区分运行时参数和构建参数。
不支持变长参数 `*args` 和 `**kwargs`：运行时参数数量与构建参数名称都必须显式声明。

## 执行

自行管理 Context 时直接调用 Node：

```python
ctx = Context(schema=R)
ctx.set(R.input.x, 3)
result = task(ctx)  # 5
```

需要输入处理、`Arg` 校验、CLI 解析或输出提取时，以 `run()` 作为应用边界：

```python
result = task.run(inputs={R.input.x: 3}, outputs=R.output.total)
```

未传入 Context 时，`run()` 会根据 Node 图、`inputs` 和 `outputs` 中出现的 Ref 创建应用根。显式传入 Context 时不会改变其声明；调用方必须事先声明 Node 可能使用的所有 Ref。

现在没有 Def/Exec 转换和 `prepare()` 阶段。每次调用都会绑定并解析当前参数与 wrapper。

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

`Auto` 会递归解析已注册的 evaluator 叶子，例如 `Ref` 和 `Node`。Ref 会直接读取传入的 Context；每个产生值的子 Node 则分别获得独立的 `ctx.fork()`，因此它的局部 Context 写入不会泄漏到父级或其他 Auto 子 Node。返回值、对共享 leaf 对象的修改以及外部副作用并不会被隔离。没有 `Auto` 时，Ref 和 Node 对象会原样传入。

Node 渲染会展示当前参数值。参数边前的 `?` 表示该值会在 Node 运行时根据传入的 Context 求值。

缺失的必需构建参数以 `UNDEFINED` 表示，并在 Node 调用时被拒绝。使用 `UNSET` 可以显式请求声明的默认值。

## 动态修改

Node 和 Wrapper 参数是真实的实例属性，并在两次调用之间始终可变：

```python
root.child.value = 10
assert root(Context()) == 11
```

参数名不能与 `run`、`func`、`specs` 或 `wrappers` 等框架属性冲突。参数不能删除；应赋予其他值或 `UNDEFINED`。

调用时，静态参数容器会直接传给用户函数，因此对容器的修改会更新 Node 或 Wrapper 上的实时参数。包含 evaluator 叶子的 Auto 参数则会用解析结果重建。

需要另一张可独立配置的图时，应重新调用 Node factory 或 Builder。可变应用值是否共享或复制由其自身语义决定；Slyme 不会猜测哪些引用需要复制。

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

Wrapper 按洋葱模型组合，并在调用时读取实时参数。

## 组合结构

Node 与 Wrapper 参数可以保存任意值和嵌套 PyTree，包括其他 Node 或 Wrapper。Slyme 不会对整张对象图施加统一的合法性检查，只有对象实际参与执行时的角色受到约束：Wrapper 模式必须与其 Node 匹配，`sequential` 只接受同步 Node，`async_sequential` 则接受同步或异步 Node。

## 顺序组合

同步声明式顺序组合使用 `sequential(nodes=[...])`，混合异步执行使用 `async_sequential(nodes=[...])`。对应的 `*_exec` helper 会让已有 iterable 使用同一个 Context 执行；与 Auto 子 Node 不同，这些 helper 会有意让各步骤共享局部写入。
