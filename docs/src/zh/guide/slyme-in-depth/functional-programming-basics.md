# 函数式编程基础

Slyme 选择性采用函数式思想，而不要求整个运行时都不可变。

- Node 用户函数显式接收运行时 `Context` 与构建参数。
- Node 可以返回任意值，包括供高阶执行使用的临时控制信息。
- Context 数据原地可变；普通修改返回 `None`，由生命周期管理的 `add()`、`declare()` 和 `contribute()` 则返回精确的提前 disposer。
- Node 和 Wrapper 调用会直接传递当前静态参数容器。
- 动态组合图仍然可变，因此后续调用可以观察结构修改。
- 结构隔离是显式的：需要另一张图时重新调用 Node factory 或 Builder，需要实时局部数据层时把 fork 出的 Context 绑定到 child Scope；普通 `Context.fork()` 会共享 parent 的 Scope。

纯度仍由应用决定：产生值的 Node 可以是纯函数，I/O Node 可以执行副作用，高阶 Node 可以协调子 Node。Slyme 负责描述它们的组合与生命周期，而不试图建模这些 Python 操作的内部细节。

通过 `Auto` 求值的每个子 Node 都会获得由父级管理、绑定到独立 child Scope 的 Context，Slyme 会在继续执行前 dispose 它。显式并发编排需要隔离局部写入时，也应为每个分支创建 child Scope。一棵 Context 树及其使用的可变 Schema 和 Compose 对象只归属于一个线程和一个事件循环。显式提交到线程或进程的工作应只处理普通值，并在返回结果后由 owner 线程修改 Context。fork 会保留应用 leaf 的 identity，也不会撤销没有注册 cleanup 的外部副作用。
