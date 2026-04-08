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

### 派生 Ref

`Ref` 提供了 `.at()` 方法，允许你基于当前路径快速派生出子路径：

```python
profile_ref = Ref("user.profile")
name_ref = profile_ref.at("name")  # 等价于 Ref("user.profile.name")
```

::: info
`Ref` 在内部会缓存哈希值和拆分后的路径片段（`parts`），因此在执行期频繁使用 `Ref` 进行查找时具有极高的性能。除此之外，`Ref` 还可以携带 `metadata` 和 `key_path`（用于 [PyTree](/zh/guide/slyme-in-depth/pytree-in-slyme) 解析）等高级元数据，以支持命令行参数配置等功能。
:::

## Context

`Context` 是 Node 运行时的状态容器。基于 **Copy-On-Write（写时复制）** 机制，每次对 Context 的修改都不会改变原对象，而是返回一个全新的 Context 实例。这种设计从根本上保证了函数式编程的并发安全和状态可追溯性。

### 创建 Context

你可以通过 `update()` 方法来初始化一个 `Context`：

```python
from slyme.context import Context, Ref

ctx = Context().update({
    Ref("user.profile.name"): "Alice",
    Ref("user.profile.age"): 25,
    Ref("status"): "active",
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
status = ctx.get(Ref("status"))  # 'active'

# 获取深层嵌套数据
name = ctx.get(Ref("user.profile.name"))  # 'Alice'

# 获取不存在的数据时提供默认值
email = ctx.get(Ref("user.profile.email"), default="unknown")
```

另外，你可以使用 `extract()` 方法来实现更高级的结构化读取，支持任意嵌套的 Python 字典、列表、元组：

```python
profiles = ctx.extract([
    {
        "age": Ref("user.profile.age"),
        "name": Ref("user.profile.name"),
        "status": Ref("status"),
    }
])
```

上述例子中，得益于 PyTree 引擎，Slyme 会把嵌套的字典/列表/元组结构中的 Ref 全部解析成对应的值，并且保持原有的结构不变。`profiles` 值应该是：

```text
[{'age': 25, 'name': 'Alice', 'status': 'active'}]
```

::: warning
请注意，`ctx.extract()` 方法要求每一个叶子的值都是 `Ref` 对象，不允许混合普通值，比如 `ctx.extract([Ref("status"), 123])` 这样是不允许的。如果想要解析混合的结构，你应该使用更高级的 eval API（详见[依赖注入](/zh/guide/slyme-in-depth/dependency-injection)）：

```python
from slyme.node.eval import eval_tree

# NOTE: 123 的值不会被解析，保持原样
eval_tree(ctx, [Ref("status"), 123])  # ['active', 123]
```
:::

其他常用的读取方法：

```python
# 检查路径是否存在。
ctx.exists(Ref("user.profile"))  # True
ctx.exists(Ref("user.profile.email"))  # False

# 列出指定层级下的所有键（类似于字典的 `keys()`）
ctx.keys(Ref("user.profile"))  # dict_keys(['name', 'age'])

# 将 Context 递归转换为普通的 Python 字典。
ctx.to_dict()  # {'user': {'profile': {'name': 'Alice', 'age': 25}}, 'status': 'active'}
```

### 修改数据 (Copy-On-Write)

由于 Context 是结构不可变的，所有的修改方法都会**返回一个新的 Context 实例**。底层的 Copy-On-Write 算法会智能地复用未修改的子树内存，确保修改操作不仅安全而且高效。

```python
# 单个设置 (set)
new_ctx = ctx.set(Ref("status"), "inactive")
# ctx 保持不变，new_ctx 中的 status 变为 inactive

# 批量更新 (update)
new_ctx = ctx.update({
    Ref("user.profile.age"): 26,
    Ref("user.profile.email"): "alice@example.com"
})

# 删除 (delete)
new_ctx = ctx.delete(Ref("user.profile.age"))
```

### 原子化事务 (mutate)

如果你需要同时进行复杂的更新和删除操作，可以使用更底层的 `mutate()` 方法。它保证了所有的修改在一次遍历中原子化完成：

```python
new_ctx = ctx.mutate(
    updates={
        Ref("user.profile.status"): "verified"
    },
    drops=[
        Ref("status") # 删除顶层 status
    ]
)
```

### 比较 Context (diff)

你可以使用 `.diff()` 方法比较两个 Context 对象之间的差异。它会返回一个 `ContextDiff` 对象，包含了新增、删除和修改的详细信息。这在调试、状态监控或编写测试用例时非常有用：

```python
new_ctx = ctx.mutate(
    updates={
        Ref("user.profile.status"): "verified",
        Ref("user.profile.name"): "Bob",
    },
    drops=[
        Ref("status")
    ]
)
diff = new_ctx.diff(ctx)
# 你可以使用 diff.flatten() 将差异展平为一个字典
print(diff.flatten())  # {'status': (<DiffMissing.MARK: 1>, 'active'), 'user.profile.status': ('verified', <DiffMissing.MARK: 1>), 'user.profile.name': ('Bob', 'Alice')}
```

其中，`slyme.context.DIFF_MISSING` 表示缺失值。`diff.flatten()` 返回的字典中，tuple 的第一个元素是新值，第二个元素是旧值。这就意味着，`DIFF_MISSING` 出现在第一个位置，表示值被删除；出现在第二个位置，表示值被新增；否则，表示值被修改。

## 异步支持 {#async-support}

在异步环境（如 `@async_node`）中，Slyme 提供了 Context 方法的对应异步版本，它们通常以 `async_` 开头：

- `await ctx.async_get(ref)`
- `await ctx.async_set(ref, value)`
- `await ctx.async_update(updates)`
- `await ctx.async_mutate(updates=..., drops=...)`
- `await ctx.async_to_dict()`

这使得你可以无缝地在同步和异步的 Node 中安全地操作 Context。值得注意的是，Context 本身是**本地存储且同步的**，异步环境的操作完全由 Context Hook 支持。

## Context Hook

为了提供更强大的扩展能力，`Context` 支持挂载 Hook。通过 Hook，你可以拦截并修改 Context 的读取（Extract）和写入（Mutate）行为。

这在许多高级场景中非常有用，例如：
- **数据转换**：在读取时自动反序列化，在写入时自动序列化。
- **外部存储映射**：将特定路径的数据映射到外部存储（如 Redis 或数据库），使得 Context 就像一个虚拟的文件系统。

### 自定义 Hook

你可以通过继承 `slyme.context.Hook` 并重写相关方法来创建自定义的 Hook：

```python
from typing import Any
from slyme.context import Hook, Context, Ref, ExtractResult, MutateResult

class MyLoggingHook(Hook):
    def on_extract(
        self,
        *,
        ctx: Context,
        refs: tuple[Ref, ...],
        values: tuple[Any, ...],
        **kwargs,
    ) -> ExtractResult:
        print(f"[Read] Paths: {[r.path for r in refs]}, Values: {values}")
        # 必须返回 ExtractResult，你可以修改 values 来改变实际读取到的值
        return ExtractResult(values=values)

    def on_mutate(
        self, 
        *, 
        ctx: Context, 
        updates: dict[Ref, Any], 
        drops: set[Ref], 
        **kwargs
    ) -> MutateResult:
        print(f"[Write] Updates: {updates}, Drops: {drops}")
        # 必须返回 MutateResult，你可以修改 updates 或 drops 来改变实际的写入行为
        return MutateResult(updates=updates, drops=drops)
```

::: info
对应的异步方法为 `on_async_extract` 和 `on_async_mutate`。默认情况下，如果你只实现了同步版本，异步版本会直接调用同步版本。但如果你需要执行真正的异步 I/O（比如查询数据库），你应该重写异步版本的方法。[异步支持](#async-support)章节中的方法实际调用的是异步的 Hook 方法。
:::

### 挂载 Hook

在初始化 Context 时，通过 `hook` 参数将其实例化并挂载：

```python
ctx = Context(hook=MyLoggingHook())

# 将会触发 on_mutate 打印日志
new_ctx = ctx.set(Ref("status"), "active") 

# 将会触发 on_extract 打印日志
status = new_ctx.get(Ref("status"))
```

### HookChain

如果你需要同时应用多个 Hook，可以使用 `HookChain` 将它们组合起来：

```python
from slyme.context import HookChain

chain = HookChain(hooks=(HookA(), HookB(), HookC()))
ctx = Context(hook=chain)
```

在 `HookChain` 中，Hook 是按顺序执行的：
- 对于 `extract`，上一个 Hook 修改后的 `values` 会作为下一个 Hook 的输入。
- 对于 `mutate`，上一个 Hook 修改后的 `updates` 和 `drops` 会作为下一个 Hook 的输入。
