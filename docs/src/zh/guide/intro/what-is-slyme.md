# Slyme 是什么？

Slyme (发音为 /slaɪm/) 是一个高度可组合的函数式执行框架。就像递归命名法所展现的那样（**S**LYME **L**ets **Y**ou **M**old **E**verything），它使开发人员能够基于简单的、可复用的函数来无缝构建任意复杂的执行流程，并且无需掌握繁琐的 API 或语法。

::: tip
只是想试用一下 Slyme? 跳转至[快速上手](/zh/guide/intro/quick-start).
:::

## 核心优势

- **原生的 Python 开发体验**: Slyme 没有繁重的面向对象的样板代码，也没有陡峭的学习曲线，你只需要掌握 Python 的一些基础概念，包括 Python 函数和一些原生的数据结构（如字典、列表、元组等），以及理解一点核心概念之后便可快速上手。
- **无限可组合性**: Slyme 的基本执行单元是 [Node](/zh/guide/essentials/node)，它负责执行一个用户自定义的函数。Node 支持**无限组合**，一个 Node 既可以包含其他 Node，也可以被其他 Node 所包含。有趣的是，受到 PyTree 的增强，这种包含关系可以直接通过原生的 Python 数据结构来表示，比如列表或字典。无限组合使得 Slyme 能够构建出任意复杂的执行流程，并且 Node 之间可实现完全解耦。
- **显式状态边界**：Context 数据可变，以支持高效的长生命周期工作流。并发分支需要隔离时由调用方显式复制，同时每次 Node 调用都获得稳定的参数快照。
- **无缝协作**: Slyme 的 Node 设计是高度解耦的，这些 Node 之间通过 Context 进行通信，这使得社区/开发团队可以独立地开发各自的功能、进行单元测试，减少了开发过程中的代码冲突，让开发者专注于逻辑的实现，而不是被深层的系统耦合和琐碎的“胶水代码”所束缚。

## 核心概念

为了理解 Slyme 的工作原理，你需要理解以下几个核心概念：

### Context

[Context](/zh/guide/essentials/context) 是 Slyme 的层次化运行时数据存储。它的外层属性保持稳定，内部数据原地可变。[Ref](/zh/guide/essentials/context#ref) 用于标识 `a.b` 等路径；并发分支需要隔离数据时应显式复制 Context。

### Node

如前面提到的，[Node](/zh/guide/essentials/node) 是 Slyme 的基本执行单元：

- [**@node**](/zh/guide/essentials/node)：运行时接收 Context，可以修改 Context、执行副作用、协调子 Node，或返回派生值。
- [**@wrapper**](/zh/guide/essentials/node#wrapper)：用于拦截或增强 Node 执行，例如 tracing、计时、重试或异常处理。

::: info
@node 和 @wrapper 同时支持普通函数与 `async def` 函数。
:::

### Builder

[@builder](/zh/guide/essentials/builder) 用于组装 Node 结构，用户可以在自定义函数内构建任意的 Node 流程，然后交由 @builder 进行自动结构检查。一个 @builder 函数可以调用其他任意 @builder 函数，以更灵活地组织构建过程。

### 动态图与调用局部快照

Slyme 在组装与执行期间始终使用同一个可变 Node 图（详见[生命周期](/zh/guide/essentials/lifecycle)）。

- **调用之间**：用户可以组装或修改 Node 参数、wrapper 与组合结构。
- **单次调用内**：当前 Node 将普通 Python 容器冻结为局部不可变快照，使用传入的 Context 解析动态值，并基于该快照执行；图的后续修改只影响后续调用。
