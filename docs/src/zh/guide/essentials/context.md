# Context

`Context` 是 Slyme 的层次化可变数据存储。每个实例拥有一棵局部结构树和一组顺序固定的直接父 Context。读取默认沿 C3 线性化结果查找，写入则只修改当前 Context。

## Ref 与 Schema {#ref}

`Ref` 是标识 Context 依赖的不可变路径值。Schema 记录应用可用的路径，
`resolve()` 返回这些路径的规范 Ref：

```python
from slyme.context import Ref, Schema, ref

schema = Schema({"user": {"name": ref(str)}})
name = schema.resolve("user.name")
assert name.path == "user.name"
assert Ref("user.name") == name
```

直接构造 `Ref` 不会修改 Schema。Context 操作会在 `ctx.schema` 中解析它的路径；
路径角色与声明的 value type 仍由该 Schema 决定。

应用应使用 `Schema` 描述可用路径：

```python
from slyme.context import Schema, ref

R = Schema(
    {
        "user": {
            "name": ref(str),
            "age": ref(),
        },
        "status": ref(),
    }
)

name = R.resolve("user.name")
```

`Schema()` 可以从空声明开始，也可以接收初始声明 mapping。Slyme 不导出全局
`R` 对象。组合代码可以把当前应用的 Schema 简写为局部变量 `R`；`ctx.schema`
则提供该应用使用的完整实时 Schema。

Schema 校验路径声明，并拒绝互相冲突的 value type 声明，但不校验所存值的运行时类型。

该路径是稳定的语义名称，不依赖物理 Node 图的位置。

`ref()` 创建带有可选 value type 的无路径声明；构造过程会将每项声明绑定到完整
路径。声明树内不接受其他值。Schema 对象本身仍可通过单调的 `declare()` 扩展。

```python
from slyme.context import Schema, ref

R = Schema(
    {
        "input": {
            "": ref(),
            "name": ref(str),
            "age": ref(),
        },
        "output": {
            "message": ref(),
        },
    }
)

assert R.resolve("input").path == "input"
assert R.resolve("input.name").path == "input.name"
R.resolve("input.naem")  # 抛出 KeyError，并提示 "name"
```

每个具名 `ref()` entry 声明 leaf；mapping entry 一定声明 container，空 mapping 也
属于 container。可选的空 key `ref()` 会显式声明该 container 自身的 Ref，而不会让
它成为 leaf；省略时 Schema 会自动生成 container Ref。Schema 只通过 `resolve()`
暴露已声明路径，因此应用路径不会与未来新增的 Schema 方法冲突。

`declare()` 会原子地递归扩展同一个 Schema 对象。等价声明是幂等的；如果已有
Ref 的新声明不同，或者 leaf/container 结构冲突，则抛出异常且 Schema 保持
不变。声明不能被替换或删除。

```python
R.declare({"output": {"score": ref()}})
```

Schema key 必须是非空且不含点号的字符串。`declare`、Python 关键字和以下划线
开头的名称都是合法路径，因为 Schema 不再把路径投影成属性。数量不受限的动态
名称应保存在 `Compose` 等 value 内部，而不应成为 Context 路径。

## 读写

```python
from slyme.context import Context, Schema, ref

R = Schema(
    {
        "user": {"name": ref(), "age": ref()},
        "status": ref(),
        "settings": ref(),
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
schema.declare({"core": {"ready": ref()}})
ctx = Context(schema=schema)

plugin_schema = Schema({"plugin": {"enabled": ref()}})
ctx.declare(plugin_schema)
child = ctx.fork()

child.set(plugin_schema.resolve("plugin.enabled"), True)
assert child.schema.resolve("plugin.enabled").path == "plugin.enabled"
```

声明 fragment 并不是插件私有的查找空间。插件需要使用其他位置声明的路径时，
应从 Context 获取完整的实时 Schema，并可在图组合代码中保留惯用的短别名：

```python
R = ctx.schema
```

fragment 仍用于声明和导出该插件拥有的路径；`ctx.schema` 是整个应用的并集。
插件卸载撤销 value 和 Compose entry，而不删除单调增长的路径声明。

`set`、`update` 和 `mutate` 的 update 部分只接受声明为 leaf 的路径；`keys` 和 `to_dict(ref)` 只接受声明为 container 的路径；`get`、`exists`、`delete` 和 `mutate` 的 drop 部分接受任一角色。批量修改会先校验全部路径；发生冲突时不会产生部分写入。

Schema 将应用内的每个已声明路径固定为 leaf 或 container 之一，Context 数据不能改变这个角色：删除 value 不会让对应路径变成 container，删除 branch 也不会让对应路径可以写入 leaf value。应用可以向 Schema 扩展新路径，但不能改变已有声明的角色。Context 只在 leaf 路径保存 value，并在修改后裁剪空的数据 branch。

用户提供的 mapping 始终是 leaf。只有写入更深的 Ref 时才会创建 Context branch：

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # 一个以 mapping 为值的 leaf
ctx.set(R.resolve("user.name"), "Ada")              # 一个结构 branch 和 leaf
```

所有读取操作都接受 `local=True`，用于只检查当前 Context；默认读取 C3 层次合并后的有效视图。

## Fork 与查找

`fork()` 是创建空子 Context 的简写：当前 Context 是它的第一个父级，额外传入的 mixin 按顺序成为后续父级：

```python
R = Schema({"settings": {"timeout": ref(), "mode": ref()}})
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
该根 Context 保存 Schema 引用；所有后代的 `schema` property 都通过 `root`
返回同一个对象。两棵独立的 Context 树也可以有意复用同一个 Schema，而不共享
Context 数据。

C3 层次中的所有直接父级都必须来自同一个应用根，因而共享同一个 `schema` 对象。父级的后续修改会持续可见，直到子 Context 写入同一个 leaf；兄弟分支互不影响。声明为 container 的 branch 会按 C3 顺序递归合并，每个 leaf 采用第一个定义它的 Context 中的 value。

删除局部值时会一并移除因此变空的局部路径前缀，并让此前被覆盖的父级值重新可见。

## 可撤销的局部绑定

`add()` 仅在当前 Context 不存在该路径时安装值，并返回一个幂等 disposer，用于撤销这一次安装：

```python
request_schema = Schema({"request": {"abort": ref()}})
agent.declare(request_schema)
remove = agent.add(request_schema.resolve("request.abort"), abort_controller)
try:
    run_request()
finally:
    remove()
```

父级已有同路径值不会阻止子级添加局部值。通过 `add()` 安装的值不能在同一个 Context 中被 `set()` 替换；应先移除它，或者在 fork 出的 Context 中写入。如果该 entry 已被其他操作删除或替换，原 disposer 不会影响当前值。

## Compose

`Compose` 按 Context identity 保存有序值，并根据目标 Context 的 C3 顺序解析可见 entry。其他 Node 需要通过 Ref 获取 Compose 时，可以把它作为普通 leaf 存入 Context：

```python
from slyme.context import Compose, Context, Schema, ref

R = Schema({"tools": ref()})
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

`to_dict()` 将 Context 路径投影为嵌套的普通字典，适合展示或序列化；在不同 Schema 之间，该投影无法区分以 mapping 为值的 leaf 与内容相同的嵌套 Context 路径。`flatten()` 则返回准确的 `dict[Ref, Any]` 可见 leaf 映射：

```python
leaf_schema = Schema({"settings": ref()})
tree_schema = Schema({"settings": {"theme": ref()}})
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
