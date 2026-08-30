# Builder

在 Slyme 中，随着业务逻辑变得复杂，通常会将多个 `@node` 和 `@wrapper` 组合成 Node 图。`@builder` 装饰器用于组织和复用这些组装逻辑。

简单来说，Builder 就是一个专门用来实例化和组装 Node 的工厂函数。

## @builder 装饰器

`@builder` 装饰器的核心职责是包裹你的组装逻辑，并在函数返回时进行一系列的安全检查，确保你构建出的是一棵合法、健壮的 Node 树。

### 基础用法

你可以像定义普通函数一样定义一个 Builder，只需要给它加上 `@builder` 装饰器：

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import R
# 假设有定义好的 nodes
# from my_nodes import load_data, process_data, save_data


@builder
def create_data_pipeline(source_path: str):
    # 1. 实例化各个 Node — 通过关键字参数直接传入 Ref
    load_node = load_data(path=source_path)
    process_node = process_data(config=R.process_config)
    save_node = save_data(output=R.output_path)

    # 2. 组装并返回一棵完整的 Node 树
    return sequential(nodes=[load_node, process_node, save_node])
```

调用 Builder 函数并不会执行 Node，它只执行内部组装逻辑并返回最外层的 Node 实例：

```python
pipeline = create_data_pipeline("/path/to/data")
# ctx = pipeline(ctx)
```

::: tip
`@builder` 会自动检查函数的返回值。如果你在编写复杂的分支逻辑时忘记了 `return`（导致返回 `None`），框架会抛出明确的 `ValueError` 异常，提醒你返回构建好的 Node 实例。
:::

### 结构校验

默认情况下，`@builder` 在返回 Node 之前，会自动调用内部的 `check_node_structure` 对整棵 Node 树进行深度的结构合法性校验。正如在 [Node 结构校验](/zh/guide/essentials/node#node-struct) 章节中提到的，Slyme 对不同类型 Node 的相互持有关系有严格的约束（例如 `@wrapper` 只能作为中间件挂载，不能被作为参数传递给 `@node` 等）。

如果在某些特殊场景下（比如在一个极其频繁被调用的内部子 Builder 中，出于性能考虑），你需要关闭这层结构校验，可以通过显式传入 `check_structure=False` 来实现：

```python
from slyme.builder import builder


@builder(check_structure=False)
def fast_internal_builder():
    # 这里返回的 Node 将跳过结构校验
    return load_data(path="...")
```

## 组合与动态修改

Builder 最大的优势在于**可复用性**。一个 Builder 可以调用另一个 Builder，返回的动态 Node 图可以在调用前或两次调用之间继续修改。

这在构建不同变体的流水线时非常有用，避免了大量重复的模板代码：

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import R


@builder
def base_pipeline():
    return sequential(
        nodes=[
            load_data(path="default_path"),
            process_data(config=R.default_config),
        ]
    )


@builder
def custom_pipeline(new_path: str):
    # 1. 获取基础的 Node 树
    pipeline = base_pipeline()

    # 2. 动态修改特定节点的构建期参数
    pipeline.nodes[0].path = new_path
    pipeline.nodes.append(save_data(output=R.output_path))
    return pipeline
```

通过这种方式，你可以将小型的 Builder 积木般地组合成大型的系统，同时保持极高的灵活性。
