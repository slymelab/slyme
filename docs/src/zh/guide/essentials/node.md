# Node

Node 是 Slyme 中最核心的逻辑执行单元，它是函数式编程概念的具象化。Slyme 基于一种严谨的构建（Def）到执行（Exec）的两阶段架构，这让 Node 在具备极高灵活性的同时，也能保证运行时结构的安全与高效。

::: info
在接下来的章节（包括整个 Slyme 文档）中，除非特殊说明，我们使用 “Node” 来泛指 @node / @expression / @wrapper，而具体的类型则会显式地通过 @node / @expression / @wrapper 来表示。
:::

## Node 体系设计 {#design}

在 Slyme 中，所有的 Node 类都遵循以下几个核心设计理念：

1. **Def 与 Exec 分离**：Node 的声明期和执行期是严格分开的。当你调用一个被 @node 装饰的函数时，返回的是一个可变的**定义对象（Def）**；只有调用 `.prepare()` 之后，才会将其转化为不可变的**执行对象（Exec）**。这种设计让你可以动态地挂载 @wrapper、修改参数，最后“冻结”为安全的执行实例。
2. **位置限定与关键字限定**：为了在函数签名中区分“执行期参数”（即真正运行时框架内部自动传入的参数）与“构建期参数”（即在编写 Node 时用户可自由定义的配置参数），Slyme 强制要求所有执行期参数必须是**仅位置参数**（在 `/` 之前），而构建期参数（用户自定义配置）必须是**仅关键字参数**（在 `*` 之后）。用户在实例化 Node 对象时，需要传入自定义的配置参数。
3. **返回值校验**：除了轻量级的 @expression 外，@node 与 @wrapper 都应该返回一个 Context。这是为了确保基于 Copy-On-Write 机制的不可变状态链能够完整传递。

::: tip
此章节将介绍 Node 系列的基础用法与注意事项，如果要完全深入地掌握 Node 的行为，强烈推荐在阅读完这一章之后继续阅读[生命周期](/zh/guide/essentials/lifecycle)。
:::

## @node {#at-node}

@node 装饰器用于创建一个标准的 @node 节点。它的核心职责是接收一个 Context，执行一段业务逻辑，并返回更新后的 Context（或者返回原 Context）。

### 创建 @node

你可以通过如下方式定义一个 @node：

```python
from slyme.node import node, Auto
from slyme.context import ARG, Arg, Context, Ref, R

@node
def to_upper(ctx: Context, /, *, value: Ref[str]) -> Context:
    return ctx.set(value, ctx.get(value).upper())
```

注意参数签名中的 `/` 和 `*`，它们是必不可少的。`ctx` 必须位于 `/` 之前，而配置参数 `value` 必须位于 `*` 之后。

### 执行 Node

使用 `Arg` 元数据声明执行前必需的所有 Context 值，然后以 `run()` 作为应用层调用入口：

```python
node_def = to_upper(
    value=R.name(metadata={ARG: Arg(type=str, required=True)})
)
value = node_def.run(
    inputs={R.name: "Alice"},
    outputs=R.name,
)
print(value)  # 输出：ALICE
```

`run()` 会自动 prepare Def、创建或扩展 Context、校验 `Arg` 声明的输入、执行 Node，并按指定的输出 Ref PyTree 提取结果。不传 `outputs` 时返回最终 Context；指定输出 schema 并设置 `return_context=True` 时返回 `(output, context)`。

如需从命令行读取输入，请设置 `use_argparse=True`；可以显式传入 `cli_args`，也可以省略它以解析 `sys.argv[1:]`。完整的输入模型和优先级规则请参阅[自动参数解析](/zh/guide/extensions/argparse-integration)。

对于底层执行，或者需要自行管理 Context 并重复使用同一个不可变 Exec 的场景，可以调用一次 `node_def.prepare()`，然后直接执行所得 Exec。

## @expression {#at-expression}

@expression 用于定义一种轻量级的派生节点。与标准 Node 不同，Expression **不要求返回 Context**。它通常被用来从 Context 中读取并计算出某个具体的值，然后将这个值作为参数传递给其他 Node。

### 创建与执行 @expression

定义一个 @expression 的语法与 @node 非常类似：

```python
from slyme.node import expression
from slyme.context import Context, Ref, R

@expression
def get_greeting(ctx: Context, /, *, prefix: str, name: Ref[str]) -> str:
    name_ = ctx.get(name, default="Guest")
    return f"{prefix} {name_}"

# 创建 @expression 实例
expr = get_greeting(prefix="Hello", name=R.not_exist_path)

# 执行 @expression
greeting = expr.prepare()(Context())  # 返回 "Hello Guest"
```

::: tip 风格指南
在 Slyme 中，我们推荐的代码风格是：不通过函数参数命名来区分 Ref 类型和真实的值，而是通过类型注解来区分。即使用 `name: Ref[str]` 而不是 `name_ref` 或者 `ref_name`。如果函数内同时存在 Ref 和它所指向的值，那么使用单后缀下划线（如：`name_`）来避免命名冲突。这样可以使得复杂 Node 结构下代码更简洁易读。文档中的示例和官方维护的代码都遵循这一风格。
:::

## @wrapper {#at-wrapper}

@wrapper 是 Slyme 提供的中间件（Middleware）机制，它允许你拦截并增强 Node 的执行过程。Slyme 的 @wrapper 遵循经典的“洋葱模型”。

### 创建 @wrapper

定义 @wrapper 时，其位置限定参数必须严格按照 `(ctx, wrapped, call_next, /)` 的顺序：

```python
from slyme.node import wrapper
from slyme.context import Context

@wrapper
def logging_wrapper(ctx: Context, wrapped, call_next, /, *, level: str) -> Context:
    print(f"[{level}] 开始执行 - Node:\n{wrapped}")

    # 将控制权交给下一个 @wrapper 或目标 @node
    ctx = call_next(ctx)

    print(f"[{level}] 执行完成 - Node:\n{wrapped}")
    return ctx
```

其中，`wrapped` 是当前 @wrapper 包裹的 @node 实例，`call_next` 是一个函数，用于调用下一个 @wrapper 或目标 @node。

### 挂载 @wrapper

你可以通过 `.add_wrappers()` 方法将一个或多个 @wrapper 挂载到 @node 实例上：

```python
node_def = to_upper(value=R.name).add_wrappers(
    logging_wrapper(level="INFO")
)

# 执行时，日志会被自动打印
node_exec = node_def.prepare()
new_ctx = node_exec(ctx)
```

## Node 结构校验 {#node-struct}

Slyme 中，@node，@expression，@wrapper 之间的合法结构关系如下：

- @expression 可以被 @node / @wrapper 或者另一个 @expression 持有，即任意 Node 类型都可以使用 @expression 来进行求值计算
- @wrapper 只能被 @node 通过 `.add_wrappers()` 方法挂载，不能被其他 Node 类型（包括 @expression 和 @wrapper）持有。即，@wrapper 只能用于作为 @node 的中间件。
- @node 只能被其他 @node 持有，不能被 @expression 或 @wrapper 持有。@node 是 Node 体系的最核心的类型，@expression 和 @wrapper 的作用是功能的增强。
- 上述“持有”的意思是，@node / @expression / @wrapper 通过自定义参数传入了 Node 类型的实例，其中包括任意嵌套的列表/元组/字典，比如某个 @node 的参数 `nodes` 传入了一个 @node 列表，或者某个 @wrapper 的参数 `expression_dict` 传入了一个 `dict[str, Expression]` 等等。

在上述约束下，我们可以构建任意复杂的 Node 结构，最终组成一个 Node 树。

## 声明式依赖 (Spec) {#spec}

在 Slyme 中，你可能会遇到一些复杂的配置参数，它们需要有默认值、动态生成的默认值（比如空列表），甚至需要在执行时根据 Context 动态求值。为此，Slyme 提供了 `Spec` 对象以及便捷的 `spec` 函数。

### Spec 解析逻辑

Slyme 会检查用户函数的参数签名和类型注解，从中找到所有合法位置的 Spec 对象，并进行合并操作。目前允许的 Spec 定义位置：

- 通过函数的默认值指定 Spec
- 通过**最外层** `Annotated` 来指定 Spec

举例（以 @node 为例，@expression 和 @wrapper 同理）：

```python
from typing import Annotated

@node
def my_node(
    ctx: Context,
    /,
    *,
    param1: str = "123",  # 等价于 `param1: str = spec(default="123")`
    param2: tuple = spec(default_factory=tuple),  # 每次触发默认值逻辑时都会创建一个新的元组
    param3: Annotated[int, spec(default=0)],  # 可以使用 Annotated 来指定 Spec
    param4: Annotated[int, spec(auto_eval=True), spec(default=0)],  # 多个 Spec 可以合并，等价于 `param4: int = spec(auto_eval=True, default=0)`。其中 `auto_eval = True` 代表着参数可以在执行时自动求值，即之前提到的 `Auto` 类型注解，它们是等价的。
    param5: Annotated[int, spec(auto_eval=True)] = 0,  # 同理，`spec(auto_eval=True)`和默认值0（即 `spec(default=0)`）也会合并
    # param_bad: Annotated[int, spec(default=0)] = 0,  # 错误，这里的 `spec(default=0)`与默认值0重复定义，字段冲突。
    # param_bad2: tuple[Annotated[int, spec(auto_eval=True)], ...]  # 错误，通过 Annotated 来指定 Spec 时，其必须在最外层。
    param6: Annotated[Annotated[int, spec(auto_eval=True)], spec(default=0)]  # 正确，多个 Annotated 嵌套可以自动展开，因此它符合最外层的约束。
): ...
```

::: tip
上述示例列举了合法的 Spec 定义方式，用于满足一些开发者的高级需求，以及深入了解 Slyme 的内部工作原理。不过，大多数需求的使用场景比较简单，可以阅读下面的章节。
:::

### 默认值与工厂函数

你可以通过 `spec(default=...)` 或 `spec(default_factory=...)` 来定义参数规范，它们将在构建期填补用户未提供的参数：

```python
from slyme.node import node, spec
from slyme.context import Context

@node
def process_data(
    ctx: Context, 
    /, 
    *, 
    timeout: int = 30,  # 或 `timeout: int = spec(default=30)`
    tags: tuple = spec(default_factory=tuple),
) -> Context:
    # 业务逻辑
    return ctx

process_data()  # timeout=30, tags=()
process_data(timeout=60)  # timeout=60, tags=()
```

### 自动求值 (Auto Eval)

这是 Slyme 最强大的特性之一。通过设置 `auto_eval=True`（或使用内置的 `Auto` 类型提示注解），你可以允许参数在传入时是一个 Ref 或 @expression。Slyme 会在 Node 执行前自动从 Context 中解析出真实的值，并将其注入到函数参数中：

```python
from slyme.node import node, Auto
from slyme.context import Context, Ref, R

@node
def dynamic_greet(
    ctx: Context, 
    /, 
    *, 
    # Auto[str] 是 Annotated[str, spec(auto_eval=True)] 的语法糖
    # 或者你也可以写成 `target: str = spec(auto_eval=True)`
    # Auto[str] 是最简洁、最推荐的写法，注意 Auto 永远在类型注解的最外层。
    target: Auto[str],
) -> Context:
    # 此时 target 已经被解析为真正的字符串
    print(f"Hello, {target}!")
    return ctx

# 传入 Ref，那么运行时将会由框架内部自动调用 `ctx.get(R.user.name)`，并将返回的结果作为 `target` 的值。
node_def = dynamic_greet(target=R.user.name)
```

这种设计让 Node 的逻辑变得极其纯粹，完全解耦了“去哪取数据”与“如何处理数据”。另外，`Auto` 支持 Python 标准数据结构嗅探，例子如下：

```python
from slyme.node import node, Auto
from slyme.context import Context, Ref, R

@node
def process_data(
    ctx: Context,
    /,
    *,
    data: Auto[dict],
): ...

process_data(data={
    "id": R.user.id,  # 运行时会被解析为具体的值
    "name": some_expression(...),  # 运行时会被解析为具体的值
    "value": 3,  # 保持不变
    "tags": (
        R.user.status,  # 运行时会被解析为具体的值
        R.user.membership,  # 运行时会被解析为具体的值
        some_expression2(...),  # 运行时会被解析为具体的值
    ),
})
```

::: tip
在执行时，标注了 `Auto` 的 `data` 参数会被 Slyme 框架自动深度求值，将其中包含的 Ref 和 @expression 全部自动调用（求值的对象就是 `process_data` 输入的 Context 参数），最后将得到的真实值注入到原始结构中。这个功能使得 Node 系列能够极致地解耦，我们可以像上面这样把不同的 Context 路径组合成一个期望的结构；也可以让某一个特定的 Context 路径直接存储这个结构（比如 `R.data`），然后创建时使用 `process_data(data=R.data)` 即可；还可以定义一个 `data_expression`，让它的返回值是符合这个结构的 dict。而这一切都不需要被 `process_data` 这个 @node 本身所关心，它只需要知道 `data` 参数会传入一个满足特定结构的 dict 即可。
:::

::: tip
如果想深入了解 `Auto` 的工作原理，你可以参考[依赖注入](/zh/guide/slyme-in-depth/dependency-injection)章节。
:::

## 使用 Scope 来初始化 Node（已弃用）

::: warning 已弃用
向 Node 工厂函数传入位置参数 `dict`（Scope）的方式自 slyme 0.1.1 起**已弃用**，并将在 0.2.0 中移除。请改用 [`RefFactory`](/zh/guide/essentials/context#reffactory)（`R.x.y.z`）在关键字参数中直接指定，代码更简洁、更显式。
:::

**旧范式 — 位置参数 Scope 字典（已弃用）：**

Scope 是一个标准的 Python 字典，用于按名注入函数的参数。想象现在有很多 @node，它们都需要用到 `user_data` 这个自定义配置参数，正常情况下，你需要这样为他们赋值：

```python
user_data_ref = Ref("user.data")
node1(user_data=user_data_ref)
node2(user_data=user_data_ref)
node3(user_data=user_data_ref)
...
```

为了减少构建期的重复代码，Slyme 提供了 Scope 自动注入的功能，现在你可以直接使用一个 Scope 字典来初始化所有的 Node（字典的 key（str）对应的是函数的参数名称，value 对应的是具体构建期需要填入的值）：

```python
shared_scope = {"user_data": user_data_ref}
node1(shared_scope)
node2(shared_scope)
node3(shared_scope)
...
```

这在复杂 Node 结构下非常有用。在开发新的 Node 时，你可以参考已有的 Scope，采用同名的参数，这样实例化这个 Node 时可以直接传入 Scope 字典，即可自动绑定到对应的参数上。为了防止参数名冲突（比如两个 Node 都有 `value` 参数，但是他们的含义完全不同），Slyme 支持按优先级顺序进行查找：

```python
scope1 = {"value": Ref("value1")}
scope2 = {"value": Ref("value2")}
my_node(scope1, scope2, value=Ref("value3"))
```

优先级顺序是：关键字参数 > 最后的 Scope 位置参数 > ... > 第一个 Scope 位置参数，因此上面这个例子中，`value` 的参数解析优先级是 `Ref("value3")` > `Ref("value2")` > `Ref("value1")`，最终会使用 `Ref("value3")` 作为 `value` 的值。

这样的分层设计使得 Node 的复用程度更高。比如对于 `create_dataset(dataset=...)` 的 @node，我们可以用 `train_scope` 和 `eval_scope` 来让同一个 `create_dataset` 函数按照配置分别实例化成一个创建训练集的 @node 和一个创建评测集的 @node，完全不需要修改 `create_dataset` 本身：

```python
common_scope = {"device": Ref("device"), "max_tokens": Ref("max_tokens")}
train_scope = {"dataset": Ref("train.data")}
eval_scope = {"dataset": Ref("eval.data")}

create_train = create_dataset(common_scope, train_scope)
create_eval = create_dataset(common_scope, eval_scope)
```

**新范式 — 在关键字参数中使用 `R`（RefFactory）：**

```python
from slyme.context import R

# 直接在关键字参数中使用 R.x.y.z — 无需 Scope 字典
create_train = create_dataset(
    device=R.device, max_tokens=R.max_tokens, dataset=R.train.data
)
create_eval = create_dataset(
    device=R.device, max_tokens=R.max_tokens, dataset=R.eval.data
)
```

这种方式更加显式、类型安全且可读性更强。由于 `RefFactory` 会被透明地解析为 `Ref`，因此可以在任何需要 `Ref` 的地方使用。

## 动态修改 Node

Slyme 中，Node（@node，@expression，@wrapper）在调用 `.prepare()` 之前是可以动态修改的。比如：

```python
my_node = dynamic_greet(target=R.user.name)
my_node["target"] = R.user.another_name  # my_node 的 target 参数变成了 R.user.another_name

# NOTE: Node 支持深层修改
another_node["nodes"][0]["value"] = ...
```

在 Slyme 中，我们可以使用 `[]` 操作符来访问 / 修改 Node 的构建期参数。比如，如果我们想对某一个 [@builder](/zh/guide/essentials/builder) 返回的 Node 树进行微调，我们可以在另一个 @builder 中调用这个 @builder，然后对其返回值进行修改，再将修改后的结果返回，这避免了重复抄写大部分相同的 @builder 代码：

```python
@builder
def my_builder():
    my_node = another_builder()
    my_node["nodes"][0]["abc"] = 123
    return my_node
```

## Node 中的标记常量

Node 中有一些常量用于实例化 Node 过程中的特殊标记：

- `UNSET`：显式表示 Node 的某个参数未被设置，从而触发默认值逻辑。
- `UNDEFINED`：表示 Node 某个参数没有被设置合法的值，需要设置之后才能执行。这意味着，一些参数可以延迟被指定，但是在 `.prepare()` 方法被调用时，所有的参数都不能是 `UNDEFINED`。

举例：

```python
from slyme.node import node, UNSET, UNDEFINED
from slyme.context import Context, Ref, R

@node
def process_data(
    ctx: Context, 
    /, 
    *, 
    timeout: int = 30,
    tags: tuple,
    data: Ref[dict],
) -> Context:
    # 业务逻辑
    return ctx

process_data(tags=())  # timeout=30, tags=(), data=UNDEFINED

my_node = process_data(timeout=60)  # timeout=60, tags=UNDEFINED, data=UNDEFINED
my_node["tags"] = (1, 2)  # timeout=60, tags=(1, 2), data=UNDEFINED
my_node["timeout"] = UNSET  # NOTE: 触发默认值逻辑，此时 timeout=30
my_node["timeout"] = UNDEFINED  # NOTE: 此时 timeout 被显式地设置为 UNDEFINED，需要在 `.prepare()` 之前设置一个合法的值

my_node2 = process_data(timeout=UNSET, tags=(2, 3))  # NOTE: timeout 被设置成 UNSET 因此触发了默认值逻辑，最终：timeout=30, tags=(2, 3), data=UNDEFINED
# my_node2.prepare()  # 此时调用 `.prepare()` 会报错，因为 data 未被设置
my_node2["data"] = R.user.data
my_node2_exec = my_node2.prepare()  # timeout=30, tags=(2, 3), data=R.user.data
# my_node2_exec 可被后续调用执行了
```

::: tip
如果想要深入了解整个过程中 Node（@node，@expression，@wrapper）的完整行为，请阅读[生命周期](/zh/guide/essentials/lifecycle)章节。
:::

## 异步支持 (Async Node) {#async-node}

为了应对现代 I/O 密集型任务，Slyme 提供了完整的异步支持。`@node`、`@expression` 和 `@wrapper` 会通过检查被装饰的函数，自动识别普通函数和 `async def` 函数，因此同步与异步代码使用同一组装饰器。

### 创建异步 @node

```python
from slyme.node import node
from slyme.context import Context, Ref, R

@node
async def fetch_user_data(ctx: Context, /, *, url: str, user_data: Ref[dict]) -> Context:
    # 假设 do_fetch 是某个异步请求函数
    # data = await do_fetch(url)
    data = {"id": 1, "name": "Alice"}
    return ctx.set(user_data, data)
```

### 执行异步 @node

异步 Node 提供与同步 Node 相同的高层调用协议；在应用边界 `await run()` 即可：

```python
node_def = fetch_user_data(url="https://api.example.com/user", user_data=R.user.data)
user_data = await node_def.run(outputs=R.user.data)
print(user_data)  # {'id': 1, 'name': 'Alice'}
```

::: info
自动判断只检查函数本身是否由 `async def` 定义，不使用返回值类型提示。如果普通 `def` 函数返回 Awaitable，请显式使用 `@node(mode="async")`；`@expression` 和 `@wrapper` 也支持相同的 `mode` 参数。必要时也可用 `mode="sync"` 显式限定同步模式。
:::

::: warning 弃用说明
`@async_node`、`@async_expression` 和 `@async_wrapper` 自 Slyme 0.1.1 起已弃用，并将在 0.2.0 中移除。请分别迁移到统一的 `@node`、`@expression` 和 `@wrapper`；普通 `def` 返回 Awaitable 的特殊情况使用 `mode="async"`。
:::

## 内置通用节点 {#common-nodes}

为了简化开发，Slyme 提供了一些常用的内置 Node（持续更新）。

### 顺序执行 (Sequential)

如果你想把多个 Node 串联起来依次执行，可以使用 `sequential` 或 `async_sequential`：

```python
from slyme.node import sequential, async_sequential

# 同步执行链
seq_node = sequential(nodes=[node1, node2, node3])
ctx = seq_node.prepare()(ctx)

# 异步执行链 (可以混用 Sync 和 Async Node)
async_seq_node = async_sequential(nodes=[async_node1, sync_node2, async_node3])
ctx = await async_seq_node.prepare()(ctx)
```

另外，你可以使用 `sequential_exec` 或 `async_sequential_exec` 来直接执行一个 NodeExec 的列表：

```python
from slyme.node import sequential_exec, async_sequential_exec

# 同步环境下
ctx = sequential_exec(ctx, [node_exec1, node_exec2, node_exec3])
# 异步环境下
ctx = await async_sequential_exec(ctx, [async_node_exec1, sync_node_exec2, async_node_exec3])
```

这意味着，当一个 @node 接受一个 @node 列表作为参数时，你可以更方便地使用 `sequential_exec` 或 `async_sequential_exec` 来执行它。
