# 依赖注入（Dependency Injection）

在复杂的数据流和工作流编排中，如何优雅地将“业务逻辑”与“配置数据/运行时状态”解耦是一个核心难题。Slyme 采用了函数式风格的设计，通过提供强大的**依赖注入**机制，使得 Node 可以保持纯粹，只关注业务计算。

Slyme 目前在两个层面实现了依赖注入：
1. **构建期（Build-time）注入**：使用 Scope 初始化 Node 时的参数注入。
2. **运行期（Runtime）注入**：Node 执行时的自动求值（Auto Eval）。

下面我们将深入探讨这两部分机制的具体实现细节。

## 1. 基于 Scope 的 Node 初始化

在构建工作流时，我们通常会在不同的层级定义各种配置项（例如全局配置、模块配置、组件配置）。Slyme 允许我们在创建 Node 时，传入多个字典（称为 Scope），底层会自动通过函数签名分析将需要的参数注入到 Node 中。

### 1.1 签名分析

在装饰器应用阶段，Slyme 利用 `inspect.signature` 和 `typing.get_type_hints` 分析函数签名和类型提示。在这个过程中，Slyme 会对每个构建期参数收集、合并生成 `Spec` 对象（包含默认值、是否需要自动求值等信息），并记录下参数名以备后续使用。

### 1.2 作用域解析

当创建 Node 时（@node / @expression / @wrapper），支持传入多个位置参数 `*scopes` 以及关键字参数 `**kwargs`。具体的注入逻辑由 `resolve_arguments` 函数完成：

- **ChainMap 优先级链**：Slyme 内部利用了标准库的高效数据结构 `collections.ChainMap` 将所有的 Scope 组合在一起。组合的顺序是 `ChainMap(overrides, *reversed(scopes))`。
- **查找规则**：这意味着在查找某个参数时，会遵循 **`kwargs (显式覆盖) > 最后一个 scope > ... > 第一个 scope`** 的优先级顺序。如果多个 scope 中存在同名键，较后传入的 scope 会覆盖前面的。
- **参数清洗**：在解析得到原始数据后，`process_kwargs` 函数会根据前一步生成的 `specs` 进行严格校验。它会抛出未知参数的异常（防止拼写错误），并为缺失的参数填充 `Spec` 中定义的默认值或调用 `default_factory`。

## 2. Node 自动求值 (Auto Eval)

静态配置往往是不够的。在很多场景下，Node 所需的参数是动态的，比如依赖 Context 中上一步的计算结果，或者是某个延迟执行的表达式。Slyme 提供了自动求值（Auto Eval）机制，让 Node 可以在运行时透明地获取动态依赖。

### 2.1 声明式求值标注

开发者无需在 Node 内部手动调用 `ctx.get()`，只需在函数签名中使用 `Auto`（其底层是 `Annotated[T, Spec(auto_eval=True)]`）来标注该参数支持自动求值。

### 2.2 编译期准备

为了保证运行时的高效性，Slyme 不会在每次 Node 执行时都去全量扫描参数。相关工作被前置到了 Node 从定义态（`*Def`）转化为执行态（`*Exec`）的 `prepare` 阶段：

1. **分离参数**：`*Exec` 的 `__init__` 方法中，调用了 `_prepare_eval`。它会根据参数的 `Spec` 检查该值是否包含需要求值的对象。不需要求值的纯静态参数会被放入 `raw_kwargs` 直接预绑定，而动态参数被放入 `eval_kwargs`。
2. **生成求值计划 (EvaluationPlan)**：针对 `eval_kwargs`，Slyme 调用 `prepare_eval_plan` 生成优化的求值计划。
3. **PyTree 展平与批处理**：
   - 依赖项可能是复杂的嵌套结构（如字典中包含列表，列表中包含 `Ref` 对象）。Slyme 利用底层的 `CTX_EVAL_ENGINE`（基于 PyTree）将嵌套结构“展平”（Flatten）成一维的叶子节点列表。
   - 接着，遍历这些叶子节点，在 `EVALUATOR_REGISTRY` 注册表中查找对应的求值器（如针对 `Ref` 类型的 `ref_evaluator`，针对 `Expression` 的 `expression_evaluator`）。
   - 将相同的求值器分组，生成基于批处理（Batch）的执行计划 `EvaluationPlan`。这种设计将零散的数据访问合并，极大地优化了性能。

::: tip
生成求值计划是跨参数的，也就是说，如果一个 Node 有多个参数标注了 `Auto`，那么 Slyme 会统一处理它们，将所有相同的求值类型汇总到一起批处理，极大地提高了求值效率。
:::

### 2.3 运行时执行

当 `*Exec` 被真正调用 `__call__(ctx)` 时，依赖注入的最后一环开始执行：

1. **执行求值计划**：调用 `execute_eval_plan(ctx, eval_plan)`。
2. **批量抽取**：执行引擎会按批次调用 Evaluator。例如对于所有的 Ref 对象，`ref_evaluator` 会一次性调用底层高效的 `ctx.extract(refs)` 获取所有路径对应的值；如果是表达式，则会传入 `ctx` 批量计算。注意，针对 Ref 进行批量求值时，Context Hook 仅会被调用一次（传入批量的 Ref 和 value），方便 Hook 编写者优化性能。
3. **结构还原 (Unflatten)**：获取到所有计算后的叶子节点值后，依照原有的嵌套结构将这些值重新组装为最初的字典/列表形态。
4. **透明执行**：最终，这些动态计算出来的依赖会与静态的 `raw_kwargs` 合并，传递给开发者定义的 Node 函数。从开发者的视角来看，他们拿到的就是已经完全解包好的纯粹数据。

### 2.4 注意事项：Auto Eval 的时机与高阶 Node

::: warning
`Auto` 的求值对象是函数被调用时**初始传入的 `ctx` 参数**。求值发生在你的 Node 函数体真正执行**之前**。

如果你正在创建一个**高阶 Node**（例如在 @node 函数内部循环调用了其他的 @node，并返回了新的 `ctx`），请务必注意：通过 `Auto` 注入的参数值**不会**随着你内部调用其他 @node 而产生的新 `ctx` 自动更新。

举个例子，如果你在编写一个训练循环 @node，并且需要在每一个 step 执行之后获取最新的 loss：
- **错误做法**：将 `loss` 参数标注为 `Auto`。这样你只能拿到进入循环前求值得到的旧数据。
- **正确做法**：将 `loss` 参数保留为普通的 `Ref`，从而能够在每次循环体内拿到最新的 `ctx` 后，手动调用 `ctx.get(loss)` 来动态获取最新值。

如果你想在高阶 @node 函数内部对复杂的嵌套结构（类似于 `Auto` 支持的结构）进行动态求值，你可以手动调用底层提供的求值 API：
```python
from slyme.node.eval import eval_tree, async_eval_tree

# 在高阶 @node 的执行逻辑中，手动对复杂的嵌套结构进行求值
resolved_values = eval_tree(current_ctx, complex_ref_structure)
```
:::
