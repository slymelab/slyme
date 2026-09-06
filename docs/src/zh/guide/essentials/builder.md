# Builder

在 Slyme 中，随着业务逻辑变得复杂，通常会将多个 `@node` 和 `@wrapper` 组合成 Node 图。`@builder` 装饰器用于组织和复用这些组装逻辑。

简单来说，Builder 就是一个专门用来实例化和组装 Node 的工厂函数。

## @builder 装饰器

`@builder` 装饰器用于标记可复用的 Node 组装逻辑。它只检查最外层结果是否为 `Node` 或 `AsyncNode`，不会检查返回对象内部的参数图。

### 基础用法

你可以像定义普通函数一样定义一个 Builder，只需要给它加上 `@builder` 装饰器：

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import Schema, ref
# 假设有定义好的 nodes
# from my_nodes import load_data, process_data, save_data

R = Schema({"process_config": ref(), "output_path": ref()})


@builder
def create_data_pipeline(source_path: str):
    # 1. 实例化各个 Node — 通过关键字参数直接传入 Ref
    load_node = load_data(path=source_path)
    process_node = process_data(config=R.resolve("process_config"))
    save_node = save_data(output=R.resolve("output_path"))

    # 2. 组装并返回一棵完整的 Node 树
    return sequential(nodes=[load_node, process_node, save_node])
```

调用 Builder 函数并不会执行结果，它只执行内部组装逻辑并返回最外层的 `Node` 或 `AsyncNode` 实例：

```python
pipeline = create_data_pipeline("/path/to/data")
# ctx = pipeline(ctx)
```

::: tip
`@builder` 会检查函数的直接返回值。遗漏 `return` 时会抛出明确的 `ValueError`；返回其他非 `Node`、非 `AsyncNode` 的值时则抛出 `TypeError`。这只是局部的根节点检查，不是递归图校验。
:::

## 组合与动态修改

Builder 最大的优势在于**可复用性**。一个 Builder 可以调用另一个 Builder，返回的动态 Node 图可以在调用前或两次调用之间继续修改。

这在构建不同变体的流水线时非常有用，避免了大量重复的模板代码：

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import Schema, ref

R = Schema({"default_config": ref(), "output_path": ref()})


@builder
def base_pipeline():
    return sequential(
        nodes=[
            load_data(path="default_path"),
            process_data(config=R.resolve("default_config")),
        ]
    )


@builder
def custom_pipeline(new_path: str):
    # 1. 获取基础的 Node 树
    pipeline = base_pipeline()

    # 2. 动态修改特定节点的构建期参数
    pipeline.get("nodes")[0].set("path", new_path)
    pipeline.get("nodes").append(save_data(output=R.resolve("output_path")))
    return pipeline
```

Builder 函数和普通参数结构都可以自由嵌套。局部检查只针对实际执行角色：Wrapper 模式必须与其 Node 匹配，`sequential` 只接受同步 Node，`async_sequential` 则接受同步或异步 Node。
