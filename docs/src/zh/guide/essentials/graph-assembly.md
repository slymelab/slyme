# 图的组装

使用普通 Python 函数组装和复用 Node 图。函数可以返回 `Node`、`AsyncNode` 或图元素的集合，不需要专门的组装装饰器。

## 基础用法

假设应用已定义 `load_data`、`process_data` 和 `save_data`，可以通过函数组装顺序执行的流水线：

```python
from slyme.node import Node, sequential
from slyme.context import Schema
# 假设有定义好的 nodes
# from my_nodes import load_data, process_data, save_data

R = Schema({"process_config": Schema.leaf(), "output_path": Schema.leaf()})


def create_data_pipeline(source_path: str) -> Node[None]:
    # 1. 实例化各个 Node — 通过关键字参数直接传入 Ref
    load_node = load_data(path=source_path)
    process_node = process_data(config=R.resolve("process_config"))
    save_node = save_data(output=R.resolve("output_path"))

    # 2. 组装并返回一棵完整的 Node 树
    return sequential(nodes=[load_node, process_node, save_node])
```

这个函数创建 Node，但不执行它们。准备就绪后，将 Context 传给返回的流水线：

```python
pipeline = create_data_pipeline("/path/to/data")
# pipeline(ctx)
```

使用返回值类型注解和静态类型检查器检查组装函数。

## 组合与动态修改

组装函数可以互相调用。返回的 Node 图保持可变，因此函数可以创建基础流水线，再进行定制：

```python
from slyme.node import Node, sequential
from slyme.context import Schema

R = Schema({"default_config": Schema.leaf(), "output_path": Schema.leaf()})


def base_pipeline() -> Node[None]:
    return sequential(
        nodes=[
            load_data(path="default_path"),
            process_data(config=R.resolve("default_config")),
        ]
    )


def custom_pipeline(new_path: str) -> Node[None]:
    # 1. 获取基础的 Node 树
    pipeline = base_pipeline()

    # 2. 动态修改特定节点的构建期参数
    pipeline.get("nodes")[0].set("path", new_path)
    pipeline.get("nodes").append(save_data(output=R.resolve("output_path")))
    return pipeline
```

每次调用 `base_pipeline()` 都会创建新的 Node 和列表，修改其中一条流水线不会改变另一条。传入多个图的应用值仍然共享，除非显式复制。

组装函数和参数结构都可以自由嵌套。执行 API 仍有明确的类型约定：Wrapper 模式必须与其 Node 匹配，`sequential` 接受同步 Node，`async_sequential` 接受同步或异步 Node。
