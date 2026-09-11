# Context

`Context` 同时提供声明式可变数据视图与生命周期归属。每个 Context 最多有一个 parent，并绑定一个不可变 `Scope`。应用根保存以有效 Schema leaf entry 为 key 的平铺 binding，其中的值按绑定到 Scope 的 Compose 局部 identity 建立索引。读取默认沿绑定 Scope 的 C3 顺序查找，写入则修改绑定到该 Scope 的 identity。

一棵 Context 树及其可变的 Schema 和 Compose 对象只归属于一个线程。同步 workflow 在该线程使用它们；异步 workflow 则在一个事件循环中使用它们。这是使用约束，而不是运行时线程身份检查。worker 线程和进程应只接收普通值，并把结果返回 owner 线程后再修改 Context。

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
from slyme.context import Context, Schema, Scope

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

仅限关键字的 `schema` 参数会把这个 Schema 对象本身安装到新的 Context 根。Context 的所有路径都必须已经声明，包括带 default 的读取和 `exists()` 检查。可选的 data mapping 随后等价于调用 `update()`。子 Context 从唯一 parent 继承同一个 `ctx.schema` 和应用数据存储，不能再提供另一个 Schema。

因此，应用既可以先构建 Schema 再创建 Context，也可以先创建 Context，再通过它声明路径。两种方式修改的是同一个 Schema 对象，已有 fork 会立即看到新增路径：

```python
schema = Schema()
remove_core = schema.declare({"core": {"ready": Schema.leaf()}})
ctx = Context(schema=schema)

plugin_schema = Schema({"plugin": {"enabled": Schema.leaf()}})
remove_plugin = ctx.declare(plugin_schema)
child = ctx.fork()
assert child.parent is ctx
assert child.scope is ctx.scope

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

fragment 仍用于声明和导出该插件拥有的路径；`ctx.schema` 是整个应用的并集。
`Schema.declare()` 返回由调用方管理的 disposer；`ctx.declare()` 还会把该声明作为
`ctx` 拥有的 effect，同时保留同一个精确的提前 disposer。

每个声明的 leaf 及其祖先 container 都通过一个 Retainer 管理独立声明者。disposer 先释放子路径，再释放 parent，仅在最后一个声明者退出后删除定义。某项 cleanup 失败不会阻止其余清理，后续调用会重现第一个失败。尚未调用或已成功释放的 disposer 不会使 Schema 或其旧定义继续存活；失败的 traceback 则可能保留清理现场。

`set`、`update` 和 `mutate` 的 update 部分只接受声明为 leaf 的路径；`keys` 和 `to_dict(ref)` 只接受声明为 container 的路径；`get`、`exists`、`delete` 和 `mutate` 的 drop 部分接受任一角色。批量修改会先校验全部路径；发生冲突时不会产生部分写入。

Schema 将应用内的每个有效路径固定为 leaf 或 container 之一，Context 数据不能改变这个角色：删除 value 不会让对应路径变成 container，删除 container 的局部值也不会让对应路径可以写入 leaf value。只要任一匹配声明仍然有效，该路径的角色和替换策略就不能改变。Context 只保存平铺的 leaf binding；访问 container 时先遍历 Schema，再读取相应的 binding。

用户提供的 mapping 始终是原子的 leaf。更深的 Ref 属于 Schema 定义的 container，并不会创建运行时结构：

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # 一个以 mapping 为值的 leaf
ctx.set(R.resolve("user.name"), "Ada")              # 一个结构 branch 和 leaf
```

所有读取操作都接受 `local=True`，用于只检查 `ctx.scope`；默认读取 `ctx.scope.mro` 上的有效视图。Context CRUD 不接受单独的 Scope 参数；需要在其他 Scope 读写数据时，应使用绑定到目标 Scope 的 Context。

## Context 生命周期与 Scope 查找 {#scope}

`fork()` 创建由当前 Context 管理的子 Context，其 `parent` 为当前 Context。默认情况下两者共享 Scope，因此同一应用根中绑定到该 Scope 的 Context 会看到相同的局部值。需要不同数据层时，应显式传入 child Scope：

```python
from slyme.context import Context, Schema

R = Schema({"settings": {"timeout": Schema.leaf(), "mode": Schema.leaf()}})
root = Context(schema=R)
root.set(R.resolve("settings.timeout"), 30)

plugin = root.fork()
assert plugin.scope is root.scope

feature_scope = root.scope.fork(name="feature")
mixin_scope = root.scope.fork(name="mixin")
agent_scope = Scope(name="agent", parents=(feature_scope, mixin_scope))

feature = root.fork(scope=feature_scope)
mixin = root.fork(scope=mixin_scope)
agent = feature.fork(scope=agent_scope)
feature.set(R.resolve("settings.mode"), "fast")
mixin.set(R.resolve("settings.timeout"), 45)

assert agent.parent is feature
assert agent.root is root
assert agent.scope.mro == (agent_scope, feature_scope, mixin_scope, root.scope)
assert agent.scope.find("mixin") is mixin_scope
assert agent.to_dict() == {
    "settings": {"mode": "fast", "timeout": 45},
}
```

Context parent 关系与 Scope 祖先关系彼此独立。parent 决定生命周期归属，以及保存 Schema 和数据的应用根；Scope 决定查找顺序。`scope.fork()` 始终创建单 parent 子级；多 parent 必须显式使用 `Scope(parents=(...))` 构造，并满足一致的 C3 线性化，这些 parent 可以来自彼此无关的 Scope 根。即使复用同一个 Scope 对象，不同 Context 根也不会共享 Context 数据。

删除局部值通常会让 Scope MRO 中的下一个值重新可见。

`isolate()` 创建带有 child Scope、由当前 Context 管理的子 Context，并阻止指定 leaf 的祖先值穿透。私有阻断标记会参与 Scope C3 查找，后续 Scope parent 无法绕过它。写入隔离后的子 Context 时值正常可见；删除该值后会重新看到阻断状态，而不是祖先值。多次调用时传入相同的 `identity=`，可以让这些隔离子级共享指定 leaf 的存储，同时仍与 parent 分离：

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

`add()` 仅在绑定 Scope 不存在该路径时安装值。调用它的 Context 会拥有这次安装并在 dispose 时撤销；返回的幂等 disposer 可用于提前移除：

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

Scope 祖先已有同路径值不会阻止在更具体的 Scope 添加值。`add()` 本身不决定之后能否替换：`Schema.leaf(replaceable=False)` 会在同一个 Scope 已有普通值时拒绝 `set()`，默认策略则允许替换。删除和 child Scope shadow 始终允许。如果 `add()` 创建的精确 entry 已被其他操作删除或替换，原 disposer 不会影响当前值。撤销某路径的最后一个 Schema 声明也会释放其隐藏 binding；仍由 Context 持有的旧 `add()` disposer 不会继续保留已移除的值。

## Effect 与 dispose

`ctx.effect(setup)` 会立即同步执行 setup，并拥有其返回的同步 cleanup callable；该方法返回同步的精确提前 disposer。`ctx.async_effect(setup)` 同样同步执行 setup，但拥有其返回的异步 cleanup callable，并返回必须 await 的提前 disposer。在返回的 cleanup 登记完成前，effect setup 不得 dispose owner Context 或其 ancestor。与其他资源获取回调相同，如果 setup 在返回 cleanup 之前抛出异常，它仍须自行撤销部分完成的资源获取。

parent 会强引用并拥有其子 Context。每个 Context 都按后进先出顺序处理自己直接拥有的 effect 与子 Context，并递归销毁子级。`dispose()` 处理完全同步的子树；如果任一后代拥有异步 cleanup，它会在任何清理开始前拒绝整个操作，此时应使用 `await async_dispose()` 处理同步和异步 cleanup。某项 cleanup 失败不会跳过其余清理；子树释放完毕后会重新抛出第一个异常。幂等表示 cleanup 最多执行一次；后续再次调用同一个 effect disposer 或 Context dispose 方法时，会重现其最终失败。已 dispose 的 Context 会拒绝后续 Context 数据访问、修改、fork、effect 与注册操作。

effect cleanup 执行期间不得 dispose 其 owner Context、ancestor 或自身。这类重入操作可能让 cleanup 仍在使用的资源提前失效，或者依赖自身完成，因此 Slyme 会抛出 `RuntimeError`。

dispose Context 后，它会从 `ctx.scope.mro` 中每个 Scope 的 viewer 集合移除，但不会解除或销毁 `ctx.scope`。通过 `set()` 安装的值，只要同一应用根内仍有活跃 Context 能看到对应的 Context-binding identity，就会继续存储；通过 `add()` 安装的值还会在 owner Context 或其精确 disposer 执行时移除。Compose entry 同样保留到各自的精确 disposer 执行。数据仍然可见并不保证值中的外部资源仍处于打开状态：拥有该资源的 effect 可能已经关闭它。资源所有权应覆盖每个可能使用它的 Context，并且调用方应确定性地 dispose 子 Context，而不是依赖垃圾回收。

Context binding 按 identity 记录仍被观察的 Scope，最后一个 viewer 退出时会清除其值，无须扫描无关的 Scope 绑定。直接复用仍被持有的 Scope，或将它作为祖先使用，都会重新登记 viewer 并保留原有 identity 绑定；已经清除的值不会恢复。

Scope viewer 和 binding identity 的成员集合由 Retainer 的回调闭包持有。每次 release 同时移除成员及其保存的释放句柄。最后一个 viewer 退出时，从索引移除 Scope，并释放各个 binding 中对应的 Scope；最后一个绑定的 Scope 退出时，移除 identity 及其数据。某项清理失败不会阻止其余 binding 和 Scope 的清理，后续调用 Context dispose 会重现第一个失败。如果某个 Scope 在值的析构期间重新获得 viewer，后续清理会保留新 viewer 仍可见的数据。

## Compose

`Compose` 在 Compose 局部 identity 下保存有序值，并根据 Scope C3 顺序解析。尚未绑定的 Scope 会在第一次写入时获得私有 identity。`compose.bind(scope_a, scope_b, identity=key)` 可将多个 Scope 一次性绑定到共享 identity；重复绑定到相同 identity 是幂等操作，改绑则会失败。绑定属于不可撤销的结构信息。Node 需要通过 Ref 获取 Compose 时，可以把它作为普通 leaf 存入 Context，再使用 `Context.effect()` 管理 `Compose.add()` 返回的 disposer：

```python
from slyme.context import Compose, Context, Schema

R = Schema({"tools": Schema.leaf(replaceable=False)})
root = Context(schema=R)
tools = Compose[str, tuple[str, ...]].collect()
root.add(R.resolve("tools"), tools)

root.effect(lambda: tools.add(root.scope, "read"))
agent = root.fork(scope=root.scope.fork(name="agent"))
remove_agent = agent.effect(
    lambda: agent.get(R.resolve("tools")).add(
        agent.scope, "shell", metadata={"plugin": "shell"}
    )
)

assert agent.get(R.resolve("tools")) is tools
assert tools.resolve(agent.scope) == ("shell", "read")
remove_agent()
agent.dispose()
root.dispose()
```

`ctx.effect(lambda: ctx.get(ref).add(target_scope, value))` 会通过 `ctx.scope` 查找 Compose，并显式选择 contribution 的目标 `target_scope`。即使目标 Scope 位于其他位置，该 Context 仍拥有 cleanup。即使 Context leaf 后来被替换为另一个 Compose，返回的 disposer 仍会移除原来的 entry。直接调用 `compose.add(scope, value)` 时，调用方须自行管理 disposer。

`Compose.one()` 选择第一个可见值，`Compose.collect()` 将所有可见值组成 tuple，`Compose.merge()` 合并 mapping，并为每个 key 保留第一个可见值。向 `Compose(...)` 传入同步 resolver 可以定义其他结果规则。在同一个 Scope 内，`position="prepend"` 将 entry 放在现有 entry 之前；默认值是 `"append"`。

`values(scope, local=True)` 可在不执行 resolver 的情况下检查该 Scope identity 下的 entry，`resolve(scope, local=True)` 则对同一组值应用 resolver。多个 Scope 共享 identity 时，这一局部集合包含从所有这些 Scope 贡献的 entry；C3 查找只会访问共享 identity 一次。`entries(scope)` 返回不可变记录，包括每项的 id、贡献 Scope、identity、value 与 metadata；省略 Scope 会检查全部当前 entry。Compose 会保留这些 entry，直到精确 disposer 执行，因此应优先使用由生命周期管理的 contribution。

绑定到 child Scope 的 Context 可以在同一 Ref 上安装新的 Compose 对象，从而得到独立集合。Compose 始终是普通 Context leaf。

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

两者默认解析绑定 Scope 的 C3 有效视图，也都接受 `local=True`。`ContextView` 使用相对
字符串访问子树，而已经解析的 Ref 始终是绝对路径，因此它的 `flatten()` 也返回
Schema 中的绝对 Ref。两种方法都不会复制 leaf value。
`Context(ctx.flatten(), schema=ctx.schema)` 会显式物化一个共享相同声明和可见 leaf
对象、但没有 parent 的新应用根。它默认获得新的 Scope，因此看不到目标为源 Scope 的
contribution；显式复用该 Scope 会共享 Compose 可见性，但不同 Context 根仍不会共享
Context 数据。
