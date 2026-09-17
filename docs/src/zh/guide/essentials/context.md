# Context

`Context` 同时提供声明式可变数据视图与生命周期归属。每个 Context 最多有一个 parent，并绑定一个不可变 `Scope`。应用根保存以有效 Schema leaf entry 为 key 的平铺 binding，其中的值按绑定到 Scope 的叶子局部 identity 建立索引。读取默认沿绑定 Scope 的 C3 顺序查找，写入则修改绑定到该 Scope 的 identity。

Context binding 与 Compose 通过 Compose 的私有静态方法共享 identity 绑定和完整 C3 遍历规则。每个 Context binding 按 identity 保存一个当前值及其撤销 token，并单独记录继承 barrier。Compose 保存带 metadata 的有序 contribution；Context binding 不继承其贡献存储或 API。

查找 identity 时会遍历完整的 Scope MRO，跳过未绑定的 Scope，并且每个共享 identity 只包含一次。读取不会创建绑定或缓存结果，因此下一次读取会看到后续的注册和移除。

一棵 Context 树及其可变的 Schema 和 Compose 对象只归属于一个线程。同步 workflow 在该线程使用它们；异步 workflow 则在一个事件循环中使用它们。这是使用约束，而不是运行时线程身份检查。worker 线程和进程应只接收普通值，并把结果返回 owner 线程后再修改 Context。

## Ref 与 Schema {#ref}

Schema 维护完整路径索引和结构化树，两者引用同一批条目。Context 叶子的读写使用路径索引，容器遍历使用结构化树。私有的逐路径 set/delete 方法统一更新两个索引。结构上的祖先 dict 可以暂时没有声明；添加或删除 container entry 不会删除其子项，删除操作会清理空 dict。因此，这些操作不要求父先子后或子先父后；完整声明仍须持有每个祖先。重新声明已删除的路径会创建新定义，不会恢复旧值。

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

`Ref("")` 表示根 container，其 `parts == ()`；`schema.resolve("")` 返回对应的规范 Ref。根由 Schema 自身永久声明。其他路径内部仍不允许空分段，例如 `".user"`、`"user."` 和 `"user..name"`。

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

`Schema()` 初始只声明根 container，也可以接收初始声明 dict 或另一个 Schema。Slyme 不导出全局
`R` 对象。组合代码可以把当前应用的 Schema 简写为局部变量 `R`；`ctx.schema`
则提供该应用使用的完整实时 Schema。

Schema 是按身份比较、支持弱引用的冻结 dataclass。它的属性绑定不能重新赋值或删除，但 `declare()` 和声明的 disposer 仍可修改其内容。初始声明仅作为初始化输入。声明 dict 会被复制，不改变原始输入；导入另一个 Schema 时复制定义，不共享声明所有者。每个 Schema 拥有独立的声明索引和已登记的 root Context 集合。

Schema 强引用使用它的每个 root Context，直到该 Context 完成 dispose；清理失败也会注销。子 Context 共享 root 的登记，构造失败会撤销自身登记。只要 Schema 仍可达，丢弃 Context 的最后一个外部引用不会释放应用；应显式调用 `dispose()`，并等待可能的异步清理。撤销字段会清除所有已登记 root 中的对应 binding，但不 dispose 这些 Context。不同 root 之间的字段清理顺序不作保证。

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
R.resolve("input.naem")  # 抛出 KeyError
```

每个具名 `Schema.leaf()` entry 声明 leaf；dict entry 一定声明 container，空 dict 也
属于 container。可选的空 key `Schema.container()` 会提供该 container 的配置；省略时
Schema 使用默认 container 配置。Schema 只通过 `resolve()` 暴露已声明路径，因此应用路径
不会与未来新增的 Schema 方法冲突。

根 dict 遵循同样规则：`Schema({"": Schema.container()})` 显式提供根的 container 配置。声明 dict 必须构成有限的树；不支持环，循环结构由 Python 递归执行报错。声明结构归一化后，逐路径合并、登记，保留已有 entry。失败时逆序撤销本次成功登记的声明，删除新增路径，恢复原有所有者及有效配置。登记过程不对同步回调提供隔离；config merge 必须只依赖输入，不观察或修改 Schema。回滚不补偿外部副作用。

`declare()` 会扩展同一个 Schema 对象，失败时回滚，并返回撤销这次声明的幂等 disposer。
多个兼容声明拥有相互独立的生命周期。如果已有 Ref 的行为配置或 metadata 不兼容，或者 leaf/container
结构冲突，则撤销本次已完成的登记后抛出异常。某个非根路径的最后一个声明撤销后，该路径及其
运行时 binding 一并消失；之后可以用不同定义重新声明它，旧值不会恢复。

```python
remove_score = R.declare({"output": {"score": Schema.leaf()}})
remove_score()
```

路径分段名必须是非空且不含点号的字符串，dict 的空 key 专用于该容器自身的配置。`declare`、Python 关键字和以下划线
开头的名称都是合法路径，因为 Schema 不再把路径投影成属性。数量不受限的动态
名称应保存在 `Compose` 等 value 内部，而不应成为 Context 路径。

### Metadata

`Schema.resolve_entry(path)` 返回当前的 `RefEntry`；`Schema.entries` 属性返回包含全部已登记 root、container 和 leaf entry 的 tuple。该 tuple 只快照成员，不快照配置：每个 entry 提供不可变的 `ref`、当前合并后的 `config` 和 `alive` 状态。`slyme.context` 导出 `RefEntry`、`RefConfig`、`RefLeafConfig` 和 `RefContainerConfig`。声明仍优先使用 `Schema.leaf()` 和 `Schema.container()`；登记和撤销由 Schema 管理。

```python
entry = schema.resolve_entry("user.name")
metadata = entry.config.metadata
for entry in schema.entries:
    print(entry.ref.path, entry.config.metadata)
```

查询和枚举 entry 不会合并 config；读取 `entry.config` 时才按需重建失效缓存，已经取得的 config 保持为不可变快照。最后一个声明撤销后，旧 entry 的 `alive` 为 false，读取其 config 抛出 `LookupError`。查询不存在的路径抛出 `KeyError`；重新声明同一路径会创建新 entry，不会恢复旧 entry。

`Schema.leaf(metadata=...)` 和 `Schema.container(metadata=...)` 都接受字符串 key 到 `Metadata` 实例的 mapping。建议使用 `cli.option`、`docs.description` 等带应用命名空间的 key。Metadata 不改变路径的 leaf/container 角色、声明的值类型或替换策略。

`Metadata` 是 frozen dataclass，不是抽象基类。默认 `merge()` 只接受同一个实例；不同实例即使 dataclass 字段相等也会冲突。按内容判断兼容性或组合数据时，覆盖 `merge()`：

```python
from dataclasses import dataclass
from slyme.context import Metadata, Schema


@dataclass(frozen=True)
class Labels(Metadata):
    values: tuple[str, ...]

    def merge(self, other: Metadata) -> "Labels":
        if not isinstance(other, Labels):
            raise ValueError("Incompatible labels")
        return Labels(self.values + other.values)


schema = Schema({"prompt": Schema.leaf(str, metadata={"app.labels": Labels(("core",))})})
remove = schema.declare({"prompt": Schema.leaf(str, metadata={"app.labels": Labels(("plugin",))})})
remove()
```

不同 metadata key 直接共存。同 key 调用已有 item 的 `merge(incoming)`，即使两者是同一个对象也会调用；框架不推断相等性，不递归合并 payload。每次 merge 必须同步、无副作用且只依赖输入 item。每个路径在登记时只合并一次新贡献；缓存失效时，可能需要先重新合并剩余声明。返回不可变且兼容的 item，或抛出冲突。已接受的贡献在任意撤销后，必须仍能按剩余声明的原始顺序合并；不要求交换律或幂等性。撤销和回滚只使 config 缓存失效，不调用 metadata merge。

metadata mapping 会复制并以只读形式暴露，item 保留原引用。Frozen dataclass 防止属性重新赋值，但不会冻结内部的 list 或 dict，payload 的不可变性由实现者保证。跨语言使用相同的字符串 key、声明顺序、默认对象身份比较和显式 merge 方法；JS 实现可用 `Map<string, Metadata>` 存储，不依赖对象属性名或 Python 的相等规则。

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

`Schema(...)`、`Schema.declare(...)` 和 `Context.declare(...)` 的 `declaration` 参数表示一次登记的声明树，其中可以包含多个路径。每个声明的 leaf 及其祖先 container 都将声明者的唯一 ID 映射到原始 config，并缓存合并后的 config。Config 合并会拒绝不兼容的类型和配置项。新增声明会将其合入当前 config；撤销只使缓存失效，下次读取 config 时才按剩余声明的插入顺序重新合并。连续撤销且不读取 config 时，不会反复合并剩余声明。框架内部的撤销、回滚及 Context dispose 不读取 config；用户 cleanup 主动读取 config 时可以触发重算。每次 declare 返回一个 disposer，按登记的逆序释放，仅在最后一个声明者退出后删除定义。某项 cleanup 失败不会阻止其余条目的清理，全部失败以异常组抛出；后续调用重现同一个异常组，不重复清理。尚未调用的 disposer 会持有 Schema 及其条目；首次调用无论成功或失败都会释放这些引用，但失败的 traceback 仍可能保留清理现场。

`set` 和 `update` 只接受声明为 leaf 的路径；`keys` 和 `to_dict(ref)` 只接受声明为 container 的路径；`get`、`exists`、`delete` 和 `drop` 接受任一角色。删除 container 会移除其后代的本地值，不会删除 Schema 声明或继承值。

### 根 container

Context 可读期间，即使没有可见值，`ctx.get("")` 也返回实时的根 `ContextView`，`ctx.exists("")` 为真。`ctx.keys("")` 和 `ctx.to_dict("")` 分别等价于不传参数的调用。空根 View 的 `keys()` 返回 `()`，`to_dict()` 和 `flatten()` 返回 `{}`；没有可见 leaf 的非根 container 仍视为不存在。

```python
from slyme.context import Context, Ref, Schema

root_ctx = Context({"value": 1}, schema=Schema({"value": Schema.leaf()}))
child_ctx = root_ctx.fork(scope=root_ctx.scope.fork())
root_view = child_ctx.get(Ref(""))
child_ctx.set("value", 2)
assert root_view.to_dict() == {"value": 2}

child_ctx.delete("")
assert root_view.to_dict(local=True) == {}
assert root_view.to_dict() == {"value": 1}
assert root_ctx.get("value") == 1
root_ctx.dispose()
```

`delete("")` 和 `drop([""])` 遍历全部已声明 leaf，删除每个 leaf 上绑定到当前 Scope 的 identity 的本地值。它们不会清空全应用数据存储、撤销声明、移除 isolate barrier 或 dispose effect。共享这些 identity 的 Context 会观察到同样的删除，不相关的 identity 不受影响；继承值可能重新可见。通过 `set`、`add` 或 `update` 给根赋值会被拒绝，因为根是 container。

### 批量更新

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

feature_scope = root.scope.fork(label="feature")
mixin_scope = root.scope.fork(label="mixin")
agent_scope = Scope(label="agent", parents=(feature_scope, mixin_scope))

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

`scope.find(label)` 按 C3 顺序返回首个 label 相等的 Scope，找不到时抛出 `LookupError`。可以通过 `scope.find(label, default)` 显式提供 Scope 或 `None` 作为回退值；将这个 `None` 传给 `ctx.fork(scope=...)` 会共享当前 Scope。`scope.find_all(label)` 按 C3 顺序返回全部匹配，找不到时返回空 tuple。label 无须可哈希或唯一，也不决定 Scope identity。

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

Scope 祖先已有同路径值不会阻止在更具体的 Scope 添加值。`add()` 本身不决定之后能否替换：`Schema.leaf(replaceable=False)` 会在同一个 Scope 已有普通值时拒绝 `set()`，默认策略则允许替换。删除和 child Scope shadow 始终允许。如果 `add()` 创建的精确 entry 已被其他操作删除或替换，原 disposer 不会影响当前值。Context 强持有 binding 表；撤销某路径的最后一个 Schema 声明时，会显式移除并清空对应的 binding。`add()` cleanup 在调用前只持有该 binding 的值表，因此由 Context 持有的旧 disposer 既不会滞留已移除的值，也不会影响重新声明后的路径。

## Effect 与 dispose

`ctx.effect(setup)` 管理一次 setup 及其 cleanup。同步 setup 立即运行，并返回提前 disposer；如果 setup 返回 awaitable，`effect()` 则返回一个解析为 disposer 的 awaitable。两种情况统一使用 `await await_result(ctx.effect(setup))`。异步 setup 在启动前就已登记归属：即使调用者没有等待注册，owner 释放时也会等待 setup，再执行其 cleanup。使用取得的资源前必须等待 setup；如果 setup 在返回 cleanup 前失败，部分资源的回滚仍由 setup 自己负责。

parent 会强引用并拥有子 Context。每个 Context 按后进先出顺序处理直接拥有的 effect 与子 Context，并递归销毁子级。`dispose()` 立即执行同步清理；全部完成时返回 `None`，否则返回用于完成剩余异步清理的 awaitable。两种情况统一使用 `await await_result(ctx.dispose())`，其中 `await_result` 从 `slyme.utils.execution` 导入。异步 continuation 在被等待时才调度；丢弃返回值会让释放停留在未完成状态。一旦调度，清理 task 不会因等待者取消而取消。提前 effect disposer 采用相同的完成协议。清理失败不会跳过其余项目，最后抛出异常组，只按清理执行顺序保存失败；重复调用共享完成结果、重现最终失败，不会重复清理。已释放的 Context 拒绝后续数据及生命周期操作。

`dispose()` 会在执行任何 cleanup 前，同步禁止整棵所属 Context 子树的修改，包括新增 effect 和子 Context。修改检查只读取接收调用的 Context 自身状态，不受生命周期深度影响。每个 Context 在自身释放完成前仍可读取。尚未轮到清理的子 Context 仍可提前 dispose；已经开始的清理保留原来的共享完成结果。所属子树之外的 Context 即使共享或继承其 Scope，仍可修改。这不会取消正在运行的 Node task，也不会冻结 Context 值中存储的对象。

`await ctx.adispose()` 是 `await await_result(ctx.dispose())` 的始终可等待的替代写法。调用 `adispose()` 会立即执行相同的同步清理，也可能在返回前抛出同步错误。等待返回值即可完成释放；取消隔离和失败结果重放的语义不变。

effect cleanup 执行期间不得 dispose 其 owner Context、ancestor 或自身。这类重入操作可能让 cleanup 仍在使用的资源提前失效，或者依赖自身完成，因此 Slyme 会抛出 `RuntimeError`。

dispose Context 后，它会从 `ctx.scope.mro` 中每个 Scope 的 viewer 集合移除，但不会解除或销毁 `ctx.scope`。通过 `set()` 安装的值，只要同一应用根内仍有活跃 Context 能看到对应的 Context-binding identity，就会继续存储；通过 `add()` 安装的值还会在 owner Context 或其精确 disposer 执行时移除。Compose entry 同样保留到各自的精确 disposer 执行。数据仍然可见并不保证值中的外部资源仍处于打开状态：拥有该资源的 effect 可能已经关闭它。资源所有权应覆盖每个可能使用它的 Context，并且调用方应确定性地 dispose 子 Context，而不是依赖垃圾回收。

Context binding 按 identity 记录仍被观察的 Scope，最后一个 viewer 退出时会清除其值，无须扫描无关的 Scope 绑定。直接复用仍被持有的 Scope，或将它作为祖先使用，都会重新登记 viewer 并保留原有 identity 绑定；已经清除的值不会恢复。

每个应用使用 `dict[Scope, set[str]]` 记录仍被观察的 Scope 参与绑定的路径。写入和隔离会登记路径，普通继承读取不会。删除 container 时，先将 Schema 的后代路径与索引求交集，再访问对应 binding。删除值保留索引条目、identity 持有关系和隔离 barrier。Scope 释放只访问索引内的 binding，最后一个 viewer 退出后删除该 Scope 的索引。Schema 最终撤销一个路径时，所有使用它的应用都会移除对应的路径索引和 binding。

弱引用成员集合记录哪些 Scope 曾经有过 viewer，不枚举弱引用。新 Scope 无须扫描 binding；复用已释放的 Scope（包括作为祖先）时，扫描当前 Schema 条目以恢复仍然存在的 identity 持有关系，已撤销的定义和已清除的值不会恢复。数据 binding 不保证清理顺序；需要有序清理的依赖应由 effect 管理。

Scope viewer 和 binding identity 直接使用集合记录持有者。Context dispose 会移除自己的 viewer 登记，再释放各个 binding 中不再被观察的 Scope；最后一个绑定的 Scope 退出时，移除 identity 及其数据。这些内部登记不为每个成员分配撤销回调。某项清理失败不会阻止其余 binding 和 Scope 的清理，后续调用 Context dispose 会重现最终失败。如果归属项清理和 Scope 释放都失败，Scope 释放的第一个错误会保留为汇总异常的 cause。如果某个 Scope 在值的析构期间重新获得 viewer，后续清理会保留新 viewer 仍可见的数据。

## Compose

`Compose` 在 Compose 局部 identity 下保存有序值，并根据 Scope C3 顺序解析。尚未绑定的 Scope 会在第一次写入时获得私有 identity。`compose.bind(scope_a, scope_b, identity=key)` 可将多个 Scope 一次性绑定到共享 identity；重复绑定到相同 identity 是幂等操作，改绑则会失败。绑定属于不可撤销的结构信息。Node 需要通过 Ref 获取 Compose 时，可以把它作为普通 leaf 存入 Context，再使用 `Context.effect()` 管理 `Compose.add()` 返回的 disposer：

```python
from slyme.context import Compose, Context, Schema

R = Schema({"tools": Schema.leaf(replaceable=False)})
root = Context(schema=R)
tools = Compose[str, tuple[str, ...]].collect()
root.add(R.resolve("tools"), tools)

root.effect(lambda: tools.add(root.scope, "read"))
agent = root.fork(scope=root.scope.fork(label="agent"))
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

`keys()`、`ContextView` 和 `to_dict()` 会先遍历 Schema 结构，再读取平铺的 leaf 单元。因此非根的空 container 的声明角色保持稳定，但不会出现在有效数据视图中；根 View 始终可取得。`to_dict()` 将可见 Context leaf 投影为嵌套的普通字典，适合展示或序列化；在不同 Schema 之间，该投影无法区分以 mapping 为值的 leaf 与内容相同的嵌套 Context 路径。`flatten()` 则返回准确的 `dict[Ref, Any]` 可见 leaf 映射：

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
Schema 中的绝对 Ref。空字符串表示 View 自身，`Ref("")` 则表示应用根，因此位于非根 View 的范围之外。两种方法都不会复制 leaf value。
`Context(ctx.flatten(), schema=ctx.schema)` 会显式物化一个共享相同声明和可见 leaf
对象、但没有 parent 的新应用根。它默认获得新的 Scope，因此看不到目标为源 Scope 的
contribution；显式复用该 Scope 会共享 Compose 可见性，但不同 Context 根仍不会共享
Context 数据。
