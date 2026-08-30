# 依赖注入

Slyme 通过关键字绑定构建参数，并可结合运行时 `Context` 对参数值进行求值。

## Spec 收集

函数的每个 keyword-only 参数都会生成一个 `Spec`。可以显式配置默认值、`default_factory` 与 `auto_eval`；`Auto[T]` 是 `Spec(auto_eval=True)` 的简写。

调用工厂时会立即校验关键字名称并应用默认值。缺失的必需值会变成 `UNDEFINED`，并在 Node 或 Wrapper 调用时被拒绝。

## 调用时求值

每次调用时，Slyme 会：

1. 读取对象的当前参数；
2. 区分静态值与需要 Auto 求值的值；
3. 为动态值生成 `EvaluationPlan`；
4. 按 evaluator 类型批处理叶子；
5. 使用传入的 Context 解析并调用用户函数，同时直接传递静态容器。

`Ref` evaluator 会批量提取 Context；`Node` evaluator 会调用产生值的子 Node。其他 realization 类型可以通过 `EVALUATOR_REGISTRY` 扩展，而不需要让 Slyme 理解其内部执行机制。

求值计划刻意只在单次调用中存在，从而避免动态 Node 图或未来 Slot registry 在调用之间变化时所需的缓存失效追踪。

## 求值时机

Auto 值在其所属 Node 或 Wrapper 进入时解析一次。如果高阶 Node 随后调用子 Node 修改了 Context，应在这些调用之后通过 `Ref` 显式读取最新值，而不是依赖较早解析的 Auto 值。
