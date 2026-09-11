# Slyme 中的 PyTree

PyTree 是一种嵌套结构：容器描述拓扑，未注册对象作为叶子。Slyme 为不同语义使用不同引擎，而不假定所有遍历都应追踪同一组对象。

## `NODE_ENGINE`

`NODE_ENGINE` 注册 `Node`、`Wrapper`、它们的异步版本以及普通容器，用于物理图检查和 Ref 收集。Node 参数可以包含异质的嵌套值；该引擎只描述遍历方式，不判断组合是否合法。

## 对象 identity

Slyme 不提供通用的 Node 图 clone。PyTree 重建无法决定哪些共享引用应继续作为别名、哪些 value 应被复制，以及循环应用图应如何处理。需要另一张图时，应重新调用对应的 Node factory 或 组装函数，并由应用显式复制所需 value。

Context 不会被注册为 PyTree container。它是最多拥有一个 parent、带 identity 的生命周期 owner，而不是自包含的值树；绑定的 Scope 承载独立的 C3 可见性图。需要显式物化时，`Context.flatten()` 会提供其可见的 Ref 到 value 映射。

## Auto 求值

Auto 使用 `CTX_EVAL_ENGINE` 查找已注册 leaf，并把 Context 本身视为不透明对象。Ref 读取当前 Context；每个子 Node 在由父级管理、绑定到独立 child Scope 的 Context 中求值。Slyme dispose 该 Context 后，再使用返回值重建动态 Auto tree。普通 leaf 保持不变。
