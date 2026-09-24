# Slyme 中的 Tree

Tree 是一种嵌套结构：container 定义拓扑，未注册的对象视为 leaf。`slyme.utils.tree` 提供无状态的 `TreeEngine` 算法和不可变的 `TreeRules`。每次遍历显式传入 `rules=`；engine 不持有注册表或 Context。

## 规则与分派

`TreeRules` 包含精确类型 handler 映射，以及有序的 `pre_resolvers` 和 `post_resolvers`。`TreeHandler(flatten, unflatten)` 描述一种 container；`unflatten=None` 允许遍历，但拒绝重建。分派顺序是显式的 `is_leaf` 判断、pre-resolver、精确类型、post-resolver。子类不会自动匹配父类，不隐式查找 Python MRO。

规则快照复制 handler 映射。`TreeRules.merge()` 对同一类型保留首个 handler，分别拼接两个阶段的 resolver。`TreeDef` 保存 flatten 时选定的重建函数，因此重建时不查询当前规则。

flatten handler 返回的 `TreeAux` 会原样传给其 unflatten handler；未指定的 `cls` 保持 `None`。`ContainerDef.cls` 独立记录实际容器类型。Leaf 定义共享不可变、无状态的标记对象；重建时每次出现的位置仍分别消费一个 leaf。

不依赖 Context 的遍历可以显式导入 `slyme.context.default.DATA_RULES` 和 `slyme.node.core.NODE_RULES`。前者处理普通数据容器，后者仅处理 Node、Wrapper 和 Auto；通过 `TreeRules.merge((NODE_RULES, DATA_RULES))` 组合后即可遍历 Node 图。这些不可变定义不加入 `__all__`，也不在包顶层重导出。`_apply` 和 Schema 的声明规则仍是私有实现细节。

## Context 持有的默认配置

根 `Context()` 从 `slyme.context.default` 安装三个 register 模式字段，每个字段持有独立的 Compose：

| Ref 常量 | 路径 | contribution |
| --- | --- | --- |
| `DATA_TREE_REF` | `$.tree.data` | 用于 Auto、`extract` 和 `update_tree` 的 `TreeRules` |
| `NODE_TREE_REF` | `$.tree.node` | 用于显式 Node 图遍历的 `TreeRules` |
| `EVALUATORS_REF` | `$.eval.handlers` | 精确类型 evaluator 映射 |

这些常量也由 `slyme.context` 导出。Data 规则展开 list、tuple、dict 和 MappingProxyType；Node 规则额外遍历 Node/Wrapper 参数绑定和 Auto payload。Node、Wrapper 和 Auto 仅用于遍历，没有重建函数。

每次操作在遍历或异步暂停前，只解析一次有效规则与 evaluator 映射。之后新增或撤销 contribution 影响后续操作，不改变本次分派和重建。保留 callable 并不延长其 owner 管理的资源生命周期。

默认组合使用 `slyme.context.default` 中的 `TreeLayer` 和 `EvaluatorLayer`。每层拒绝重复登记相同的精确 class，即使 handler 相同，或登记来自共享该 Identity 的另一个 Scope。批次中存在冲突时，不会安装其中任何 class。撤销后可以重新登记这些 class。跨层查询按 Scope C3 顺序保留首个 handler；向子层登记以覆盖继承的贡献。Tree resolver 序列保持登记顺序。撤销后重新显露下一条适用定义：

```python
from dataclasses import dataclass

from slyme.context import DATA_TREE_REF, Context
from slyme.utils.tree import TreeAux, TreeEngine, TreeHandler, TreeRules

@dataclass
class Box:
    value: object

ctx = Context()
plugin = ctx.fork()
rules = TreeRules({
    Box: TreeHandler(
        lambda box: ((box.value,), TreeAux()),
        lambda items, _: Box(next(iter(items))),
    ),
})
plugin.effect(lambda: ctx.get(DATA_TREE_REF).register(plugin.scope, rules))

effective = ctx.get(DATA_TREE_REF).resolve(ctx.scope)
leaves, definition = TreeEngine.flatten(Box(1), rules=effective)
assert leaves == [1]
assert TreeEngine.unflatten(definition, [2]) == Box(2)

plugin.dispose()
assert Box not in ctx.get(DATA_TREE_REF).resolve(ctx.scope).handlers
ctx.dispose()
```

## Scope 隔离

普通 fork 复用父级配置，子 Scope 通过 C3 继承。绑定到无关 `Scope()` 的 fork 看不到默认值，必须显式安装需要的 Compose 和 contribution；框架不会回退到 `ctx.root`。独立创建的根 `Context()` 会安装自己的默认配置。

在由父级管理、阻断继承的子 Context 中安装独立规则组合：

```python
from slyme.context import Compose, Context, DATA_TREE_REF, ScopeBinding
from slyme.context.default import TreeLayer

ctx = Context()
child = ctx.derive(bindings={DATA_TREE_REF: ScopeBinding(blocked=True)})
child.register(DATA_TREE_REF, Compose(
    factory=TreeLayer,
    query=TreeLayer.merge,
))
assert not child.get(DATA_TREE_REF).resolve(child.scope).handlers
ctx.dispose()
```

空组合返回空规则，不隐式补充默认值。Node 的组装不依赖 Context，执行时使用传入的 Context；图遍历显式解析 NODE_TREE_REF，再将规则传给 TreeEngine。

Schema 声明使用私有、不可变、仅处理 dict 的规则，不读取运行时配置。修改 data tree 规则不会改变 Schema 对声明的解释方式。

## 对象身份与求值

Tree 按每次出现的位置分别遍历，不保持重建后容器的共享引用，也不支持环。普通 leaf 和 evaluator 返回值保持对象身份。Context 对 TreeEngine 是不透明 leaf；`Context.flatten()` 返回其可见 Ref 到值的映射，包含可见的 `$`。

Auto 使用 data 规则寻找 leaf。Ref 和 Node evaluator 仅匹配精确类型；子类需要向 `EVALUATORS_REF` 单独贡献。Ref 读取当前 Context；每个子 Node 在独立 child Scope、由父级管理的 child Context 内运行，并在父级收到结果前释放。所有被遍历的 container 都会重建，即使其中没有可求值 leaf；evaluator 的返回结果不会再次遍历。
