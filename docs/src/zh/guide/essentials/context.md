# Context

`Context` 同时提供声明式可变数据视图与生命周期归属。每个 Context 最多有一个 parent，并绑定一个不可变 `Scope`。应用根保存以有效 Schema leaf entry 为 key 的平铺 binding，其中的值按绑定到 Scope 的叶子局部 identity 建立索引。读取默认沿绑定 Scope 的 C3 顺序查找，写入则修改绑定到该 Scope 的 identity。

Context 私有持有三个协作对象：`Schema` 定义路径和 metadata，`ContextStore` 保存分层值及其 viewer，`Lifecycle` 管理 effect 和释放状态。父子树只由 Context 维护，通过 `parent` 和只读的 `children` tuple 快照暴露。同一应用中的 Context 共享 Schema 和 Store，但各自独占一个 Lifecycle。Context 通过 `declare()`、`resolve()`、`resolve_entry()` 和 `entries` 暴露声明操作，调用方不必访问私有对象。`entries` 包含声明的 container 和未赋值 leaf，而 `keys()` 和 `flatten()` 描述可见值。

Context 通过 Schema 解析路径、检查 leaf/container 角色，再将 `RefEntry` 交给 Store。Store 处理分层值和写入模式；Context 根据 Store 返回的可见条目组织 view、keys 和字典。ContextView 只调整相对路径，不重复解析。Binding 和 Store 使用 `get/set/delete` 操作值；取不到可见值时，内部 getter 返回缺值哨兵。存在性检查和条目遍历无需通过异常处理缺值。Context 负责返回用户默认值或抛出 `ContextPathError`；默认值不会隐藏路径未声明的错误。

Context 协调组件归属，ContextView 通过 Context 完成访问。Store 独占维护 viewer 和反向索引，每个私有 binding 则独占维护自己的 identity、值与注册 token，不持有 Store 的状态。字段撤销先移除 Store 索引，再清空 binding 数据，因此值的析构函数可以重新声明同一路径，而旧清理不会删除新数据。Schema 自己登记和解绑 Store，仅向其通知字段撤销；Lifecycle 持有所属 Context，并在 effect 清理后调用其数据释放方法。Scope 只保存可见性信息，不管理可变数据或清理归属。

普通 Context 操作在委托前检查自身 Lifecycle。释放期间可以读取，直到该 Context 完成释放，但禁止写入、声明、新增 effect 和创建子级。内部撤销和 Scope 释放检查精确的持有记录，在清理期间仍可执行。共享的 Schema 和 Store 不采用某个调用者的生命周期状态，其他活跃 Context 可以继续使用它们。

每个 Context binding 按 Scope 保存不可变的 `ScopeBinding`，其中指定 `Identity` 和局部继承阻断；Identity 自身定义共享阻断。可变数据记录保存当前值、注册 token 和仍持有数据的 Scope。Binding 负责这些记录的全部操作，写入不会暴露记录对象。Context 独立管理存储，Compose 则管理应用定义 Layer 中的可撤销登记。

Store 执行 Schema 写入模式，以及注册不能覆盖本地已有值的规则。Binding 提供带 token 的写入和删除：记录保存的 token 为 `None` 时不限制调用方；非 `None` 时必须传入同一个 token，调用方传 `None` 也不能绕过检查。Store 注册会创建唯一 token，并通过 `once()` 包装调用 Binding 的 `matches()` 和 `delete()` 的 disposer。`matches()` 要求本地值存在且 token 精确匹配，因此旧 disposer 不会删除替代值，即使替代值没有 token 保护。

读取会遍历完整的 Scope MRO，跳过未绑定的 Scope，并选择首个值或继承 barrier。读取不会创建绑定或缓存结果，因此下一次读取会看到后续的写入和移除。

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

直接构造 `Ref` 不会修改 Schema。Context 操作会在应用的 Schema 中解析它的路径；
路径角色、声明的 value type 和写入模式仍由该 Schema 决定。Ref 自身只包含路径和
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
`R` 对象。组合代码可以把当前应用的 Schema 简写为局部变量 `R`。`ctx.resolve()` 和 `ctx.resolve_entry()` 查询应用完整的实时声明，包括其他插件声明的路径。

Schema 是按身份比较、支持弱引用的冻结 dataclass。它的属性绑定不能重新赋值或删除，但 `declare()` 和声明的 disposer 仍可修改其内容。初始声明仅作为初始化输入。声明 dict 会被复制，不改变原始输入；导入另一个 Schema 时复制定义，不共享声明所有者。每个 Schema 拥有独立的声明索引和已登记的应用 Store 集合。

Schema 强引用每个应用 Store，Store 的 viewer 登记持有活跃 Context。子 Context 共享 root 的 Store。根释放 viewer 后会注销 Store，清理失败也会注销；根构造失败同样会注销。只要 Schema 仍可达，丢弃 Context 的最后一个外部引用不会释放应用；应显式调用 `dispose()`，并等待可能的异步清理。撤销字段会清除所有已登记 Store 中的对应 binding，但不 dispose 这些 Context。不同 Store 之间的字段清理顺序不作保证。

Schema 校验路径声明，并拒绝互相冲突的 value type 或写入模式，但不校验所存值的运行时类型。

该路径是稳定的语义名称，不依赖物理 Node 图的位置。

`Schema.leaf()` 创建带有可选 value type 和写入 `mode` 的无路径 leaf 配置；默认
`"assign"` 允许 `set()` 和 `delete()`，`"register"` 只允许 `register()` 及其 disposer。
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

`Schema.leaf(metadata=...)` 和 `Schema.container(metadata=...)` 都接受字符串 key 到 `Metadata` 实例的 mapping。建议使用 `cli.option`、`docs.description` 等带应用命名空间的 key。Metadata 不改变路径的 leaf/container 角色、声明的值类型或写入模式。

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
        "status": Schema.leaf(),
        "settings": Schema.leaf(),
    }
)

ctx = Context()
ctx.declare(R)
ctx.update({R.resolve("user.name"): "Ada"})
ctx.update({R.resolve("user.age"): 36, R.resolve("status"): "active"})

assert ctx.get(R.resolve("user.name")) == "Ada"
assert ctx.exists(R.resolve("user.age"))
assert ctx.get("user.name") == "Ada"
assert ctx.extract({"name": R.resolve("user.name"), "age": R.resolve("user.age")}) == {
    "name": "Ada",
    "age": 36,
}
```

`Context(*, parent=None, scope=None, dispose_mode="sequential")` 建立归属和可见性。根创建自己的 Schema 和 Store，并在 `$` 下安装独立的框架配置；子级共享 parent 的 Schema 和 Store，不重复安装默认配置。`root` 是固定字段：根指向自身，子级指向同一个应用根。Context 的属性绑定不可重赋值，但声明的数据仍可修改。创建后通过 `declare()` 声明路径，再用 `update()` 或 `set()` 赋值。所有数据访问都要求路径已声明，包括带 default 的读取和 `exists()` 检查。`update()` 只接受 `assign` 字段。

应用既可以预先构建 Schema，再通过 `ctx.declare(schema)` 导入，也可以直接声明 dict。导入会把当前定义复制到应用自己的 Schema，声明所有权独立；源 Schema 后续的新增或撤销不会自动传播。即使导入同一个源，不同根的 Schema 仍相互独立。同一应用的 Context 所声明的变动，已有 fork 会立即看到：

```python
schema = Schema({"core": {"ready": Schema.leaf()}})
ctx = Context()
remove_core = ctx.declare(schema)

plugin_schema = Schema({"plugin": {"enabled": Schema.leaf()}})
remove_plugin = ctx.declare(plugin_schema)
child = ctx.fork()
assert child.parent is ctx
assert child.scope is ctx.scope

child.set(plugin_schema.resolve("plugin.enabled"), True)
assert child.resolve("plugin.enabled").path == "plugin.enabled"
```

声明 fragment 并不是插件私有的查找空间。插件需要使用其他位置声明的路径时，通过 Context 解析即可：

```python
ready = ctx.resolve("core.ready")
metadata = ctx.resolve_entry("core.ready").config.metadata

remove_plugin()
remove_core()
```

fragment 仍用于声明和导出该插件拥有的路径；`ctx.entries` 列出整个应用的声明并集。
`Schema.declare()` 返回由调用方管理的 disposer。`ctx.declare()` 在应用的 Schema 中登记定义，并把清理作为 `ctx` 拥有的 effect，返回精确的提前 disposer。后续的 `declare()` 或 `update()` 失败不会销毁已经创建的 Context；调用方决定重试还是 dispose。

`Schema(...)`、`Schema.declare(...)` 和 `Context.declare(...)` 的 `declaration` 参数表示一次登记的声明树，其中可以包含多个路径。每个声明的 leaf 及其祖先 container 都将声明者的唯一 ID 映射到原始 config，并缓存合并后的 config。Config 合并会拒绝不兼容的类型和配置项。新增声明会将其合入当前 config；撤销只使缓存失效，下次读取 config 时才按剩余声明的插入顺序重新合并。连续撤销且不读取 config 时，不会反复合并剩余声明。框架内部的撤销、回滚及 Context dispose 不读取 config；用户 cleanup 主动读取 config 时可以触发重算。每次 declare 返回一个 disposer，按登记的逆序释放，仅在最后一个声明者退出后删除定义。某项 cleanup 失败不会阻止其余条目的清理，全部失败以异常组抛出；后续调用重现同一个异常组，不重复清理。尚未调用的 disposer 会持有 Schema 及其条目；首次调用无论成功或失败都会释放这些引用，但失败的 traceback 仍可能保留清理现场。

`set` 和 `update` 只接受声明为 leaf 的路径；`keys` 和 `to_dict(ref)` 只接受声明为 container 的路径；`get`、`exists`、`delete` 和 `drop` 接受任一角色。删除 container 会移除其后代的本地值，不会删除 Schema 声明或继承值。

### 框架配置

`context/default.py` 在根 Context 安装三个 register 模式的 Compose：`$.tree.data`、`$.tree.node` 和 `$.eval.handlers`。分别通过 `DATA_TREE_REF`、`NODE_TREE_REF`、`EVALUATORS_REF` 访问；这些常量由 `slyme.context` 导出。它们和贡献的清理由根生命周期管理，不存在可变的进程级 registry。

这些路径服从普通 Context/Scope 规则。子 Scope 可继承，完全无关的 Scope 必须显式获得配置，不会隐式回退到 root。可以通过 Compose 贡献规则，也可以调用 `ctx.derive(bindings={ref: ScopeBinding(blocked=True)})`，再在返回的子 Context 中注册独立的 Compose。详见 [Tree 规则](../slyme-in-depth/tree-in-slyme.md)。

### 根 container

Context 可读期间，即使没有可见值，`ctx.get("")` 也返回实时的根 `ContextView`，`ctx.exists("")` 为真。`ctx.keys("")` 和 `ctx.to_dict("")` 分别等价于不传参数的调用。新建根的 `keys()`、`to_dict()` 和 `flatten()` 包含 `$`，不会隐藏框架字段；没有可见 leaf 的非根 container 仍视为不存在。

```python
from slyme.context import Context, Ref, Schema

root_ctx = Context()
root_ctx.declare(Schema({"value": Schema.leaf()}))
root_ctx.update({"value": 1})
child_ctx = root_ctx.fork(scope=root_ctx.scope.fork())
root_view = child_ctx.get(Ref(""))
child_ctx.set("value", 2)
assert root_view.get("value") == 2

child_ctx.delete("value")
assert root_view.to_dict(local=True) == {}
assert root_view.get("value") == 1
assert root_ctx.get("value") == 1
root_ctx.dispose()
```

`delete("")` 和 `drop([""])` 会因框架的 register 字段而在任何值被删除前报错；应删除仅含 assign 字段的业务子树。删除 container 保留声明、继承值、隔离 barrier 和 effect。通过 `set`、`register` 或 `update` 给根赋值会被拒绝，因为根是 container。

### 批量更新

`update` 会在写入前校验全部路径和写入模式；`drop` 会在删除前消费并校验全部输入路径，收集其后代叶子。预检失败时 binding 保持不变。实际应用修改时发生的错误或重入副作用不会触发回滚。先删除再更新是两次独立调用，不构成组合事务。

Schema 将应用内的每个有效路径固定为 leaf 或 container 之一，Context 数据不能改变这个角色：删除 value 不会让对应路径变成 container，删除 container 的局部值也不会让对应路径可以写入 leaf value。只要任一匹配声明仍然有效，该路径的角色和写入模式就不能改变。Context 只保存平铺的 leaf binding；访问 container 时先遍历 Schema，再读取相应的 binding。

用户提供的 mapping 始终是原子的 leaf。更深的 Ref 属于 Schema 定义的 container，并不会创建运行时结构：

```python
ctx.set(R.resolve("settings"), {"theme": "dark"})  # 一个以 mapping 为值的 leaf
ctx.set(R.resolve("user.name"), "Ada")  # 一个结构 branch 和 leaf
```

所有读取操作都接受 `local=True`，用于只检查 `ctx.scope`；默认读取 `ctx.scope.mro` 上的有效视图。Context CRUD 不接受单独的 Scope 参数；需要在其他 Scope 读写数据时，应使用绑定到目标 Scope 的 Context。

## Context 生命周期与 Scope 查找 {#scope}

`Context.fork()` 和 `Context.derive()` 创建基础 `Context` 实例；`Scope.fork()` 创建基础 `Scope`。这些方法不传播调用方的 Python 子类，业务扩展应使用 facet 和组合。

`fork()` 创建由当前 Context 管理的子 Context，其 `parent` 为当前 Context。默认情况下两者共享 Scope，因此同一应用根中绑定到该 Scope 的 Context 会看到相同的局部值。需要不同数据层时，应显式传入 child Scope：

```python
from slyme.context import Context, Schema

R = Schema({"settings": {"timeout": Schema.leaf(), "mode": Schema.leaf()}})
root = Context()
root.declare(R)
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
assert agent.to_dict("settings") == {"mode": "fast", "timeout": 45}
```

Context parent 关系与 Scope 祖先关系彼此独立。parent 决定生命周期归属，以及保存 Schema 和数据的应用根；Scope 决定查找顺序。`Scope(*, label=None, parents=())` 接受单个 Scope 或 tuple，在 C3 线性化之前将存储的 `parents` 属性归一化为 tuple。parent 可以来自彼此无关的 Scope 根。`scope.fork()` 创建单 parent 子级。即使复用同一个 Scope 对象，不同 Context 根也不会共享 Context 数据。

单 parent Scope 直接在 parent 已有的 MRO 前加入自身，即使 parent 本身使用多继承也成立。构造成本与该 MRO 的长度呈线性关系。

`scope.find(label)` 按 C3 顺序返回首个 label 相等的 Scope，找不到时抛出 `LookupError`。可以通过 `scope.find(label, default)` 显式提供 Scope 或 `None` 作为回退值；将这个 `None` 传给 `ctx.fork(scope=...)` 会共享当前 Scope。`scope.find_all(label)` 按 C3 顺序返回全部匹配，找不到时返回空 tuple。label 无须可哈希或唯一，也不决定 Scope identity。

删除局部值通常会让 Scope MRO 中的下一个值重新可见。

`fork()` 创建由当前 Context 管理、共享当前 Scope 的子 Context；`fork(scope=...)` 选择已有 Scope。`Identity(label=None, blocked=False)` 是不可变的存储身份：共享要求使用同一对象，而不是相同 label。数据仍局限于单个 Context store 中的某个字段，或单个 Compose。`blocked=True` 为所有使用该 Identity 的 Scope 固定继承阻断。

`ScopeBinding(identity=Identity(), blocked=False)` 描述存储身份和 Scope 局部阻断，默认会为每个实例创建私有 Identity；两级 blocked 均默认为 False。公开 API 只在新 Scope 上安装显式配置，已有绑定不可更改。未绑定 Scope 的首次写入会固定私有、不阻断的绑定。已有值仍然可见；无值时任意一级阻断都会停止 C3 查找，包括后续 parent。Context 和 Compose 不提供原地修改绑定或阻断的公开操作。

数据清理不改变配置。复用已保存的 Scope 会保留该字段的绑定和局部阻断；复用 Identity 会保留身份级阻断。已清理的值不会恢复。撤销 Schema 的最终声明会移除该字段的绑定，但不能改变外部仍持有的 Identity。这些规则控制查找，不是插件之间的权限控制。

`ctx.derive(*, label=None, parents=None, bindings=None, dispose_mode="sequential")` 创建由当前 Context 管理的子 Context，并为全部 target 创建同一个新 Scope。`parents=None` 继承 `ctx.scope`；显式 Scope 或 tuple 指定新 Scope 的直接父级，`()` 创建独立 Scope。生命周期仍由 `ctx` 管理。路径或 Ref key 配置已声明的 leaf；Compose 对象 key 配置贡献层，不替换 Context 中的值。每个值可以是 ScopeBinding 或 Identity。直接传 Identity 等价于 `ScopeBinding(identity=identity)`，映射中的单个值不接受 None。`ScopeBinding()` 创建私有 Identity 且不阻断；`ScopeBinding(identity=shared)` 选择共享存储。两级阻断均需显式设置 `blocked=True`。未指定的 target 沿指定父集继承。省略 bindings、传入 None 或空映射均会创建新 Scope，不配置显式绑定。无参 `ctx.derive()` 等价于 `ctx.fork(scope=ctx.scope.fork())`。共享 identity 不会让新 Scope 的阻断影响无继承关系的其他 Scope：

```python
from slyme.context import Identity

service_schema = Schema({"service": Schema.leaf()})
root = Context()
root.declare(service_schema)
root.update({"service": "default"})
service = service_schema.resolve("service")
isolated = root.derive(bindings={service: ScopeBinding(blocked=True)})
assert not isolated.exists(service)

isolated.set(service, "ready")
assert isolated.get(service) == "ready"
isolated.delete(service)
assert not isolated.exists(service)
assert root.get(service) == "default"
isolated.dispose()

shared = Identity("shared")
left = root.derive(bindings={service: ScopeBinding(identity=shared)})
right = root.derive(bindings={service: ScopeBinding(identity=shared)})
left.set(service, "shared value")
assert right.get(service) == "shared value"
root.dispose()
```

插件重载时，从干净的共享基础 Scope 创建新 Scope，并使用插件独占的子 Context 管理声明、注册、Compose 贡献和清理。在成功释放并停止相关任务后，另一个插件可以不带这些贡献地启动。复用 Scope 或 Identity 表示显式沿用其配置及仍被持有的共享数据；新 Scope 若未阻断，仍会继承祖先。释放不会撤销对共享 assign 数据的任意写入、已存对象的内部修改或文件、网络操作。因此不可变配置和精确撤销支持干净重载，但不是通用事务回滚。

## 可撤销的局部绑定

字段声明为 `mode="register"` 后才能使用 `register()`；绑定的 identity 尚无值时允许注册。调用它的 Context 拥有这次安装并在 dispose 时撤销；返回的幂等 disposer 可用于提前移除：

```python
request_schema = Schema({"request": {"abort": Schema.leaf(mode="register")}})
remove_schema = agent.declare(request_schema)
remove = agent.register(request_schema.resolve("request.abort"), abort_controller)
try:
    run_request()
finally:
    remove()
    remove_schema()
```

继承的值可以被 child Scope 的注册遮蔽。共享同一局部 identity 的 Context 不能注册多个相互竞争的值，必须先撤销已有注册再安装新值。`set()`、`update()`、`update_tree()`、`delete()` 和 `drop()` 都拒绝 `register` 字段，即使当前没有局部值；删除 container 时，只要任一后代是 `register` 字段，整个操作都会被拒绝。`assign` 字段则拒绝 `register()`。这些模式约束绑定的修改方式，不限制所存对象自身是否可变。

Context 强持有 binding 表。注册 disposer 捕获 Binding、Scope 和安装 token，不持有 Store 或内部值记录。调用时，其闭包在 `finally` 中释放 Binding 引用，即使清理失败也会释放；异常 traceback 可能另外持有方法栈帧。尚未调用时，它会保留 Binding 及其中剩余的 identity 数据。最终撤销 Schema 声明会清空整个 Binding，最后一个 viewer 退出时 Scope 释放会清空对应 identity 数据，这些操作不受外部 disposer 持有的影响。

## 扩展方法 {#methods}

`ctx.install(name, func)` 在 `$.methods.<name>` 声明 register 模式字段并注册原始函数。访问 `ctx.name` 时，按照访问方 Context 的 Scope 查找，并将该 Context 绑定为第一个参数。普通属性和方法保持原有行为。返回的 disposer 先撤销方法注册，再撤销声明；安装由共享安装者 Scope 的 sequential 子 Context 管理，即使安装者采用 batch 清理也是如此。安装失败会释放该子 Context。

```python
from slyme.context import Context


def identify(ctx: Context, /) -> Context:
    return ctx


root = Context()
remove = root.install("identify", identify)
child = root.derive()
assert child.identify() is child
assert child.get("$.methods.identify") is identify
remove()
assert not hasattr(child, "identify")
root.dispose()
```

名称遵循 Schema 的单路径段规则，不要求是 Python 标识符：`install("class", func)` 可以通过 `getattr(ctx, "class")` 或 `ctx.get("$.methods.class")(ctx, ...)` 使用。原生成员不能被替换，包括已声明但尚未初始化的 dataclass 字段。其他名称（包括私有及协议名称）不额外保留，安装者自行负责这些名称对语言协议的影响。

独立的子 Scope 可以覆盖继承的方法，撤销后重新显示继承的方法；共享同一本地 identity 的 Context 不能安装相互竞争的方法。每次属性访问重新解析，但已经获取的绑定函数不会被撤销。方法缺失抛出 `AttributeError`，生命周期错误正常传播。调用保留同步结果、awaitable 和函数异常，不调度任务或包装执行。

插件扩展可以使用 `ctx.install("provide", provide)`，然后调用 `ctx.provide(...)`；函数收到的是调用方 Context，不是安装者。核心安装不添加依赖通知，也不改变 `register()` 的语义。按字符串安装方法不会生成静态方法签名，需要时由应用提供类型声明。

## Effect 与 dispose

`ctx.effect(setup)` 管理一次 setup 及其 cleanup。同步 setup 立即运行，并返回提前 disposer；如果 setup 返回 awaitable，`effect()` 则返回一个解析为 disposer 的 awaitable。两种情况统一使用 `await await_result(ctx.effect(setup))`。异步 setup 在启动前就已登记归属：即使调用者没有等待注册，owner 释放时也会等待 setup，再执行其 cleanup。使用取得的资源前必须等待 setup；如果 setup 在返回 cleanup 前失败，部分资源的回滚仍由 setup 自己负责。

parent 会强引用并拥有子 Context。每个 Context 按登记逆序调用直接拥有的 disposer。`dispose_mode="sequential"` 等待每项结束后再调用下一项；`"batch"` 先调用所有项，再一起等待异步结果。构造、`fork()` 和 `derive()` 均独立默认使用串行策略。嵌套分组和共享等待参见[生命周期](./lifecycle.md#清理分组)。`dispose()` 立即执行同步清理；全部完成时返回 `None`，否则返回用于完成剩余异步清理的 awaitable。两种情况统一使用 `await await_result(ctx.dispose())`，其中 `await_result` 从 `slyme.utils.execution` 导入。异步 continuation 在被等待时才调度；丢弃返回值会让释放停留在未完成状态。一旦调度，清理 task 不会因等待者取消而取消。提前 effect disposer 采用相同的完成协议。清理失败不会跳过其余项目，最后抛出异常组，只按登记逆序保存失败，不依赖完成顺序；重复调用共享完成结果、重现最终失败，不会重复清理。已释放的 Context 拒绝后续数据及生命周期操作。

`dispose()` 会在执行任何 cleanup 前，同步禁止整棵所属 Context 子树的修改，包括新增 effect 和子 Context。修改检查只读取接收调用的 Context 自身状态，不受生命周期深度影响。每个 Context 在自身释放完成前仍可读取。尚未轮到清理的子 Context 仍可提前 dispose；已经开始的清理保留原来的共享完成结果。所属子树之外的 Context 即使共享或继承其 Scope，仍可修改。这不会取消正在运行的 Node task，也不会冻结 Context 值中存储的对象。

setup 和 cleanup 不得重入释放自身、所属 Context 或其祖先，也不得等待包含自身的释放操作。这些调用不受支持，Lifecycle 不检测此类重入或等待环。应通知外部协调者执行释放。独立任务可以在 setup 或 cleanup 尚未结束时释放 owner：释放会等待该操作，而该操作不能反过来等待释放。释放期间，尚未完成的 setup 可以完成资源获取并返回 cleanup，但不能新增 Context 修改或注册。

dispose Context 后，它会从 `ctx.scope.mro` 中每个 Scope 的 viewer 集合移除，但不会解除或销毁 `ctx.scope`。通过 `set()` 安装的值，只要同一应用根内仍有活跃 Context 能看到对应的 Context-binding identity，就会继续存储；通过 `register()` 安装的值还会在 owner Context 或其精确 disposer 执行时移除。Compose entry 同样保留到各自的精确 disposer 执行。数据仍然可见并不保证值中的外部资源仍处于打开状态：拥有该资源的 effect 可能已经关闭它。资源所有权应覆盖每个可能使用它的 Context，并且调用方应确定性地 dispose 子 Context，而不是依赖垃圾回收。

Context binding 按 identity 记录仍被观察的 Scope，最后一个 viewer 退出时会清除其值，无须扫描无关的 Scope 绑定。直接复用仍被持有的 Scope，或将它作为祖先使用，都会重新登记 viewer 并保留原有 identity 绑定；已经清除的值不会恢复。

每个应用使用弱键保存 usage 记录，包含 Context viewer 集合与参与绑定的 Schema leaf entry 集合。写入和隔离会登记 entry，普通继承读取不会。删除 container 时先校验后代模式，再将后代 entry 与索引求交集，访问对应 binding。删除值保留索引条目、identity 持有关系和隔离 barrier。最后一个 viewer 退出时释放 identity 持有关系，但保留 Scope 的绑定历史。Schema 最终撤销字段时，所有使用它的应用都会移除对应的精确 binding，并从活跃绑定的索引中移除该 entry。

复用已释放的 Scope（包括作为祖先）时，只访问其记录的 entry 来恢复仍然存在的 identity 持有关系，不扫描 Schema，也不枚举弱键。恢复和释放会顺便移除失效的历史 entry；同路径重新声明会创建不同的 entry，不继承旧绑定。外部仍持有的闲置 Scope 可能保留已撤销的 entry，直到再次使用或被回收。已经清除的值不会恢复。数据 binding 不保证清理顺序；需要有序清理的依赖应由 effect 管理。

Scope viewer 和 binding identity 直接使用集合记录持有者。Context dispose 会移除自己的 viewer 登记，再释放各个 binding 中不再被观察的 Scope；最后一个绑定的 Scope 退出时，移除 identity 及其数据。这些内部登记不为每个成员分配撤销回调。某项清理失败不会阻止其余 binding 和 Scope 的清理，后续调用 Context dispose 会重现最终失败。如果归属项清理和 Scope 释放都失败，Scope 释放的第一个错误会保留为汇总异常的 cause。如果某个 Scope 在值的析构期间重新获得 viewer，后续清理会保留新 viewer 仍可见的数据。

## Compose

`Compose` 负责登记 token、精确撤销和 Scope 可见性。`Compose[L, R]` 分别描述应用定义的 Layer 和默认 query 的结果类型。构造函数接受 `factory` 和可选的 `query`；每个活跃 Identity 都通过 factory 创建独立的 Layer。

公开的 `ComposeLayer` 协议只要求一个同步方法：`register(token, /, *args, **kwargs) -> Callable[[], None]`，返回的 disposer 精确移除本次登记的业务数据，不要求继承该协议。`compose.register(scope, /, *args, **kwargs)` 选择 Identity、注入唯一 token，并原样转发所有业务参数。Layer 不接收隐式 Compose、Scope 或 Identity，其 register 签名决定可接受的业务参数，包括 prepend/append、metadata 等关键字选项。固定依赖可以由 factory 闭包提供；跨层检查放在显式接收 Compose 和 Scope 的上层操作中。

```python
from slyme.context import Compose, Context, Schema

class ValueLayer(dict):
    def register(self, token, /, value):
        self[token] = value

        def dispose():
            del self[token]

        return dispose

root = Context()
root.declare({"tools": Schema.leaf(mode="register")})
tools = Compose(
    factory=ValueLayer,
    query=lambda layers: tuple(
        value for layer in layers for value in layer.values()
    ),
)
root.register("tools", tools)
root.effect(lambda: tools.register(root.scope, "read"))

agent = root.derive(label="agent")
remove_agent = agent.effect(
    lambda: agent.get("tools").register(agent.scope, value="shell")
)
assert tools.resolve(agent.scope) == ("shell", "read")
assert tools.resolve(agent.scope, local=True) == ("shell",)
assert tools.resolve(
    agent.scope, lambda layers: sum(len(layer) for layer in layers)
) == 2

remove_agent()
agent.dispose()
root.dispose()
```

`resolve(scope)` 使用构造时配置的 query；`resolve(scope, query)` 仅覆盖本次查询，可以返回不同类型。未配置默认 query 时必须显式提供，否则 resolve 抛出 ValueError。Query 组合可见的 Layer；每层自行管理存储、索引和本层顺序。

`layers(scope)` 惰性返回实际的 Layer 对象，而不是扁平化贡献或副本。每个共享 Identity 在 C3 顺序中只出现一次。`local=True` 选择当前 Scope 的 Identity，也包含其他共享该 Identity 的 Scope 所登记的内容。每个访问到的绑定都会在当前层之后应用 Scope 级和 Identity 级阻断，即使该层为空或 Identity 已访问过。`layers()` 不传 Scope 时枚举全部活跃层；`local=True` 必须提供 Scope。读取不创建 Layer 或绑定。

不要在查询迭代期间修改 Compose 的结构。调用 handler 前先保存所选 handler 的快照。快照持有对象，但不会延长其外部资源的生命周期。通过 Layer 自己的接口检查业务数据；Compose 不会从仅保存聚合结果的 Layer 重建原始值。

每次成功登记都保留唯一 token 和贡献来源 Scope，直到精确撤销。Compose 返回 `once()` 包装的 disposer，负责移除登记记录，在最终登记退出时移除层，调用 Layer disposer，最后释放 Scope。Layer disposer 不需要另行包装 `once()`。层的清理依据是登记存活状态，而不是层的大小、计算结果或真假值；`len(compose)` 返回活跃登记数量。拒绝登记时必须保持层数据不变。登记应通过 Compose，不要直接调用查询返回的 Layer 的 register。Layer 登记和撤销不得显式重入所属 Compose 的修改操作，框架不对任意层内修改提供事务回滚。清理失败会抛给调用方；重复调用 disposer 重现同一错误，但不重试清理。

`compose.derive(*, label=None, parents=..., binding=...)` 为一个 Compose 创建配置好的 Scope；`Compose.derive_many(*, label=None, parents=..., bindings=...)` 在一个新 Scope 上配置多个 Compose。两者都要求显式 parents，接受 ScopeBinding 或 Identity，且不创建生命周期所有者。使用 `ctx.derive()` 创建受拥有的子 Context，同时配置 Context 字段和 Compose 绑定。未绑定的 Scope 首次登记时固定为私有、未阻断的绑定；撤销不会重置该配置。

`ctx.effect(lambda: ctx.get(ref).register(target_scope, value))` 通过 ctx.scope 获取 Compose，显式选择登记层，并负责撤销，即使 target_scope 与它无关。Context 叶子被替换后，disposer 仍指向原 Compose。直接调用 compose.register 时由调用方负责所有权。子 Context 可以在相同路径登记另一个 Compose，获得独立集合。

## 结构化操作与投影

Context 接受 Ref Tree 进行批量读写。`extract` 会将输入展开一次、校验全部 Ref、读取对应值，再重建一次请求结构；叶子值保持原对象 identity。`update_tree` 从结构一致的 value tree 写入各个路径，复用 `update` 的预检规则。

`ContextView` 只提供 `get/exists/keys/to_dict/flatten`，用于读取和自省子树。它仅保存 Context 与路径前缀并转发读取，不独立管理数据、Scope 或生命周期操作。树形提取使用 `ctx.extract(...)`，传入绝对路径或 Ref；View 不提供 `extract()`，也不依赖 Tree 配置。

`Context.flatten(ref=None, *, local=False)` 接受字符串或 Ref 指定的 container 路径，省略时选择根。ContextView 的 `flatten()` 将自身前缀转发给这个方法。两者都返回绝对 Ref 键，不复制 leaf value。

`keys()`、`ContextView` 和 `to_dict()` 会先遍历 Schema 结构，再读取平铺的 leaf 单元。因此非根的空 container 的声明角色保持稳定，但不会出现在有效数据视图中；根 View 始终可取得。`to_dict()` 将可见 Context leaf 投影为嵌套的普通字典，适合展示或序列化；在不同 Schema 之间，该投影无法区分以 mapping 为值的 leaf 与内容相同的嵌套 Context 路径。`flatten()` 则返回准确的 `dict[Ref, Any]` 可见 leaf 映射：

```python
leaf_schema = Schema({"settings": Schema.leaf()})
tree_schema = Schema({"settings": {"theme": Schema.leaf()}})
mapping_leaf = Context()
mapping_leaf.declare(leaf_schema)
mapping_leaf.update({leaf_schema.resolve("settings"): {"theme": "dark"}})
nested_path = Context()
nested_path.declare(tree_schema)
nested_path.update({tree_schema.resolve("settings.theme"): "dark"})

assert mapping_leaf.get("settings") == nested_path.to_dict("settings")
assert mapping_leaf.flatten() == {
    **mapping_leaf.get("$").flatten(),
    leaf_schema.resolve("settings"): {"theme": "dark"},
}
assert nested_path.get("settings").flatten() == {tree_schema.resolve("settings.theme"): "dark"}
```

两者默认解析绑定 Scope 的 C3 有效视图，也都接受 `local=True`。`ContextView` 只接受相对字符串路径；空字符串表示 View 自身，也是 `keys()` 和 `to_dict()` 的默认路径。绝对 Ref 直接交给 Context 查询。View 的 `flatten()` 仍返回以 Schema 绝对 Ref 为键的字典。两种方法都不会复制 leaf value。
创建新根并声明所有被复制的路径后，调用 `snapshot.update(ctx.flatten("app"))` 物化值；这里选择 `app` 等业务子树，复制的字段必须全部采用 `assign` 模式；整根 flatten 还包含 register 模式的框架配置。
`register` 字段需要在新 owner 上显式调用 `register()`，快照不会转移所有权。
新应用根默认获得新的 Scope，因此看不到目标为源 Scope 的
contribution；显式复用该 Scope 会共享 Compose 可见性，但不同 Context 根仍不会共享
Context 数据。
