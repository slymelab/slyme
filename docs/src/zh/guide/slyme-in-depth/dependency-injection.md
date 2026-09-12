# 依赖注入

Slyme 通过关键字绑定构建参数，并可结合运行时 `Context` 对参数值进行求值。

## Spec 收集

函数的每个 keyword-only 参数都会生成一个 `Spec`。可以显式配置默认值、`default_factory` 与 `auto_eval`；`Auto[T]` 是 `Spec(auto_eval=True)` 的简写。

调用工厂时会立即校验关键字名称并应用默认值。缺失的必需值会变成 `UNDEFINED`，并在 Node 或 Wrapper 调用时被拒绝。

## 调用时求值

每次调用时，Slyme 会：

1. 读取对象的当前参数；
2. 根据声明的 `auto_eval` 标记区分参数；
3. 按 PyTree 规则展开 Auto 参数；
4. 按 evaluator 类型批处理叶子；
5. 使用传入的 Context 解析、重建 Auto 容器并调用用户函数，同时直接传递非 Auto 值。

`Ref` evaluator 会按批次内的顺序为每个 Ref 调用 `ctx.get(ref)`，保留 leaf identity 和 container view；`Node` evaluator 会调用产生值的子 Node，并为每个子 Node 提供由父 Context 管理、绑定到独立 child Scope 的 Context。同步 tree 求值会按求值顺序运行子 Node。异步 tree 求值把 child 求值调度为 task：异步 child 可以在暂停点交错执行，而每个同步 child 都会在事件循环线程内持续运行至返回。两种模式下的 Scope 局部写入都保持隔离。其他 realization 类型可以通过 `EVALUATOR_REGISTRY` 扩展，而不需要让 Slyme 理解其内部执行机制。

`eval_tree(ctx, tree)` 在一次调用内完成遍历、批量求值和重建。普通叶子与 evaluator 返回值保持原对象 identity，返回值不会递归求值。即使没有叶子需要求值，Auto 容器也会重建。

## 求值时机

每次 Node 或 Wrapper 调用会对参数绑定取浅快照，在其用户函数被调用前遍历和求值 Auto 参数。Wrapper 多次调用 `call_next` 时，每次都会重新遍历，观察 Auto 容器的原地变化和当前 Context 值；替换 Node 参数绑定则影响下一次 Node 调用。短路返回的 Wrapper 不会遍历或求值被包装 Node 的 Auto 参数。

Auto 子 Node 可以返回派生值，但父级继续执行前，Slyme 会 dispose 其 Context 与注册的 effect。异步 child 执行或 cleanup 会使外层调用返回 awaitable，取消时也会等待 child cleanup。如果高阶 Node 显式地使用自己的 Context 调用子 Node，应在调用结束后通过 Ref 重新读取值，而不是依赖更早的 Auto 结果。
