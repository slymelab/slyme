# 自省

`slyme.inspection.inspect(value, tree_rules=..., interpreters=..., type_names=..., env=...)` 返回原值保留策略可配置的内存观察图。它观测当前结构，不执行 Node 图，也不预测未来的运行轨迹。全部配置显式传入，不要求 Context；普通数据可以传入 `slyme.tree.common` 的 `DATA_RULES`。应用也可以通过 `resolve_inspection_options(ctx)` 获取分层配置：

```python
from slyme.context import Context, Ref, Schema, ref
from slyme.context.inspection import resolve_inspection_options
from slyme.inspection import inspect
from slyme.node import node, auto


@node
def task(ctx, *, value):
    return value


ctx = Context()
ctx.declare({"input": Schema.leaf()})
ctx.set("input", 42)
graph = task.partial(value=auto({"items": [ref("input")]}))

options = resolve_inspection_options(ctx)
document = inspect(graph, **options, env=ctx)
assert "value" not in document["objects"][document["root"]["$ref"]]
context_document = inspect(ctx, **options)  # Context 自动使用自身来观测值。
ctx.dispose()
```

## 命名与职责

名称按职责选择词形，不强行使用同一种语法形式。`inspection` 表示功能领域；`inspect()` 执行自省，返回 `InspectionGraph`。`Interpreter` 是同步解释函数，`Interpretation` 是它返回的值与环境，`interpretation` 表示图中的解释记录或关系。`interpreters` 将类型映射到解释函数；`interpreter_resolver` 选择解释函数，类型为 `InterpreterResolver`。`resolve_inspection_options(ctx)` 解析作用域配置，不对值执行自省。结构展开独立复用已有的 `TreeRules`、`TreeHandler` 和 `TreeResolver` 命名。

## 两个独立职责

**children 展开**复用 `TreeRules` 和 `flatten_one()`。默认支持普通容器、Node／Wrapper 的参数和 metadata、Auto 的内容、Context 的子节点与声明、Scope、Ref 和 Compose 的 layers。参数中绑定的 Node 或 Wrapper 可以继续展开。`Wrapper.compose()` 只产生临时调用链；自省不会扫描闭包来推断对象关系，也不会改变原对象。

**value 解释**使用精确类型到同步函数的映射。函数接收 `(value, env)`，返回 `Interpretation(value, env=...)` 或 `None`。省略 `env` 表示继承当前环境，显式传 `None` 表示清除环境。回调返回 `None` 表示无法解释，`Interpretation(None)` 表示观测结果确实为 `None`。

当环境是 Context 时，默认解释器通过 `ctx.get()` 读取 Ref，通过 `resolve(ctx.scope)` 调用 Compose 已配置的 query。没有 query 的 Compose 不解释。Context 和 ContextView 解释为自身可见的 Ref 到原始值的映射，结果的环境自动设为所属 Context。使用这些适配器时，`inspect(target_ctx, **options)` 不需要额外指定环境；没有环境的 `inspect(node, **options)` 仍能展示保存的结构。配置来源、被观察对象和解释环境相互独立。

结构 children 和解释得到的值都会继续进行结构展开和语义解释。children 继承当前环境；只有解释结果使用 `Interpretation.env` 显式指定的新环境。因此 query 可以返回嵌套在任意容器中的 Node，自省能够展开它们而不执行它们。自省不运行 Auto 求值、Node／Wrapper 函数体、awaitable 或 generator，也不调用不透明对象的 `repr`、属性枚举或迭代协议。未知对象以引用和类型标签展示。query 和扩展回调是普通 Python 代码，应保证同步、轻量、在本次观测中稳定且无副作用；自省不会对它们施加沙箱或超时中断。

## 扩展方式

根 Context 安装四个 register 模式的 Compose，由 `slyme.context` 导出的常量访问：

| Ref | 路径 | 贡献内容 |
| --- | --- | --- |
| `INSPECTION_TREE_RULES_REF` | `$.inspection.tree_rules` | `TreeRules` |
| `INSPECTION_INTERPRETERS_REF` | `$.inspection.interpreters` | 精确类型 → 同步解释函数 |
| `INSPECTION_TYPE_NAMES_REF` | `$.inspection.type_names` | 精确类型 → 语义类型名称 |
| `INSPECTION_RAW_VALUE_TYPES_REF` | `$.inspection.raw_value_types` | 精确类型 → `None`；键表示允许保留原值的类型 |

通过 `ctx.effect()` 登记并管理贡献的撤销。同一层不允许重复登记精确类型；子层按 Scope C3 顺序覆盖继承的项。撤销后重新显露下一条适用定义。子类需要单独登记，返回 `None` 的解释器可以禁用继承的解释。

`resolve_inspection_options(ctx)` 按 `ctx.scope` 解析配置，返回 `tree_rules`、`interpreters`、`type_names`、`raw_value_types` 四个关键字参数。它不观察对象，也不选择 `env`。各项配置都是浅快照，`raw_value_types` 是新的 `dict[type, None]`，以键作为白名单。需要新配置时重新调用；解释环境切换不会重新选择配置。配置缺失时报错，不回退到 root 或内置适配器。

`type_names` 为类型命名；`raw_value_types` 独立控制哪些记录包含原始 `value`。传入集合时，它是精确类型白名单，不是编码器或序列化校验器；传入 `None` 时保留所有原始值。直接调用 `inspect()` 时默认白名单为空。若要使用 Context 配置但保留所有原值，先获取 `options = resolve_inspection_options(ctx)`，再将 `tree_rules=options["tree_rules"]`、`interpreters=options["interpreters"]`、`type_names=options["type_names"]` 和 `raw_value_types=None` 显式传入 `inspect()`。Context 默认登记 `slyme.inspection.common.SCALAR_TYPES`，包含 `NoneType`、`bool`、`int`、`float`、`str`。取 `compose = ctx.get(INSPECTION_RAW_VALUE_TYPES_REF)` 后，通过 `ctx.effect(lambda: compose.register(ctx.scope, {MyType: None}))` 增加类型。它复用 `TypeLayer[None]`：同层重复类型报错，撤销后恢复继承层条目。要替换继承的默认项，先使用 `bindings={compose: ScopeBinding(blocked=True)}` 派生 Context，再向其登记需要的类型。可见组合为空表示不保留原值，子类需要显式加入。两个配置都不改变结构展开或解释行为。

语义类型名称为对象表记录指定独立于 Python 类名的标识。它们不改变结构 handler 或 interpreter，同样适用于标量和其他对象。未知对象无需语义标识也能通用展示。

```python
from dataclasses import dataclass

from slyme.context import Context, Metadata, INSPECTION_TREE_RULES_REF, INSPECTION_TYPE_NAMES_REF
from slyme.context.inspection import resolve_inspection_options
from slyme.inspection import inspect
from slyme.tree import AttributeKey, TreeAux, TreeHandler, TreeRules


@dataclass(frozen=True)
class Description(Metadata):
    text: str


rules = TreeRules(
    {
        Description: TreeHandler(
            lambda value: (
                (value.text,),
                TreeAux(children_keys=(AttributeKey("text"),)),
            ),
            None,
        ),
    }
)
ctx = Context()
plugin = ctx.derive()
plugin.effect(
    lambda: plugin.get(INSPECTION_TREE_RULES_REF).register(plugin.scope, rules)
)
plugin.effect(
    lambda: plugin.get(INSPECTION_TYPE_NAMES_REF).register(
        plugin.scope, {Description: "example.description"}
    )
)
document = inspect(Description("任务说明"), **resolve_inspection_options(plugin))
ctx.dispose()
```

### 分派控制

`tree_rules` 通过 `TreeRules` 将精确类型映射到结构 handler；`interpreters` 是精确类型到解释器的映射。可选的 `tree_resolver=TreeResolver(...)` 和 `interpreter_resolver=callback` 分别覆盖这两侧的默认分派：

| Resolver 返回值 | Tree | Interpretation |
| --- | --- | --- |
| `False` | 使用精确类型的 tree rule | 使用精确类型的解释函数 |
| `True` | 跳过结构展开 | 跳过解释，不创建解释记录 |
| Handler | 使用返回的 `TreeHandler` | 使用返回的 `Interpreter` |

Tree resolver 接收 `value`，每个对象执行一次。自省仅接受 `takes_aux=False`；结构不能依赖路径、环境或访问次数。Interpreter resolver 接收 `(value, env)`，每个对象与环境组合执行一次，即使没有类型规则也可以选择解释器。它返回解释函数，而不是 `Interpretation` 结果。`InterpreterResolver` 是导出的 callable 类型，不需要 resolver 列表或额外的分派对象。两个 resolver 都是显式的单次调用参数，不包含在 `resolve_inspection_options()` 返回的规则快照中。

停止一侧不会停止另一侧。让两侧 resolver 都对某个对象返回 `True`，可以只保留它的引用与基本描述。解释器返回 `None` 仍记录 `unavailable`；resolver 返回 `True` 则不调用解释器。Resolver 的异常向外传播。标量和其他值使用同一分派逻辑，没有内置的终点类型。

`TreeAux.metadata` 中的重建数据不放入观察图。所有结构条目统一使用 `role="child"`。条目的 key 保留原始 TreeKey 对象及共享身份，与 value 一样经过结构展开和解释规则，不调用其 `resolve()` 或 `codify()`。未提供 `children_keys` 时使用顺序下标，下标也参与规则分派。

`slyme.inspection.common` 提供可选的 `KEY_INSPECTION_TREE_RULES` 和 `KEY_INSPECTION_TYPE_NAMES` 来描述标准 TreeKey。每种 key 暴露一个子值，分别为属性名、映射键或下标，语义类型分别为 `slyme.tree.attribute_key`、`slyme.tree.mapping_key` 和 `slyme.tree.sequence_key`。根 Context 安装这些普通且可覆盖的规则，Inspector 内部并不内置这些规则。

框架适配器位于 `slyme.context.inspection` 和 `slyme.node.inspection`，由 `context/default.py` 安装。公开的 `inspect` 直接导出核心引擎，不包含 Context 专用包装。

inspection 不是脱敏机制：解释器增加关系，不替换原始值。导出观测结果的应用应自行选择编码、截断和脱敏策略。

## 内存自省图

返回包含根引用和对象表的普通字典：

```typescript
type Reference = { $ref: string };
interface InspectionGraph {
  root: Reference;
  objects: Record<string, {
    kind: string;
    value?: unknown;
    type?: string;
    label?: string;
    attributes?: Record<string, unknown>;
    entries: Array<{ role: string; key?: Reference; value: Reference }>;
  }>;
}
```

对象 ID 仅在本次观察图内有效，共享对象和循环引用复用同一个引用。每个获准纳入的值都有记录，`raw_value_types` 允许的类型会在 `value` 中保留原始对象；配置为 `None` 时保留所有类型。省略不表示值为 `None`：允许的 `None` 会显式保存。合成的解释记录没有原始 `value`。根引用和条目引用始终指向对象表，`entries` 保留顺序。来源对象的 `interpretation` 条目指向其观测记录，不存储反向的 `source` 或 `parent` 条目；消费端可按需建立入边索引。

`kind` 描述表示类别，可选的 `type` 标识语义，`label` 仅用于显示。内置语义类型为 `slyme.node`、`slyme.wrapper`、`slyme.auto`、`slyme.context`、`slyme.context_view`、`slyme.ref`、`slyme.scope`、`slyme.identity`、`slyme.scope_binding`、`slyme.ref_entry`、`slyme.ref_declaration`、`slyme.ref_leaf_config`、`slyme.ref_container_config` 和 `slyme.compose`。这些标识与适配器字段名独立于实现的模块路径和存储属性。存在类型标识不代表结构展开成功。

| kind | 含义 |
| --- | --- |
| `object` | 已进行结构展开的对象，条目 role 为 `child` |
| `opaque` | 带类型标签的对象引用，未进行结构展开 |
| `interpretation` | 观测记录，包含 `env` 和成功时的 `value`；环境改变时增加 `value_env` |

解释记录的 `attributes.state` 为 `resolved` 或 `unavailable`。Handler、子节点迭代器、resolver 和解释器的运行时异常直接向外传播，不返回部分观察图。普通异常通过 `enrich_exception` 补充对象、环境类型、深度和操作信息，保留原异常身份、调用栈和 cause；控制类异常原样传播。预算和深度截断仍保留在返回图中。

容器与函数类型没有特殊的展示分类。未注册 handler 的函数保持不透明；有 handler 的自定义容器与列表一样，都是 `object`。消费端通过语义类型和配置的描述实现专门展示。

children 按对象身份记录一次，解释结果按对象与环境的身份组合记录一次。不调用相等比较：值相等但身份不同的对象或环境仍然不同，同一个对象则复用。这也适用于标量，Python 可能复用不可变对象。环境覆盖不是原对象时输出 `value_env`。共享容器仍会在每个环境下继续访问其子节点，保证后代 Ref 等得到正确的环境。仅用于标注的环境不会自动展开。对象 ID 不能用于跨观察图识别对象。

观察图强引用允许的原始值，不复制、截断或编码。`raw_value_types=None` 时也包括容器、函数和环境对象；图可能保留循环引用，通常不能直接使用 JSON 序列化。遍历期间内部持有全部观测值；返回图不直接持有被排除的原值，但被允许的容器仍可能间接持有它们。自省完成后，可变值可能继续变化，记录的关系则保持不变。不再需要这些引用时应释放观察图；保留引用并不保证已释放的外部资源仍可用。

序列化由消费端负责。Context 默认省略 Node、Context、函数和容器的原值，因此普通有限标量的观察结果可以使用 JSON 编码。但白名单不是通用保证：NaN 不符合严格 JSON，大整数跨语言可能丢失精度，显式允许的容器可能包含不支持的值或循环。导出方负责这些策略、字符串截断和脱敏；inspection 不定义传输格式或对象重建格式。

没有解释器时不生成解释条目；解释器返回 `None` 记录为 `unavailable`；返回 `Interpretation(None)` 表示已解析的 `None`。不透明或截断的观测缺少子节点或值，不代表它们不存在。`tests/fixtures/inspection` 中的关系快照归一化对象 ID、忽略原始值和显示标签，但保留条目顺序；它们是测试投影，不是导出格式。

## 数量限制

`budget: int | None = None` 限制对象记录数与关系条目数之和。每条记录消耗一个单位，包括标量和解释记录；每个 entry 也消耗一个单位。复用对象不增加记录成本，但新增指向它的关系仍然计数。指定的预算必须为正数，保证根记录始终可以表示。Inspector 将一条关系及其新增目标记录一起检查，第一次无法容纳时停止增加关系，因此可能剩余少量预算。现有记录通过 `attributes.truncated=true`、`reason="budget"` 标记，不额外创建截断对象。所有记录保持可达，不产生悬空引用。 恰好用完预算本身不标记截断：分派和展开仍可能执行，以判断是否需要新增关系。空容器与没有解释器的叶子不需要额外预算。

`max_depth: int | None = None` 限制逻辑探索深度，根深度为零。Tree 的 child、key 和解释结果各增加一层。合成的解释记录和环境标注不增加探索深度，标注的环境不继续展开。达到上限时仍执行 resolver、handler 和 interpreter，以判断是否存在更多内容。空容器、没有解释器的叶子以及不可用的解释结果均完整，不标记截断。实际超出限制的 child 或解释结果被省略，其所属记录标记 `reason="max_depth"`；child 迭代器在第一个被省略的子项处停止。广度优先遍历对每个值与环境组合采用最浅路径。其他环境下复用结构缓存也不能越过深度限制；共享记录可以保留较浅访问的关系，同时标记较深访问的不完整。`max_depth=0` 检查根对象，不继续访问其 children 或解释结果，但仍可能包含解释记录和环境标注。负数深度报错。

两个限制默认都不启用，调试界面等消费端可自行设置。它们约束图记录、关系和探索深度，不限制原始值体积、被持有的内存或回调时间。仅靠深度限制不能约束无限 child 迭代器，需要有限 budget；解释器不断生成新对象形成无限链时，需要有限 budget 或深度限制。始终不返回的回调无法被任何一种限制中断。不完整观测可能缺少通常存在的条目，消费端应检查截断标记。

引擎使用显式工作队列，支持深层结构和循环引用，不依赖递归 Python 调用。Tree 的整树操作同样使用显式栈，但共享对象每次出现都会单独遍历，不支持循环引用；inspection 则将共享对象和循环关系记录为引用。
