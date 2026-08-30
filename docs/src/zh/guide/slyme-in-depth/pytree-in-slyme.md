# Slyme 中的 PyTree

PyTree 是一种嵌套结构：容器描述拓扑，未注册对象作为叶子。Slyme 为不同语义使用不同引擎，而不假定所有遍历都应追踪同一组对象。

## `NODE_ENGINE`

`NODE_ENGINE` 注册 `Node`、`Wrapper`、它们的异步版本以及普通容器，用于物理图检查、渲染、校验和 Ref 收集。由于它能够遍历 Node 关系，引入逻辑 Slot 图后，执行整图分析的调用方需要明确自己的环处理策略。

## 结构克隆

`NodeElement.clone()` 会在 `NODE_ENGINE` 上映射恒等函数。反扁平化过程重建每个已注册的 Node、Wrapper 与普通参数容器，同时保留未注册叶子对象。因此结果拥有独立的物理 Node/PyTree 结构，但不会任意深拷贝应用值。

`ContextElement.clone()` 使用只将 `Context` 与 `ContextData` 视为容器的 `CONTEXT_ENGINE`。它会重建 ContextData 层次，但保留已存储 list、dict、模型对象及其他叶子的身份。克隆 `ContextView` 会生成以该子树为根的独立 Context。

两种操作都沿已注册的树边遍历，并要求 PyTree 无环。同一个已注册容器出现在多个路径时，克隆不会保留其别名身份。

## Auto 求值

Auto 使用 Context 求值引擎解析 `Ref`、`Node` 等已注册叶子，普通叶子保持不变。静态参数容器会直接传递；动态 Auto 树则会用求值后的叶子重建。
