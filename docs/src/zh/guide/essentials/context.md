# Context

`Context` 是 Slyme 的层次化可变数据存储。每个实例拥有一棵局部结构树和一组顺序固定的直接父 Context。读取默认沿 C3 线性化结果查找，写入则只修改当前 Context。

## Ref 与 Schema {#ref}

`Ref` 标识具有语义的数据路径：

```python
from slyme.context import Ref

name = Ref("user.name")
assert name.path == "user.name"
```

应用应使用 `Schema` 描述可用路径：

```python
from slyme.context import ARG, Arg, Ref, Schema

R = Schema(
    {
        "user": {
            "name": Ref(metadata={ARG: Arg(type=str, required=True, help="User name")}),
            "age": ...,
        },
        "status": ...,
    }
)

name = R.user.name()
```

`Schema` 始终要求传入声明 mapping。Slyme 不导出全局 `R` 对象。组合代码
可以把当前应用的 Schema 简写为局部变量 `R`；`ctx.schema` 则提供该应用持有
的完整实时 Schema。

Schema 校验路径声明及其 metadata，但不校验这些路径中存储值的运行时类型。

该路径是稳定的语义名称，不依赖物理 Node 图的位置。

`...` 是未绑定 `Ref()` 声明的简写；构造过程会将每项声明绑定到完整路径，
并冻结声明树的私有副本。

```python
from slyme.context import ARG, Arg, Ref, Schema

R = Schema(
    {
        "input": {
            "": Ref(metadata={"description": "应用输入"}),
            "name": Ref(metadata={ARG: Arg(type=str, required=True)}),
            "age": ...,
        },
        "output": {
            "message": ...,
        },
    }
)

assert R.input().path == "input"
assert R.input.name().path == "input.name"
R.input.naem  # 抛出 AttributeError，并提示 "name"
```

每次属性访问仍返回 `Schema` view，因此可直接传给任何接受 `RefLike` 的接口；
调用 view 会返回对应的 `Ref`。Mapping branch 未提供空 key 时会自动获得默认
Ref；空 key 用于配置该 branch 自身的 Ref。

声明树的组合不会修改原对象。Ref 或 `...` entry 是 leaf；mapping entry 是
container，空 mapping 也属于 container。leaf 与 container 合并始终属于结构
冲突。两个 leaf 在默认 `conflict="error"` 下冲突；`conflict="replace"` 选择
右侧 leaf。两个 container 则递归合并。

对于 container 自身的空 key Ref，省略声明与显式 `Ref()` 会被区别记录。
一侧显式声明、另一侧省略时采用显式声明；两侧均显式声明时，`"error"`
报错，`"replace"` 选择右侧。Ref 声明不提供删除操作。

```python
extended = R | {"output": {"score": ...}}
```

Schema key 必须是 Python 标识符。以下划线开头的名称以及 `merge`、`from_refs`
由 API 保留。`Schema.from_refs(...)` 可以从已有的已绑定 Ref 构造声明树。数量
不受限的动态名称应保存在 `Compose` 等 value 内部，而不应成为 Context 路径。

## 读写

```python
from slyme.context import Context, Schema

R = Schema(
    {
        "user": {"name": ..., "age": ...},
        "status": ...,
        "settings": {"": ...},
    }
)

ctx = Context({R.user.name: "Ada"}, schema=R)
ctx.update({R.user.age: 36, R.status: "active"})

assert ctx.get(R.user.name) == "Ada"
assert ctx.exists(R.user.age)
assert ctx.extract({"name": R.user.name, "age": R.user.age}) == {
    "name": "Ada",
    "age": 36,
}
```

仅限关键字的 `schema` 参数用于初始化应用根。Context 的所有路径都必须已经声明，包括带 default 的读取和 `exists()` 检查。可选的 data mapping 随后等价于调用 `update()`。子 Context 从父级继承同一个 `ctx.schema` 对象，不能再提供另一个。

插件可以通过应用内的任意 Context 加入不可变的声明树。已有 fork 会立即看到新增路径；重复加入同一个 `Schema` 对象是幂等操作，且声明不可删除。插件卸载撤销 value 和 Compose entry，而不删除路径声明：

```python
plugin_schema = Schema({"plugin": {"enabled": ...}})
child = ctx.fork()
ctx.declare(plugin_schema)

child.set(plugin_schema.plugin.enabled, True)
assert child.schema.plugin.enabled().path == "plugin.enabled"
```

声明 fragment 并不是插件私有的查找空间。插件需要使用其他位置声明的路径时，
应从 Context 获取完整的实时 Schema，并可在图组合代码中保留惯用的短别名：

```python
R = ctx.schema
```

fragment 仍用于声明和导出该插件拥有的路径；`ctx.schema` 是整个应用的并集。

`set`、`update`、`mutate`、`drop` 和 `delete` 会更新同一个 Context，并返回 `None`。批量修改会先校验全部已声明的局部路径；发生冲突时不会产生部分写入。

在同一个 Context 内，已有路径会保持当前结构角色。leaf 不能隐式变成 container，非空 container 也不能变成 leaf。改变角色前应删除该局部路径；也可以在一次原子 `mutate()` 中删除并重建。container 只是由现存 leaf 推导出的索引，因此每次修改都会移除事务结束后为空的 container。

用户提供的 mapping 始终是 leaf。只有写入更深的 Ref 时才会创建 Context branch：

```python
ctx.set(R.settings, {"theme": "dark"})  # 一个以 mapping 为值的 leaf
ctx.set(R.user.name, "Ada")              # 一个结构 branch 和 leaf
```

所有读取操作都接受 `local=True`，用于只检查当前 Context；默认读取 C3 层次合并后的有效视图。

## Fork 与查找

`fork()` 是创建空子 Context 的简写：当前 Context 是它的第一个父级，额外传入的 mixin 按顺序成为后续父级：

```python
R = Schema({"settings": {"timeout": ..., "mode": ...}})
root = Context(schema=R)
root.set(R.settings.timeout, 30)

feature = root.fork()
feature.set(R.settings.mode, "fast")

mixin = root.fork()
agent = feature.fork(mixin)
assert agent.root is root
assert agent.mro == (agent, feature, mixin, root)
assert agent.to_dict() == {
    "settings": {"mode": "fast", "timeout": 30},
}
```

`mro` 是构造 Context 时计算出的不可变线性化结果，`root` 是其中最后一项。
只有该根 Context 存储应用 Schema；所有后代的 `schema` property 都通过
`root` 返回同一个对象。

C3 层次中的所有直接父级都必须来自同一个应用根，因而共享同一个 `schema` 对象。父级的后续修改会持续可见，直到子 Context 写入优先级更高的值；兄弟分支互不影响。同一路径上的 branch 按 C3 顺序合并，但第一个可见 leaf 会阻断优先级更低的 branch。例如，子级的 `a.b` 会隐藏父级的 `a.b.c`。反过来，即使父级在 `a.b` 保存了 leaf，子级仍可定义 `a.b.c`，因为子级的局部 branch 优先级更高。

删除局部值时会一并移除因此变空的局部路径前缀，并让此前被覆盖的父级值重新可见。

## 可撤销的局部绑定

`add()` 仅在当前 Context 不存在该路径时安装值，并返回一个幂等 disposer，用于撤销这一次安装：

```python
request_schema = Schema({"request": {"abort": ...}})
agent.declare(request_schema)
remove = agent.add(request_schema.request.abort, abort_controller)
try:
    run_request()
finally:
    remove()
```

父级已有同路径值不会阻止子级添加局部值。通过 `add()` 安装的值不能在同一个 Context 中被 `set()` 替换；应先移除它，或者在 fork 出的 Context 中写入。如果该 entry 已被其他操作删除或替换，原 disposer 不会影响当前值。

## Compose

`Compose` 按 Context identity 保存有序值，并根据目标 Context 的 C3 顺序解析可见 entry。其他 Node 需要通过 Ref 获取 Compose 时，可以把它作为普通 leaf 存入 Context：

```python
from slyme.context import Compose, Context, Schema

R = Schema({"tools": ...})
root = Context(schema=R)
tools = Compose[str, tuple[str, ...]].collect()
root.add(R.tools, tools)

remove_base = tools.add(root, "read")
agent = root.fork()
remove_agent = tools.add(agent, "shell", metadata={"plugin": "shell"})

assert agent.get(R.tools) is tools
assert tools.resolve(agent) == ("shell", "read")
remove_agent()
remove_base()
```

`Compose.one()` 选择第一个可见值，`Compose.collect()` 将所有可见值组成 tuple，`Compose.merge()` 合并 mapping，并为每个 key 保留第一个可见值。向 `Compose(...)` 传入同步 resolver 可以定义其他结果规则。在同一个 Context 内，`position="prepend"` 将 entry 放在现有 entry 之前；默认值是 `"append"`。

`values(ctx, local=True)` 可在不执行 resolver 的情况下检查单个 Context 的 entry，`resolve(ctx, local=True)` 则对同一组局部值应用 resolver。`entries(ctx)` 返回不可变记录，包括每项的 id、Context、value 与 metadata；省略 `ctx` 会检查当前仍存活的所有 Context。Compose 对 Context 使用弱 key，因此 key 本身不会让 Context 保持存活；但存储的 value 或 metadata 仍可能反向持有该 Context，所以返回的 disposer 才是确定性的清理机制。

子 Context 可以在同一 Ref 上安装新的 Compose 对象，从而得到独立集合。Compose 始终是普通 Context leaf。

## 结构化操作与投影

Context 接受 Ref PyTree 进行批量读写。`extract` 会保持请求的 Python 结构，`update_tree` 则从结构一致的 value tree 写入各个路径。

`to_dict()` 将 Context 路径投影为嵌套的普通字典，适合展示或序列化；该投影无法区分以 mapping 为值的 leaf 与内容相同的嵌套 Context 路径。`flatten()` 则返回准确的 `dict[Ref, Any]` 可见 leaf 映射：

```python
R = Schema({"settings": {"": ..., "theme": ...}})
mapping_leaf = Context({R.settings: {"theme": "dark"}}, schema=R)
nested_path = Context({R.settings.theme: "dark"}, schema=R)

assert mapping_leaf.to_dict() == nested_path.to_dict()
assert mapping_leaf.flatten() == {R.settings(): {"theme": "dark"}}
assert nested_path.flatten() == {R.settings.theme(): "dark"}
```

两者默认解析 C3 层次上的有效视图，也都接受 `local=True`。`ContextView` 返回相对于自身的路径。两种方法都不会复制 leaf value。`Context(ctx.flatten(), schema=ctx.schema)` 会显式物化一个共享相同声明和可见 leaf 对象、但没有父级的新应用根；它拥有新的 Context identity，因此不会转移源 Context 名下注册的 Compose entry。
