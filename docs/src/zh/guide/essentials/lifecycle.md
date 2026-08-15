# 生命周期 (Lifecycle)

Slyme 的 Node 体系采用了严格的**两阶段架构**（构建期 Def 与执行期 Exec）。这种设计从根本上解耦了逻辑的“组装”与“运行”，在保证高度灵活性的同时，也为执行期的安全性和性能优化提供了坚实的保障。

一个 Node 从创建到最终运行，通常会经历四个完整的生命周期阶段：**构建 (Build)** -> **修改 (Modify)** -> **准备 (Prepare)** -> **执行 (Execute)**。

## 1. 构建阶段 (Build)

当你调用一个被 `@node`、`@expression` 或 `@wrapper` 装饰的函数，并传入配置参数时，你并没有执行这段业务逻辑，而是实例化了一个**可变的定义对象（Def）**。

```python
from slyme.node import node, Auto
from slyme.context import Context, R

@node
def process_data(ctx: Context, /, *, timeout: int = 30, data: Auto[list], max_len: int) -> Context:
    return ctx

# 构建阶段：实例化 Def 对象
my_node_def = process_data(max_len=1024)  # 此时 data 为 UNDEFINED
```

在这个阶段，Slyme 会进行初步的参数收集，并处理 `UNSET`（显式触发默认值逻辑）等标记常量。此时如果某些必须提供的参数未提供，可以留到修改阶段再进行赋值。

为了更好地管理构建逻辑，我们通常推荐使用 [`@builder`](/zh/guide/essentials/builder) 来组装复杂的 Node 树。

## 2. 动态修改阶段 (Modify)

在 Def 对象调用 `.prepare()` 之前，它是**完全可变**的。Slyme 允许你使用 `[]` 操作符来动态访问和深层修改它的构建期参数。

这赋予了系统极大的灵活性。比如，你可以基于一个现有的 Builder 产物进行微调，而无需重新编写大量重复的代码：

```python
# 动态修改阶段：微调 Def 参数
my_node_def["timeout"] = 60
my_node_def["data"] = [R.user.age, R.user.name]
```

## 3. 准备阶段 (Prepare)

当 Node 树的结构和参数修改完成后，`.prepare()` 标志着 Node 从“构建期”进入“执行期”，并返回一个**不可变的执行对象（Exec）**。应用层的 `run()` 会自动完成这一步；只有在需要复用 Exec 或自行管理 Context 时，才需要显式调用 `.prepare()`。

```python
# 准备阶段：将 Def 转换为不可变的 Exec
my_node_exec = my_node_def.prepare()
```

在这个阶段，Slyme 框架内部会进行一些检查和优化：
1. **结构与参数校验**：检查所有的 `UNDEFINED` 是否都已被正确赋值。
2. **参数冻结 (Parameter Freezing)**：这是为了执行期安全性与性能优化所做的关键操作。
3. **性能优化**：
    - 对于 @node，Slyme 会将其挂载的 @wrapper 与它本身结合成链式函数（洋葱模型），并缓存下来。
    - Slyme 会深度分析构建期参数，将其中需要自动求值的部分预分析并缓存，以提高运行时性能（自动求值的深入原理可见[依赖注入](/zh/guide/slyme-in-depth/dependency-injection)）。
    - 由于 `.prepare()` 方法调用之后的产物（Exec 对象）是不可变的，Slyme 会在未来的版本考虑加入更激进的优化（比如 JIT）以进一步提高运行效率。

::: tip 参数冻结机制
在 `.prepare()` 被调用时，Slyme 内部的 Prepare Engine 会对 Node 接收到的参数进行深度遍历和结构优化（冻结）。**用户在构建阶段传入的可变结构（如 `list` 或 `dict`），在 prepare 后会被转换为不可变结构（如 `tuple` 或 `MappingProxyType` 等）。**

这意味着一旦 prepare 完成，Node 的参数结构就彻底锁定了，无法再被意外修改，从而保证了执行期的高效和并发安全。
:::

## 4. 执行阶段 (Execute)

在底层执行边界，将 `Context` 传入 Exec 对象，触发业务逻辑：

```python
# 执行阶段：传入 Context 执行业务逻辑
new_ctx = my_node_exec(Context())
```

普通应用调用应优先使用 `node.run(inputs=..., outputs=...)`；它会统一完成输入准备、required `Arg` 校验、prepare、执行和输出提取。

在这个阶段，Slyme 最强大的特性之一——**Auto 自动求值与注入**——将会发挥作用。框架会深度遍历被冻结的参数结构，将其中的 Ref 和 @expression 解析为 Context 中的真实值，并注入给目标函数。

## Auto 自动注入的易混淆点

由于**准备阶段的参数冻结**机制，结合 **Auto 自动注入**，有一个容易混淆的场景需要特别注意：**注入可变结构的差异**。

请看以下两个场景对比：

```python
from typing import Any
from slyme.node import node, Auto
from slyme.context import Context, Ref

@node
def process(ctx: Context, /, *, data: Auto[Any]) -> Context:
    print(f"Type: {type(data)}, Value: {data}")
    return ctx
```

**场景 A：在构建时拼接了包含 `Ref` 的 `list`**

```python
# 1. 构建：传入一个 Python list，内部包含多个 Ref
node_def_a = process(data=[R.a, R.b])

# 2. Prepare：由于参数冻结机制，内部的 list 结构被转换成了 tuple！
exec_a = node_def_a.prepare()
# 此时 exec_a 内部持有的 data 参数结构实际上是: (R.a, R.b)

# 3. 执行：Auto 自动求值引擎解析这个 tuple，并注入最终值
ctx_a = Context().update({R.a: 1, R.b: 2})
exec_a(ctx_a)
# 打印输出 => Type: <class 'tuple'>, Value: (1, 2)
```

**场景 B：构建时直接传入一个指向 `list` 的 `Ref`**

```python
# 1. 构建：传入一个单独的 Ref
node_def_b = process(data=R.my_list)

# 2. Prepare：Ref 本身是不可变的叶子节点，保持不变
exec_b = node_def_b.prepare()
# 此时 exec_b 内部持有的 data 参数结构仍然是: R.my_list

# 3. 执行：Auto 自动求值引擎解析这个 Ref，直接取出 Context 中的值并注入
ctx_b = Context().set(R.my_list, [1, 2])
exec_b(ctx_b)
# 打印输出 => Type: <class 'list'>, Value: [1, 2]
```

**总结：**
- 如果你在**构建期**拼接了一个列表（如 `[Ref(...), Ref(...)]`），经过 prepare 冻结后，执行期你得到的将是一个 `tuple`。
- 如果你仅仅是传递了一个 `Ref`，而这个 `Ref` 在 Context 中指向一个原始的 `list`，那么执行期你得到的就是原汁原味的 `list`。
- 上述两个行为差异与 Slyme 的核心思想对应：Node 结构及其配置参数在运行时不可变，而 Context 内存储的用户数据则不做限制。
