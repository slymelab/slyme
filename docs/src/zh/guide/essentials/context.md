# Context

`Context` 是 Slyme 的层次化可变数据存储。Schema 是 leaf/container 结构的唯一来源。应用根为每个有效 Schema leaf 保存一个平铺 binding，每个 binding 再保存各个 Context identity 对应的值。读取默认沿 C3 线性化结果查找，写入则只修改当前 Context 层。

## Ref 与 Schema {#ref}

`Ref` 是标识 Context 依赖的不可变路径值。Schema 记录应用可用的路径，
`resolve()` 返回这些路径的规范 Ref：

```python
from slyme.context import Ref, Schema

schema = Schema({"user": {"name": Schema.leaf(str)}})
name = schema.resolve("user.name")
assert name.path == "user.name"
assert Ref("user.name") == name
```

直接构造 `Ref` 不会修改 Schema。Context 操作会在 `ctx.schema` 中解析它的路径；
路径角色、声明的 value type 和替换策略仍由该 Schema 决定。Ref 自身只包含路径和
缓存的路径分段。

应用应使用 `Schema` 描述可用路径：

```python
from slyme.context import Schema

R = Schema(
    {
        "user": {
            "name": Schema.leaf(str),
            "age": Schema.leaf(),
        },
        "status": Schema.leaf(),
    }
)

name = R.resolve("user.name")
```

`Schema()` 可以从空声明开始，也可以接收初始声明 mapping。Slyme 不导出全局
`R` 对象。组合代码可以把当前应用的 Schema 简写为局部变量 `R`；`ctx.schema`
则提供该应用使用的完整实时 Schema。

Schema 校验路径声明，并拒绝互相冲突的 value type 或替换策略，但不校验所存值的运行时类型。

该路径是稳定的语义名称，不依赖物理 Node 图的位置。

`Schema.leaf()` 创建带有可选 value type 和 `replaceable` 策略的无路径 leaf 配置；
构造过程会将每项配置绑定到完整路径。声明树内不接受其他值。Schema 声明可以通过
`declare()` 可撤销地扩展。

```python
from slyme.context import Schema

R = Schema(
    {
        "input": {
            "": Schema.container(),
            "name": Schema.leaf(str),
            "age": Schema.leaf(),
        },
        "output": {
            "message": Schema.leaf(),
        },
    }
)

assert R.resolve("input").path == "input"
assert R.resolve("input.name").path == "input.name"
R.resolve("input.naem")  # 抛出 KeyError，并提示 "name"
```

每个具名 `Schema.leaf()` entry 声明 leaf；mapping entry 一定声明 container，空 mapping 也
属于 container。可选的空 key `Schema.container()` 会提供该 container 的配置；省略时
Schema 使用默认 container 配置。Schema 只通过 `resolve()` 暴露已声明路径，因此应用路径
不会与未来新增的 Schema 方法冲突。

`declare()` 会原子地递归扩展同一个 Schema 对象，并返回撤销这次声明的幂等 disposer。
多个等价声明拥有相互独立的生命周期。如果已有 Ref 的配置不同，或者 leaf/container
结构冲突，则抛出异常且 Schema 保持不变。某路径的最后一个声明撤销后，该路径及其
运行时 binding 一并消失；之后可以用不同定义重新声明它，旧值不会恢复。

```python
remove_score = R.declare({"output": {"score": Schema.leaf()}})
remove_score()
```

Schema key 必须是非空且不含点号的字符串。`declare`、Python 关键字和以下划线
开头的名称都是合法路径，因为 Schema 不再把路径投影成属性。数量不受限的动态
名称应保存在 `Compose` 等 value 内部，而不应成为 Context 路径。

## 读写

```python
from slyme.context import Context, Schema

R = Schema(
    {
        "user": {"name": Schema.leaf(), "age": Schema.leaf()},
        "status": Schema.leaf(replaceable=False),
        "settings": Schema.leaf(),
    }
)

ctx = Context({R.resolve("user.name"): "Ada"}, schema=R)
ctx.update({R.resolve("user.age"): 36, R.resolve("status"): "active"})

assert ctx.get(R.resolve("user.name")) == "Ada"
assert ctx.exists(R.resolve("user.age"))
assert ctx.get("user.name") == "Ada"
assert ctx.extract({"name": R.resolve("user.name"), "age": R.resolve("user.age")}) == {
    "name": "Ada",
    "age": 36,
}
```

仅限关键字的 `schema` 参数会把这个 Schema 对象本身安装到新的 Context 根。Context 的所有路径都必须已经声明，包括带 default 的读取和 `exists()` 检查。可选的 data mapping 随后等价于调用 `update()`。子 Context 从父级继承同一个 `ctx.schema` 对象，不能再提供另一个。

因此，应用既可以先构建 Schema 再创建 Context，也可以先创建 Context，再通过它声明路径。两种方式修改的是同一个 Schema 对象，已有 fork 会立即看到新增路径：

```python
schema = Schema()
remove_core = schema.declare({"core": {"ready": Schema.leaf()}})
ctx = Context(schema=schema)

plugin_schema = Schema({"plugin": {"enabled": Schema.leaf()}})
remove_plugin = ctx.declare(plugin_schema)
child = ctx.fork()

child.set(plugin_schema.resolve("plugin.enabled"), True)
assert child.schema.resolve("plugin.enabled").path == "plugin.enabled"

remove_plugin()
remove_core()
```

声明 fragment 并不是插件私有的查找空间。插件需要使用其他位置声明的路径时，
应从 Context 获取完整的实时 Schema，并可在图组合代码中保留惯用的短别名：

```python
R = ctx.schema
```

fragment 仍用于声明和导出该插件拥有的路径；`ctx.schema` 是整个应用的并集。插件应像
管理 value 和 Compose entry 的 disposer 一样，管理 `declare()` 返回的 disposer。

`set`、`update` 和 `mutate` 的 update 部分只接受声明为 leaf 的路径；`keys` 和 `to_dict(ref)` 只接受声明为 container 的路径；`get`、`exists`、`delete` 和 `mutate` 的 drop 部分接受任一角色。批量修改会先校验全部路径；发生冲突时不会产生部分写入。

Schema 将应用内的每个有效路径固定为 leaf 或 container 之一，Context 数据不能改变这个角色：删除 value 不会让对应路径变成 container，删除 container 的局部值也不会让对应路径可以写入 leaf value。只要任一匹配声明仍然有效，该路径的角色和替换策略就不能改变。Context 只保存平铺的 leaf binding；访问 container 时先遍历 Schema，再读取相应的 binding。

用户提供的 mapping 始终是原子的 leaf。更深的 Ref 属于 Schema 定义的 container，并不会创建运行时结构：

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # 一个以 mapping 为值的 leaf
ctx.set(R.resolve("user.name"), "Ada")              # 一个结构 branch 和 leaf
```

所有读取操作都接受 `local=True`，用于只检查当前 Context；默认读取 C3 层次合并后的有效视图。

## Fork 与查找

`fork()` 是创建空子 Context 的简写：当前 Context 是它的第一个父级，额外传入的 mixin 按顺序成为后续父级：

```python
R = Schema({"settings": {"timeout": Schema.leaf(), "mode": Schema.leaf()}})
root = Context(schema=R)
root.set(R.resolve("settings.timeout"), 30)

feature = root.fork()
feature.set(R.resolve("settings.mode"), "fast")

mixin = root.fork()
agent = feature.fork(mixin)
assert agent.root is root
assert agent.mro == (agent, feature, mixin, root)
assert agent.to_dict() == {
    "settings": {"mode": "fast", "timeout": 30},
}
```

`mro` 是构造 Context 时计算出的不可变线性化结果，`root` 是其中最后一项。
该根 Context 保存 Schema 引用和平铺的有效 Schema entry 到 binding 索引；所有后代的
`schema` property 都通过 `root` 返回同一个 Schema。两棵独立的 Context 树也可以
有意复用同一个 Schema，而不共享 Context 数据。

C3 层次中的所有直接父级都必须来自同一个应用根，因而共享同一个 `schema` 对象。父级的后续修改会持续可见，直到子 Context 写入同一个 leaf；兄弟分支互不影响。Schema 遍历决定一个 container 包含哪些 leaf，每个 leaf 分别选择 C3 顺序中第一个可见的值。

删除局部值通常会让此前被覆盖的父级值重新可见。

`isolate()` 创建一个子 Context，并阻止指定 leaf 的父级值穿透。私有阻断标记会参与 C3 查找，后续父级无法绕过它。写入隔离后的子 Context 时值正常可见；删除该值后会重新看到阻断状态，而不是父级值：

```python
service_schema = Schema({"service": Schema.leaf(replaceable=False)})
root = Context({"service": "default"}, schema=service_schema)
service = service_schema.resolve("service")
isolated = root.isolate(service)
assert not isolated.exists(service)

isolated.set(service, "ready")
assert isolated.get(service) == "ready"
isolated.delete(service)
assert not isolated.exists(service)
```

## 可撤销的局部绑定

`add()` 仅在当前 Context 不存在该路径时安装值，并返回一个幂等 disposer，用于撤销这一次安装：

```python
request_schema = Schema({"request": {"abort": Schema.leaf()}})
remove_schema = agent.declare(request_schema)
remove = agent.add(request_schema.resolve("request.abort"), abort_controller)
try:
    run_request()
finally:
    remove()
    remove_schema()
```

父级已有同路径值不会阻止子级添加局部值。`add()` 本身不决定之后能否替换：`Schema.leaf(replaceable=False)` 会在同一个 Context 层已有普通值时拒绝 `set()`，默认策略则允许替换。删除和子层 shadow 始终允许。如果 `add()` 创建的精确 entry 已被其他操作删除或替换，原 disposer 不会影响当前值。

## Compose

`Compose` 按 Context identity 保存有序值，并根据目标 Context 的 C3 顺序解析可见 entry。其他 Node 需要通过 Ref 获取 Compose 时，可以把它作为普通 leaf 存入 Context：

```python
from slyme.context import Compose, Context, Schema

R = Schema({"tools": Schema.leaf()})
root = Context(schema=R)
tools = Compose[str, tuple[str, ...]].collect()
root.add(R.resolve("tools"), tools)

remove_base = tools.add(root, "read")
agent = root.fork()
remove_agent = tools.add(agent, "shell", metadata={"plugin": "shell"})

assert agent.get(R.resolve("tools")) is tools
assert tools.resolve(agent) == ("shell", "read")
remove_agent()
remove_base()
```

`Compose.one()` 选择第一个可见值，`Compose.collect()` 将所有可见值组成 tuple，`Compose.merge()` 合并 mapping，并为每个 key 保留第一个可见值。向 `Compose(...)` 传入同步 resolver 可以定义其他结果规则。在同一个 Context 内，`position="prepend"` 将 entry 放在现有 entry 之前；默认值是 `"append"`。

`values(ctx, local=True)` 可在不执行 resolver 的情况下检查单个 Context 的 entry，`resolve(ctx, local=True)` 则对同一组局部值应用 resolver。`entries(ctx)` 返回不可变记录，包括每项的 id、Context、value 与 metadata；省略 `ctx` 会检查当前仍存活的所有 Context。Compose 对 Context 使用弱 key，因此 key 本身不会让 Context 保持存活；但存储的 value 或 metadata 仍可能反向持有该 Context，所以返回的 disposer 才是确定性的清理机制。

子 Context 可以在同一 Ref 上安装新的 Compose 对象，从而得到独立集合。Compose 始终是普通 Context leaf。

## 结构化操作与投影

Context 接受 Ref PyTree 进行批量读写。`extract` 会保持请求的 Python 结构，`update_tree` 则从结构一致的 value tree 写入各个路径。

`keys()`、`ContextView` 和 `to_dict()` 会先遍历 Schema 结构，再读取平铺的 leaf 单元。因此空 container 的声明角色保持稳定，但不会出现在有效数据视图中。`to_dict()` 将可见 Context leaf 投影为嵌套的普通字典，适合展示或序列化；在不同 Schema 之间，该投影无法区分以 mapping 为值的 leaf 与内容相同的嵌套 Context 路径。`flatten()` 则返回准确的 `dict[Ref, Any]` 可见 leaf 映射：

```python
leaf_schema = Schema({"settings": Schema.leaf()})
tree_schema = Schema({"settings": {"theme": Schema.leaf()}})
mapping_leaf = Context(
    {leaf_schema.resolve("settings"): {"theme": "dark"}}, schema=leaf_schema
)
nested_path = Context(
    {tree_schema.resolve("settings.theme"): "dark"}, schema=tree_schema
)

assert mapping_leaf.to_dict() == nested_path.to_dict()
assert mapping_leaf.flatten() == {
    leaf_schema.resolve("settings"): {"theme": "dark"}
}
assert nested_path.flatten() == {tree_schema.resolve("settings.theme"): "dark"}
```

两者默认解析 C3 层次上的有效视图，也都接受 `local=True`。`ContextView` 使用相对
字符串访问子树，而已经解析的 Ref 始终是绝对路径，因此它的 `flatten()` 也返回
Schema 中的绝对 Ref。两种方法都不会复制 leaf value。
`Context(ctx.flatten(), schema=ctx.schema)` 会显式物化一个共享相同声明和可见 leaf
对象、但没有父级的新应用根；它拥有新的 Context identity，因此不会转移源 Context
名下注册的 Compose entry。
