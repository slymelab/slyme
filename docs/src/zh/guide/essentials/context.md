# Context

[Context](/zh/guide/essentials/context) 是 Slyme 在各个 [Node](/zh/guide/essentials/node) 之间传递状态的核心数据结构。它的行为非常类似于 Python 的字典（`dict`），但有两个关键的不同点：它是**层次化**的，并且是**结构不可变**的。

为了方便且类型安全地访问或修改 Context 中的深层嵌套数据，Slyme 引入了 [Ref](#ref) 的概念，它类似于访问字典的“键”，但支持多级路径。

## Ref {#ref}

`Ref` 是一个不可变的引用对象，用于在 Context 中定位数据。你可以将它理解为一条指向 Context 内部特定位置的路径。

### 创建 Ref

你可以通过传入一个点分字符串（Dotted string）来创建一个 `Ref`：

```python
from slyme.context import Ref

# 指向根层级下的 'user'
user_ref = Ref("user")

# 指向嵌套字典中的 'user.profile.name'
name_ref = Ref("user.profile.name")
```

::: tip 最佳实践
在实际使用中，推荐使用更简洁的 [`R` (RefFactory)](#reffactory) 简写：`R.user` 替代 `Ref("user")`，`R.user.profile.name` 替代 `Ref("user.profile.name")`。
:::

### 派生 Ref

`Ref` 提供了 `.at()` 方法，允许你基于当前路径快速派生出子路径：

```python
profile_ref = Ref("user.profile")
name_ref = profile_ref.at("name")  # 等价于 Ref("user.profile.name")
```

::: info
`Ref` 在内部会缓存哈希值和拆分后的路径片段（`parts`），因此在执行期频繁使用 `Ref` 进行查找时具有极高的性能。除此之外，`Ref` 还可以携带 `metadata` 等高级元数据，以支持命令行参数配置等功能。
:::

::: warning 已弃用
`Ref` 的 `key_path` 参数自 slyme 0.1.1 起**已弃用**，并将在 0.2.0 中移除。请改用 [`@expression`](/zh/guide/essentials/node#at-expression) + [`Auto`](/zh/guide/essentials/node#spec) 来实现动态值解析。详见下方 [Key Path](#key-path) 章节的迁移指南。
:::

### Key Path（已弃用） {#key-path}

::: warning 已弃用
`Ref.key_path` 以及 `slyme.utils.pytree` 中的 `CallKey`、`KeyPathExpr`、`P` 代理对象自 slyme 0.1.1 起**已弃用**，并将在 0.2.0 中移除。请改用 [`@expression`](/zh/guide/essentials/node#at-expression) + [`Auto`](/zh/guide/essentials/node#spec) 来实现动态值解析。
:::

**旧范式（已弃用）：**

```python
from slyme.utils.pytree import P

# 从 Context 中获取 model，再深入访问 model.config.hidden_size
ctx.get(Ref("model", key_path=tuple(P.config.hidden_size)))
```

**新范式 — 使用 `@expression` + `Auto`：**

```python
from slyme.node import expression, node, Auto
from slyme.context import Context, Ref

@expression
def get_hidden_size(ctx: Context, /, *, model: Auto[object]) -> int:
    return model.config.hidden_size

# 在需要 hidden_size 的 @node 中传入 expression：
@node
def my_node(ctx: Context, /, *, hidden_size: Auto[int]) -> Context:
    # hidden_size 已经被解析为实际的 int 值
    return ctx

# 将它们组合起来：
my_node(hidden_size=get_hidden_size(model=R.model))
```

这种方式让 Node 保持完全解耦 — Node 只需要声明它需要一个 `hidden_size` 参数，而无需关心这个值是如何计算得到的。

### RefFactory

::: tip 0.1.1 新增
`RefFactory` 是创建 `Ref` 对象的推荐方式，使用简洁的属性访问语法。
:::

Slyme 提供了一个全局的 `R` 实例（`RefFactory`）。使用 `R`，你可以通过点号记法创建 `Ref` 对象：

```python
from slyme.context import R

# 属性访问记录了点分路径：
# R.user.profile.name  记录了 "user.profile.name"

# 直接使用它 — 自动转换为 Ref：
name_ref = R.user.profile.name  # 等价于 Ref("user.profile.name")
```

`RefFactory` 是**不可变的** — 每次属性访问都会返回一个带有扩展路径的新 `RefFactory` 实例。它可以在任何需要 `Ref` 的地方使用（例如 `Context.get()`、`Context.set()` 或 Node 的关键字参数），会被自动转换为 `Ref`：

```python
from slyme.context import Context, R

ctx = Context().set(R.status, "active")

# 创建 Ref 时传递额外的元数据：
ref_with_meta = R.user.profile.name(metadata={"desc": "用户的显示名称"})
```

`RefFactory` 与 Node 实例化无缝集成，提供了已弃用 Scope 模式的简洁替代方案：

```python
from slyme.node import node
from slyme.context import Context, R, Ref

@node
def greet(ctx: Context, /, *, name: Ref[str], title: Ref[str]) -> Context:
    name_ = ctx.get(name)
    title_ = ctx.get(title)
    return ctx.set(R.greeting, f"{title_} {name_}")

# 直接在关键字参数中使用 R
node_def = greet(name=R.user.name, title=R.user.title)
```

::: info
在内部，`RefFactory` 会被透明地解析为 `Ref` — 任何接受 `Ref` 的 API 也通过 `RefLike` 类型别名接受 `RefFactory`。
:::

## Context

`Context` 是 Node 运行时的状态容器。基于 **Copy-On-Write（写时复制）** 机制，每次对 Context 的修改都不会改变原对象，而是返回一个全新的 Context 实例。这种设计从根本上保证了函数式编程的并发安全和状态可追溯性。

### 创建 Context

你可以通过 `update()` 方法来初始化一个 `Context`：

```python
from slyme.context import Context, R

ctx = Context().update({
    R.user.profile.name: "Alice",
    R.user.profile.age: 25,
    R.status: "active",
})
```

打印 `ctx`，你会得到：

```text
Context({
    'user': ContextView({
        'profile': ContextView({
            'name': 'Alice',
            'age': 25,
        }),
    }),
    'status': 'active',
})
```

其中，`ContextView` 指的是 `Context` 的内部结构化视图，用于表示层次化的 `Context` 结构。

### 读取数据

使用 `get()` 方法并传入 `Ref` 来获取数据。如果路径不存在，你可以提供一个默认值，否则会抛出 `ContextPathError` 异常：

```python
# 获取顶层数据
status = ctx.get(R.status)  # 'active'

# 获取深层嵌套数据
name = ctx.get(R.user.profile.name)  # 'Alice'

# 获取不存在的数据时提供默认值
email = ctx.get(R.user.profile.email, default="unknown")
```

另外，你可以使用 `extract()` 方法来实现更高级的结构化读取，支持任意嵌套的 Python 字典、列表、元组：

```python
profiles = ctx.extract([
    {
        "age": R.user.profile.age,
        "name": R.user.profile.name,
        "status": R.status,
    }
])
```

上述例子中，得益于 PyTree 引擎，Slyme 会把嵌套的字典/列表/元组结构中的 Ref 全部解析成对应的值，并且保持原有的结构不变。`profiles` 值应该是：

```text
[{'age': 25, 'name': 'Alice', 'status': 'active'}]
```

::: warning
请注意，`ctx.extract()` 方法要求每一个叶子的值都是 `Ref` 对象，不允许混合普通值，比如 `ctx.extract([R.status, 123])` 这样是不允许的。如果想要解析混合的结构，你应该使用更高级的 eval API（详见[依赖注入](/zh/guide/slyme-in-depth/dependency-injection)）：

```python
from slyme.node.eval import eval_tree

# NOTE: 123 的值不会被解析，保持原样
eval_tree(ctx, [R.status, 123])  # ['active', 123]
```
:::

其他常用的读取方法：

```python
# 检查路径是否存在。
ctx.exists(R.user.profile)  # True
ctx.exists(R.user.profile.email)  # False

# 列出指定层级下的所有键（类似于字典的 `keys()`）
ctx.keys(R.user.profile)  # dict_keys(['name', 'age'])

# 将 Context 递归转换为普通的 Python 字典。
ctx.to_dict()  # {'user': {'profile': {'name': 'Alice', 'age': 25}}, 'status': 'active'}
```

### 修改数据 (Copy-On-Write)

由于 Context 是结构不可变的，所有的修改方法都会**返回一个新的 Context 实例**。底层的 Copy-On-Write 算法会智能地复用未修改的子树内存，确保修改操作不仅安全而且高效。

```python
# 单个设置 (set)
new_ctx = ctx.set(R.status, "inactive")
# ctx 保持不变，new_ctx 中的 status 变为 inactive

# 批量更新 (update)
new_ctx = ctx.update({
    R.user.profile.age: 26,
    R.user.profile.email: "alice@example.com"
})

# 删除 (delete)
new_ctx = ctx.delete(R.user.profile.age)
```

### 原子化事务 (mutate)

如果你需要同时进行复杂的更新和删除操作，可以使用更底层的 `mutate()` 方法。它保证了所有的修改在一次遍历中原子化完成：

```python
new_ctx = ctx.mutate(
    updates={
        R.user.profile.status: "verified"
    },
    drops=[
        R.status # 删除顶层 status
    ]
)
```

### 比较 Context (diff)

你可以使用 `.diff()` 方法比较两个 Context 对象之间的差异。它会返回一个 `ContextDiff` 对象，包含了新增、删除和修改的详细信息。这在调试、状态监控或编写测试用例时非常有用：

```python
new_ctx = ctx.mutate(
    updates={
        R.user.profile.status: "verified",
        R.user.profile.name: "Bob",
    },
    drops=[
        R.status
    ]
)
diff = new_ctx.diff(ctx)
# 你可以使用 diff.flatten() 将差异展平为一个字典
print(diff.flatten())  # {'status': (<DiffMissing.MARK: 1>, 'active'), 'user.profile.status': ('verified', <DiffMissing.MARK: 1>), 'user.profile.name': ('Bob', 'Alice')}
```

其中，`slyme.context.DIFF_MISSING` 表示缺失值。`diff.flatten()` 返回的字典中，tuple 的第一个元素是新值，第二个元素是旧值。这就意味着，`DIFF_MISSING` 出现在第一个位置，表示值被删除；出现在第二个位置，表示值被新增；否则，表示值被修改。

## 异步支持（已弃用） {#async-support}

::: warning 已弃用
`Context` 和 `ContextView` 上的所有 `async_*` 方法自 slyme 0.1.1 起**已弃用**，并将在 0.2.0 中移除，包括 `async_get`、`async_extract`、`async_to_dict`、`async_mutate`、`async_update`、`async_drop`、`async_set`、`async_update_tree`、`async_delete` 和 `async_clear`。Context 本身是**本地存储且同步的** — 请直接使用对应的同步方法替代。
:::

**旧范式（已弃用）：**

```python
value = await ctx.async_get(Ref("path"))
data = await ctx.async_to_dict()
new_ctx = await ctx.async_mutate(updates={...}, drops=[...])
```

**新范式 — 统一使用同步方法：**

```python
value = ctx.get(R.path)
data = ctx.to_dict()
new_ctx = ctx.mutate(updates={...}, drops=[...])
```

这些同步方法可以在同步和异步 Node 中正常工作 — 无需 `await`。

## Context Hook（已弃用）

::: warning 已弃用
`Hook`、`HookChain` 以及 `Context.__init__()` 的 `hook=` 参数自 slyme 0.1.1 起**已弃用**，并将在 0.2.0 中移除。它们目前仅为迁移而暂时保留，不应继续用于新代码。如果你需要数据转换或拦截功能，请改用 [`@wrapper`](/zh/guide/essentials/node#at-wrapper) 节点在 Node 层面拦截执行。
:::
