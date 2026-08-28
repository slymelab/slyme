# Slyme 中的 PyTree

PyTree 是一种嵌套结构：容器描述拓扑，未注册对象作为叶子。Slyme 为不同语义使用不同引擎，而不假定所有遍历都应追踪同一组对象。

## `NODE_ENGINE`

`NODE_ENGINE` 注册 `Node`、`Wrapper`、它们的异步版本以及普通容器，用于物理图检查、渲染、校验和 Ref 收集。由于它能够遍历 Node 关系，引入逻辑 Slot 图后，执行整图分析的调用方需要明确自己的环处理策略。

## `NODE_SNAPSHOT_ENGINE`

每次 Node 和 Wrapper 调用都会使用一个只注册普通 Python 容器的小型快照引擎：

- `list` 转为 `tuple`；
- `tuple` 保持为 `tuple`；
- `dict` 转为 `MappingProxyType`；
- 已有 `MappingProxyType` 继续保持只读。

Node、Wrapper 与未来的 Slot 对象都未注册，因此会作为叶子。该引擎能够递归冻结参数数据，但不会沿组合边遍历，也不会陷入 Node 图的环。

生成的快照只服务于一次调用。动态 Node 图仍然可变，后续调用会重新创建快照。

## Auto 求值

局部快照创建后，Auto 使用 Context 求值引擎进行解析。`Ref`、`Node` 等已注册叶子会结合当前 `Context` 求值，普通叶子保持不变；从 Context 取得的求值结果不会再次被冻结。
