# Context

`Context` 同时提供声明式可变数据视图与生命周期归属。每个 Context 最多有一个 parent，并绑定一个不可变 `Scope`。应用根保存以有效 Schema leaf entry 为 key 的平铺 binding，其中的值按绑定到 Scope 的叶子局部 identity 建立索引。读取默认沿绑定 Scope 的 C3 顺序查找，写入则修改绑定到该 Scope 的 identity。

Context binding 与 Compose 通过 Compose 的私有静态方法共享 identity 绑定和完整 C3 遍历规则。每个 Context binding 按 identity 保存一个当前值及其撤销 token，并单独记录继承 barrier。Compose 保存带 metadata 的有序 contribution；Context binding 不继承其贡献存储或 API。

查找 identity 时会遍历完整的 Scope MRO，跳过未绑定的 Scope，并且每个共享 identity 只包含一次。读取不会创建绑定或缓存结果，因此下一次读取会看到后续的注册和移除。

一棵 Context 树及其可变的 Schema 和 Compose 对象只归属于一个线程。同步 workflow 在该线程使用它们；异步 workflow 则在一个事件循环中使用它们。这是使用约束，而不是运行时线程身份检查。worker 线程和进程应只接收普通值，并把结果返回 owner 线程后再修改 Context。

## Ref 与 Schema {#ref}

Schema 维护完整路径索引和结构化树，两者引用同一批条目。Context 叶子的读写使用路径索引，容器遍历使用结构化树。声明和最终撤销同时更新两个索引，因此重新声明已删除的路径会创建新定义，不会恢复旧值。

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

每个声明的 leaf 及其祖先 container 都使用集合保存声明者的唯一 ID。每次 declare 返回一个 disposer，先释放子路径，再释放 parent，仅在最后一个声明者退出后删除定义。某项 cleanup 失败不会阻止其余清理，后续调用会重现第一个失败。尚未调用或已成功释放的 disposer 不会使 Schema 或其旧定义继续存活；失败的 traceback 则可能保留清理现场。

`set` 和 `update` 只接受声明为 leaf 的路径；`keys` 和 `to_dict(ref)` 只接受声明为 container 的路径；`get`、`exists`、`delete` 和 `drop` 接受任一角色。删除 container 会移除其后代的本地值，不会删除 Schema 声明或继承值。

`update` 会在写入前校验全部路径和替换策略；`drop` 会在删除前消费并校验全部输入路径，收集其后代叶子。预检失败时 binding 保持不变。实际应用修改时发生的错误或重入副作用不会触发回滚。先删除再更新是两次独立调用，不构成组合事务。

Schema 将应用内的每个有效路径固定为 leaf 或 container 之一，Context 数据不能改变这个角色：删除 value 不会让对应路径变成 container，删除 container 的局部值也不会让对应路径可以写入 leaf value。只要任一匹配声明仍然有效，该路径的角色和替换策略就不能改变。Context 只保存平铺的 leaf binding；访问 container 时先遍历 Schema，再读取相应的 binding。

用户提供的 mapping 始终是原子的 leaf。更深的 Ref 属于 Schema 定义的 container，并不会创建运行时结构：

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # 一个以 mapping 为值的 leaf
ctx.set(R.resolve("user.name"), "Ada")  # 一个结构 branch 和 leaf
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

单 parent Scope 直接在 parent 已有的 MRO 前加入自身，即使 parent 本身使用多继承也成立。构造成本与该 MRO 的长度呈线性关系。

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

`ctx.effect(setup)` 管理一次 setup 及其 cleanup。同步 setup 立即运行，并返回提前 disposer；如果 setup 返回 awaitable，`effect()` 则返回一个解析为 disposer 的 awaitable。两种情况统一使用 `await resolve(ctx.effect(setup))`。异步 setup 在启动前就已登记归属：即使调用者没有等待注册，owner 释放时也会等待 setup，再执行其 cleanup。使用取得的资源前必须等待 setup；如果 setup 在返回 cleanup 前失败，部分资源的回滚仍由 setup 自己负责。

parent 会强引用并拥有子 Context。每个 Context 按后进先出顺序处理直接拥有的 effect 与子 Context，并递归销毁子级。`dispose()` 立即执行同步清理；全部完成时返回 `None`，否则返回用于完成剩余异步清理的 awaitable。两种情况统一使用 `await resolve(ctx.dispose())`，其中 `resolve` 从 `slyme.utils.awaitable` 导入。异步 continuation 在被等待时才调度；丢弃返回值会让释放停留在未完成状态。一旦调度，清理 task 不会因等待者取消而取消。提前 effect disposer 采用相同的完成协议。清理失败不会跳过其余项目，最后抛出第一个失败；重复调用共享完成结果、重现最终失败，不会重复清理。已释放的 Context 拒绝后续数据及生命周期操作。

`dispose()` 会在执行任何 cleanup 前，同步禁止整棵所属 Context 子树的修改，包括新增 effect 和子 Context。修改检查只读取接收调用的 Context 自身状态，不受生命周期深度影响。每个 Context 在自身释放完成前仍可读取。尚未轮到清理的子 Context 仍可提前 dispose；已经开始的清理保留原来的共享完成结果。所属子树之外的 Context 即使共享或继承其 Scope，仍可修改。这不会取消正在运行的 Node task，也不会冻结 Context 值中存储的对象。

`await ctx.adispose()` 是 `await resolve(ctx.dispose())` 的始终可等待的替代写法。调用 `adispose()` 会立即执行相同的同步清理，也可能在返回前抛出同步错误。等待返回值即可完成释放；取消隔离和失败结果重放的语义不变。

effect cleanup 执行期间不得 dispose 其 owner Context、ancestor 或自身。这类重入操作可能让 cleanup 仍在使用的资源提前失效，或者依赖自身完成，因此 Slyme 会抛出 `RuntimeError`。

dispose Context 后，它会从 `ctx.scope.mro` 中每个 Scope 的 viewer 集合移除，但不会解除或销毁 `ctx.scope`。通过 `set()` 安装的值，只要同一应用根内仍有活跃 Context 能看到对应的 Context-binding identity，就会继续存储；通过 `add()` 安装的值还会在 owner Context 或其精确 disposer 执行时移除。Compose entry 同样保留到各自的精确 disposer 执行。数据仍然可见并不保证值中的外部资源仍处于打开状态：拥有该资源的 effect 可能已经关闭它。资源所有权应覆盖每个可能使用它的 Context，并且调用方应确定性地 dispose 子 Context，而不是依赖垃圾回收。

Context binding 按 identity 记录仍被观察的 Scope，最后一个 viewer 退出时会清除其值，无须扫描无关的 Scope 绑定。直接复用仍被持有的 Scope，或将它作为祖先使用，都会重新登记 viewer 并保留原有 identity 绑定；已经清除的值不会恢复。

每个应用维护 Scope 到其曾绑定的 Context 叶子的索引。viewer 的登记和释放只访问这些 binding，不遍历应用的所有字段。索引对 Scope 和 binding 均使用弱引用，既支持复用保存的 Scope，又不会让已撤销 Schema 的值或无人使用的 Scope 继续存活。普通继承读取不会增加索引条目。

Scope viewer 和 binding identity 直接使用集合记录持有者。Context dispose 会移除自己的 viewer 登记，再释放各个 binding 中不再被观察的 Scope；最后一个绑定的 Scope 退出时，移除 identity 及其数据。这些内部登记不为每个成员分配撤销回调。某项清理失败不会阻止其余 binding 和 Scope 的清理，后续调用 Context dispose 会重现第一个失败。如果某个 Scope 在值的析构期间重新获得 viewer，后续清理会保留新 viewer 仍可见的数据。

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

每个 Compose identity 的 bucket 就是有序 entry 字典。按唯一 token 删除 entry 后，如果 bucket 为空就将其移除；不另行维护计数，也不为每条 entry 分配内部 release 回调。复用已清空的 identity 会创建新 bucket，旧 disposer 不会误删新 entry。disposer 在调用前保留其 Compose；后续重复调用会重现释放失败，而不会重试 cleanup。Context binding 的某个 identity 最后一个绑定 Scope 不再被观察时，会清除该 identity 的值和 barrier。

绑定到 child Scope 的 Context 可以在同一 Ref 上安装新的 Compose 对象，从而得到独立集合。Compose 始终是普通 Context leaf。

## 结构化操作与投影

Context 接受 Ref Tree 进行批量读写。`extract` 会将输入展开一次、校验全部 Ref、读取对应值，再重建一次请求结构；叶子值保持原对象 identity。`update_tree` 从结构一致的 value tree 写入各个路径，复用 `update` 的预检规则。

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
assert mapping_leaf.flatten() == {leaf_schema.resolve("settings"): {"theme": "dark"}}
assert nested_path.flatten() == {tree_schema.resolve("settings.theme"): "dark"}
```

两者默认解析绑定 Scope 的 C3 有效视图，也都接受 `local=True`。`ContextView` 使用相对
字符串访问子树，而已经解析的 Ref 始终是绝对路径，因此它的 `flatten()` 也返回
Schema 中的绝对 Ref。两种方法都不会复制 leaf value。
`Context(ctx.flatten(), schema=ctx.schema)` 会显式物化一个共享相同声明和可见 leaf
对象、但没有 parent 的新应用根。它默认获得新的 Scope，因此看不到目标为源 Scope 的
contribution；显式复用该 Scope 会共享 Compose 可见性，但不同 Context 根仍不会共享
Context 数据。
