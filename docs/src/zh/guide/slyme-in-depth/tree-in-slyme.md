# Slyme 中的 Tree

Tree 是一种嵌套结构：container 定义拓扑，未注册的对象视为 leaf。`slyme.utils.tree` 提供 `flatten` 等无状态模块函数和不可变的 `TreeRules`。每次遍历显式传入 `rules=`；这些函数不持有注册表或 Context。

## 规则与分派

`TreeRules` 包含精确类型 handler 映射。`TreeHandler(flatten, unflatten)` 描述一种 container；`unflatten=None` 允许遍历，但拒绝重建。子类不会自动匹配父类，不隐式查找 Python MRO。

规则快照复制 handler 映射。`TreeRules.merge()` 对同一类型保留首个 handler。`TreeDef` 保存 flatten 时选定的重建函数，因此重建时不查询当前规则。

每个遍历接口可传入 `resolver=TreeResolver(func, takes_aux=False)`。类型查找前先调用 `func(element)`：返回 `True` 将当前位置视为 leaf，返回 `False` 按精确类型查找，返回 `TreeHandler` 则覆盖默认分派。返回 `False` 且没有对应 handler 时，对象仍是 leaf。设置 `takes_aux=True` 后，回调接收 `(element, aux)`；`aux.parent` 是直接父容器，`aux.key_path` 是完整路径，根处分别为 `None` 和 `()`。回调参数数量只由 `takes_aux` 决定，返回路径的遍历也不例外。

```python
from slyme.utils.tree import TreeResolver, flatten
from slyme.utils.tree.common import DATA_RULES

leaves, definition = flatten(
    {"items": [1, 2]},
    rules=DATA_RULES,
    resolver=TreeResolver(lambda value: isinstance(value, list)),
)
assert leaves == [[1, 2]]
```

普通 `flatten`、`iter` 和 `map` 仅在 resolver 请求辅助数据时创建遍历上下文；返回路径的接口始终记录路径。记录路径时，handler 必须为非空容器提供 `TreeAux.children_keys`；缺失或数量不足时抛出 `ValueError`，不自动补充序列索引。不记录路径时，遍历不检查子元素的 key，但重建 handler 仍可能需要它们。内置 list 和 tuple handler 显式提供 `SequenceKey`。无论哪种情况，handler 提供的重建信息都会保留，包括字典的 key。

flatten handler 返回的 `TreeAux` 会原样传给其 unflatten handler；未指定的 `cls` 保持 `None`。`TreeDef` 保存平铺、不可变的 leaf 标记与私有 container 记录，其中包括实际容器类型和子元素数量。重建按原顺序消费 leaf，使用局部栈组装容器，不倒序或复制输入的 leaf 序列。每次出现的位置分别消费一个 leaf，无需公开逐 leaf 或逐 container 的定义类。

Flatten 递归遍历容器，仍受 Python 递归深度限制；重建采用后序迭代，先重建子元素，再重建其容器。遇到仅支持遍历的容器记录时才报错。Leaf 数量不匹配会抛出 `ValueError`。迭代器遍历不创建 `TreeDef`。

Tree 的内置数据类使用 slots，不提供实例字典或弱引用支持。子类自行选择是否声明 slots。

不依赖 Context 的遍历可以显式导入 `slyme.utils.tree.common.DATA_RULES` 和 `slyme.node.core.NODE_RULES`。前者处理普通数据容器，后者仅处理 Node、Wrapper 和 Auto；通过 `TreeRules.merge((NODE_RULES, DATA_RULES))` 组合后即可遍历 Node 图。这些不可变定义不在包顶层重导出。`_apply` 仍是私有实现细节。

## Context 持有的默认配置

根 `Context()` 从 `slyme.context.default` 安装三个 register 模式字段，每个字段持有独立的 Compose：

| Ref 常量 | 路径 | contribution |
| --- | --- | --- |
| `DATA_TREE_REF` | `$.tree.data.rules` | 用于 Auto、`extract` 和 `update_tree` 的 `TreeRules` |
| `NODE_TREE_REF` | `$.tree.node.rules` | 用于显式 Node 图遍历的 `TreeRules` |
| `EVALUATORS_REF` | `$.eval.handlers` | 精确类型 evaluator 映射 |

这些常量也由 `slyme.context` 导出。Data 规则展开 list、tuple、dict 和 MappingProxyType；Node 规则额外遍历 Node/Wrapper 参数绑定和 Auto payload。Node、Wrapper 和 Auto 仅用于遍历，没有重建函数。

`$.tree.data` 和 `$.tree.node` 容器目前只有 `rules`；Context 操作不声明也不读取 resolver 字段。

每次操作在遍历或异步暂停前，只解析一次有效规则与 evaluator 映射。之后新增或撤销 contribution 影响后续操作，不改变本次分派和重建。保留 callable 并不延长其 owner 管理的资源生命周期。

默认组合使用 `slyme.context.default` 中的 `TreeLayer` 和 `EvaluatorLayer`。每层拒绝重复登记相同的精确 class，即使 handler 相同，或登记来自共享该 Identity 的另一个 Scope。批次中存在冲突时，不会安装其中任何 class。撤销后可以重新登记这些 class。跨层查询按 Scope C3 顺序保留首个 handler；向子层登记以覆盖继承的贡献。撤销后重新显露下一条适用定义：

```python
from dataclasses import dataclass

from slyme.context import DATA_TREE_REF, Context
from slyme.utils.tree import TreeAux, TreeHandler, TreeRules, flatten


@dataclass
class Box:
    value: object


ctx = Context()
plugin = ctx.fork()
rules = TreeRules(
    {
        Box: TreeHandler(
            lambda box: ((box.value,), TreeAux()),
            lambda items, _: Box(next(iter(items))),
        ),
    }
)
plugin.effect(lambda: ctx.get(DATA_TREE_REF).register(plugin.scope, rules))

effective = ctx.get(DATA_TREE_REF).resolve(ctx.scope)
leaves, definition = flatten(Box(1), rules=effective)
assert leaves == [1]
assert definition.unflatten([2]) == Box(2)

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
child.register(
    DATA_TREE_REF,
    Compose(
        factory=TreeLayer,
        query=TreeLayer.merge,
    ),
)
assert not child.get(DATA_TREE_REF).resolve(child.scope).handlers
ctx.dispose()
```

空组合返回空规则，不隐式补充默认值。Node 的组装不依赖 Context，执行时使用传入的 Context；图遍历显式解析 NODE_TREE_REF，再将规则传给遍历函数。

Schema 声明直接归一化嵌套 dict，不读取运行时 Tree 配置。修改 data tree 规则不会改变 Schema 对声明的解释方式。

## 对象身份与求值

Tree 按每次出现的位置分别遍历，不保持重建后容器的共享引用，也不支持环。普通 leaf 和 evaluator 返回值保持对象身份。Context 在遍历中是不透明 leaf；`Context.flatten()` 返回其可见 Ref 到值的映射，包含可见的 `$`。

Auto 使用 data 规则寻找 leaf。Ref 和 Node evaluator 仅匹配精确类型；子类需要向 `EVALUATORS_REF` 单独贡献。Ref 读取当前 Context；每个子 Node 在独立 child Scope、由父级管理的 child Context 内运行，并在父级收到结果前释放。所有被遍历的 container 都会重建，即使其中没有可求值 leaf；evaluator 的返回结果不会再次遍历。
