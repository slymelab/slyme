# Slyme 是什么？

Slyme (发音为 /slaɪm/) 是一个高度可组合的函数式执行框架。就像递归命名法所展现的那样（**S**LYME **L**ets **Y**ou **M**old **E**verything），它使开发人员能够基于简单的、可复用的函数来无缝构建任意复杂的执行流程，并且无需掌握繁琐的 API 或语法。

::: tip
只是想试用一下 Slyme? 跳转至[快速上手](/zh/guide/intro/quick-start).
:::

## 核心优势

- **原生的 Python 开发体验**: Slyme 没有繁重的面向对象的样板代码，也没有陡峭的学习曲线，你只需要掌握 Python 的一些基础概念，包括 Python 函数和一些原生的数据结构（如字典、列表、元组等），以及理解一点核心概念之后便可快速上手。
- **无限可组合性**: Slyme 的基本执行单元是 [Node](/zh/guide/essentials/node)，它负责执行一个用户自定义的函数。Node 支持**无限组合**，一个 Node 既可以包含其他 Node，也可以被其他 Node 所包含。有趣的是，受到 PyTree 的增强，这种包含关系可以直接通过原生的 Python 数据结构来表示，比如列表或字典。无限组合使得 Slyme 能够构建出任意复杂的执行流程，并且 Node 之间可实现完全解耦。
- **函数式/并发安全**: Slyme 被设计成一个函数式的执行框架，其中 Node 之间负责交互的“状态”（即 [Context](/zh/guide/essentials/context)）是**结构不可变的**，这使得并发执行下的状态管理更加简单和安全。
- **无缝协作**: Slyme 的 Node 设计是高度解耦的，这些 Node 之间通过 Context 进行通信，这使得社区/开发团队可以独立地开发各自的功能、进行单元测试，减少了开发过程中的代码冲突，让开发者专注于逻辑的实现，而不是被深层的系统耦合和琐碎的“胶水代码”所束缚。

## 核心概念

为了理解 Slyme 的工作原理，你需要理解以下几个核心概念：

### Context

[Context](/zh/guide/essentials/context) 是 Slyme 的核心数据结构，它的行为类似于 Python 中的字典（dict），区别是它是**层次化**的、**结构不可变**的。[Ref](/zh/guide/essentials/context#ref) 是用于访问/修改 Context 的类，类似于 Python 字典的 key，你可以通过 Ref 访问 `a.b` 的路径，也可以修改 `c.d` 的值。每次对 Context 的修改都会返回一个新的 Context 对象，Context 内部通过 **Copy-On-Write** 机制来复用结构、提高运行效率。

### Node

如前面提到的，[Node](/zh/guide/essentials/node) 是 Slyme 的基本执行单元，它负责执行一个用户自定义的函数。根据 Node 的类型，它可以分为以下几种：

- [**@node**](/zh/guide/essentials/node#at-node): Slyme 的基本执行单元，它负责接受一个 Context 对象，执行用户函数，并且返回一个新的 Context 对象（如果没有对 Context 进行修改，也可直接返回原始 Context 对象）。在这个过程中，用户函数从 Context 获取值，执行自定义的逻辑，将执行的结果写入到 Context 中并返回，类似于传统函数接受输入参数、执行逻辑、返回结果的过程。
- [**@expression**](/zh/guide/essentials/node#at-expression): 与 @node 的行为相似，@expression 的用户函数从 Context 获取值，对值进行一些处理，然后**直接返回这个值本身**（而不是 Context 对象），这个行为类似于 Python 的 @property（或者是 Vue 的 computed 属性）。
- [**@wrapper**](/zh/guide/essentials/node#at-wrapper): @wrapper  的作用类似于中间件，它负责拦截、修改或增强 Node 的执行过程，例如在 @node 执行前或执行后添加日志、记录性能、处理异常等。

::: info
@node 和 @wrapper 同时支持普通函数与 `async def` 函数。
:::

### Builder

[@builder](/zh/guide/essentials/builder) 用于组装 Node 结构，用户可以在自定义函数内构建任意的 Node 流程，然后交由 @builder 进行自动结构检查。一个 @builder 函数可以调用其他任意 @builder 函数，以更灵活地组织构建过程。

### 构建期/执行期分离

在 Slyme 中，构建期和执行期是两个不同的概念，它们分别对应于不同的时间点（详见[生命周期](/zh/guide/essentials/lifecycle)）。

- **构建期**: 在构建期，用户组装 Node 结构，并且可以对 Node 结构进行任意修改。
- **执行期**: 在执行期，构建好的 Node 树会被转换为固定的执行结构，以便安全、高效地执行。高层 `.run()` 会自动完成这一步；`.prepare()` 则用于显式获取可在底层复用的不可变 Exec。
