# Context

`Context` 是 Slyme 的层次化可变数据存储。它的外壳保持 frozen——内部数据对象等属性不能被替换——但数据操作会原地修改该对象并返回 `None`。

## Ref 与 RefFactory {#ref}

`Ref` 标识具有语义的数据路径：

```python
from slyme.context import Ref

name = Ref("user.name")
assert name.path == "user.name"
```

应用应使用不可变的 `RefFactory` 统一声明可用路径，之后的属性访问会被限制在
该 schema 内：

```python
from slyme.context import ARG, Arg, Ref, RefFactory

refs = RefFactory(
    {
        "user": {
            "name": Ref(metadata={ARG: Arg(type=str, required=True, help="User name")}),
            "age": ...,
        },
        "status": ...,
    }
)

name = refs.user.name()
```

`RefFactory` 始终要求传入 schema mapping，Slyme 也不再导出全局 Factory，
从而让每个应用的引用命名空间保持显式。

该路径是稳定的语义名称，不依赖物理 Node 图的位置。

`...` 是未绑定 `Ref()` 声明的简写；构造过程会将每项声明绑定到完整路径，
并冻结 schema 的私有副本。

```python
from slyme.context import ARG, Arg, Ref, RefFactory

refs = RefFactory(
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

assert refs.input().path == "input"
assert refs.input.name().path == "input.name"
refs.input.naem  # 抛出 AttributeError，并提示 "name"
```

每次属性访问仍返回 `RefFactory`，因此可直接传给任何接受 `RefLike` 的接口；
无参数调用 Factory 时返回解析 schema 时创建的真实 `Ref`。Mapping branch
未提供空 key 时会自动获得默认 Ref；空 key 用于配置该 branch 自身的 Ref。
Schema 定义应通过生成合并后的新 Factory 进行更新，而不是修改已有声明。

Schema 组合不会修改原对象。Ref 或 `...` entry 是 leaf；mapping entry 是
container，空 mapping 也属于 container。leaf 与 container 合并始终属于结构
冲突。两个 leaf 在默认 `conflict="error"` 下冲突；`conflict="replace"` 选择
右侧 leaf。两个 container 则递归合并。

对于 container 自身的空 key Ref，省略声明与显式 `Ref()` 会被区别记录。
一侧显式声明、另一侧省略时采用显式声明；两侧均显式声明时，`"error"`
报错，`"replace"` 选择右侧。Schema 属于声明，因此不提供删除操作。

```python
extended = refs | {"output": {"score": ...}}
```

Schema key 必须是 Python 标识符。以下划线开头的名称以及 `merge` 由 Factory
API 保留。只有路径确实需要动态生成或专门操作 `Ref` 时，才应显式使用
`Ref("...")`。

## 读写

```python
from slyme.context import Context, RefFactory

refs = RefFactory({"user": {"name": ..., "age": ...}, "status": ...})

ctx = Context()
ctx.set(refs.user.name, "Ada")
ctx.update({refs.user.age: 36, refs.status: "active"})

assert ctx.get(refs.user.name) == "Ada"
assert ctx.exists(refs.user.age)
assert ctx.extract({"name": refs.user.name, "age": refs.user.age}) == {
    "name": "Ada",
    "age": 36,
}
```

`set`、`update`、`mutate`、`drop`、`delete` 和 `clear` 等修改方法会更新同一个 Context，并返回 `None`。不要写成 `ctx = ctx.set(...)`。
批量 mutation 会先校验所有路径，再进行原地修改；发生冲突时不会产生部分写入，
未被替换的 container 也会保持对象身份。

已经存在的路径会保持当前结构角色。leaf 不能隐式变成 container；container
（包括空 container）也不能被 leaf 覆盖。`clear()` 会保留目标的空 container
角色。若要重新定义路径角色，必须先通过 `delete()` 或 `drop()` 删除该路径
本身；`mutate()` 也可以在同一个原子操作中删除并重建同一路径。

## 结构化操作

Context 接受 Ref PyTree 进行批量读写。`extract` 会保持请求的 Python 结构；`mutate` 对选定路径应用函数。各方法接受的确切 schema 请参考 API 文档。

## 隔离

由于 Context 数据可变，当并发分支需要独立状态时，不能隐式共享同一个 Context，调用方应在分支前显式创建复制的 Context。这使隔离边界清晰可见，同时允许顺序执行高效共享同一个 Context。

`Context.clone()` 会创建独立的内部 `ContextData` 层次，同时保留每个已存储叶子的对象身份。修改克隆中的路径不会改变源 Context，但修改共享叶子会被两者同时观察到。`ContextView` 也可以克隆为只包含该子树的独立 Context。

```python
branch = ctx.clone()
branch.set(refs.user.age, 37)
assert ctx.get(refs.user.age) == 36
```
