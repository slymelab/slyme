# 自省

`slyme.inspection.inspect()` 将对象结构与依赖环境的解释结果组织成内存图。所有配置均显式传入，不要求存在 Context。

```python
from slyme.inspection import Interpretation, inspect
from slyme.tree.common import DATA_RULES

class Lookup:
    def __init__(self, name):
        self.name = name

shared = [Lookup("answer")]
value = [shared, shared]
graph = inspect(
    value,
    env={"answer": 42},
    tree_rules=DATA_RULES,
    interpreters={Lookup: lambda value, env: Interpretation(env[value.name])},
    raw_value_types=(type(None), bool, int, float, str),
)
root = graph["objects"][graph["root"]["$ref"]]
assert root["entries"][0]["value"] == root["entries"][1]["value"]
```

## 结构与解释

`tree_rules: TreeRules` 提供按精确类型匹配的结构处理器。`flatten_one()` 只展开每个对象一次；共享对象和循环引用复用引用。键和值都参与自省；未提供子键时使用顺序索引。重建用的元数据不会进入结果。

`interpreters` 将精确类型映射到接收 `(value, env)` 的同步 `Interpreter` 函数。返回 `Interpretation(value)` 继承环境，返回 `Interpretation(value, env=...)` 切换环境，返回 `None` 表示不可用。显式传入 `env=None` 清空环境。`Interpretation(None)` 表示已解析的空值，不是不可用。

结构子项和解释结果都会继续展开与解释。子项继承当前环境；解释结果指定的新环境只作用于该结果。结构按对象身份缓存，解释按对象／环境身份组合记录一次。共享容器会在各环境下继续遍历。不使用相等比较去重。

回调必须同步执行，在自省期间保持稳定且无副作用。它们是普通 Python 代码，不受沙箱约束。未知对象保持不透明。自省器不主动调用任意 getter、`repr()`、生成器或 awaitable；访问行为由显式处理器和解释器决定。

## 分派与描述

两个可选 resolver 独立覆盖结构和解释分派：

| 返回值 | `tree_resolver` | `interpreter_resolver` |
| --- | --- | --- |
| `False` | 使用精确类型的结构处理器 | 使用精确类型的解释器 |
| `True` | 跳过结构展开 | 跳过解释且不创建记录 |
| 处理器 | 使用返回的 `TreeHandler` | 使用返回的 `Interpreter` |

结构 resolver 使用 `takes_aux=False` 的 `TreeResolver`，只接收值；结构不能依赖路径或环境。`InterpreterResolver` 接收 `(value, env)`。停止一方不会停止另一方。

`type_names` 将精确类型映射到可选的语义名称，与结构处理器及解释器独立。`raw_value_types` 是决定哪些记录保留原值的精确类型白名单，默认为空，子类需要单独列入。这两项都不改变遍历行为。

## 图与引用持有

`InspectionGraph` 包含 `root: {"$ref": str}` 和 `objects: dict[str, InspectionObject]`。ID 只在单次自省中有效。所有引用都指向表中存在的记录，关系顺序保持不变。

记录包含 `kind`、`entries`，以及可选的 `value`、`type`、`label` 和 `attributes`。每个 `InspectionEntry` 包含 `role`、`value` 引用和可选的 `key` 引用。

| Kind | 内容 |
| --- | --- |
| `object` | 结构性的 `child` 条目 |
| `opaque` | 值的身份与类型标签，未展开结构 |
| `interpretation` | `env`、已解析的 `value`，以及切换环境时的 `value_env` |

源记录通过 `interpretation` 条目指向解释记录，不保存反向 source 或 parent 引用。解释记录的属性包含 `state="resolved"` 或 `state="unavailable"`。环境标注本身不会触发对该环境对象的遍历。

白名单内的原值按引用保留，不复制或编码。遍历期间持有所有访问过的值和环境以保持身份稳定。返回图可能通过允许保留的容器间接持有未列入白名单的对象。修改原值不会更新已记录的关系。序列化和脱敏由消费方负责：有限标量可以进行 JSON 编码，但 NaN、大整数、不支持的对象与环需要序列化器自己的处理策略。自省不定义重建格式。

处理器、迭代器、resolver 和解释器的错误直接传播，不转换为图记录。普通异常通过 `enrich_exception` 补充操作、对象、环境类型及深度信息；控制类异常原样传播。

## 限制

`budget: int | None = None` 限制对象记录和关系条目的总数，每项计一个单位。复用对象节省记录，不免除关系条目的开销。预算必须为正以容纳根记录。每条关系及其新目标一起检查预算；首次无法容纳时停止新增关系，并在受影响记录上设置 `attributes.truncated=True, reason="budget"`。因此可能剩余少量未使用预算。恰好用完预算不会自动标记截断；仍可能执行回调确认是否存在更多内容。所有记录始终可达。

`max_depth: int | None = None` 从根深度零限制探索层数。子键、子值和解释结果增加一层，解释记录和环境标注不增加。达到深度限制时仍执行回调：只有实际省略子项或结果才设置 `reason="max_depth"`，空容器、无解释器的叶子和不可用解释不必截断。子项迭代在首个被省略的子项处停止。广度优先遍历让每个对象／环境组合从最浅路径访问。共享记录可以保留浅层访问产生的关系，同时因另一条更深的访问而标记不完整。负深度会报错。

两个限制默认均不启用。它们限制输出和探索范围，不限制回调耗时或原值体积。仅深度限制无法约束无限子项迭代器，需要有限预算。回调若始终不返回，两种限制都无法中断它。消费方必须检查截断属性，不能直接把缺失关系解释为不存在。
