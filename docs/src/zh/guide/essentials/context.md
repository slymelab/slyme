# Context

`Context` 是 Slyme 的层次化可变数据存储。它的外壳保持 frozen——内部数据对象等属性不能被替换——但数据操作会原地修改该对象并返回 `None`。

## Ref 与 R {#ref}

`Ref` 标识具有语义的数据路径：

```python
from slyme.context import R, Ref

assert R.user.name == Ref("user.name")
```

`R` 是不可变的 `RefFactory`，支持属性语法和可选元数据：

```python
from slyme.context import ARG, Arg, R

name = R.user.name(metadata={ARG: Arg(type=str, required=True, help="User name")})
```

该路径是稳定的语义名称，不依赖物理 Node 图的位置。

## 读写

```python
from slyme.context import Context, R

ctx = Context()
ctx.set(R.user.name, "Ada")
ctx.update({R.user.age: 36, R.status: "active"})

assert ctx.get(R.user.name) == "Ada"
assert ctx.exists(R.user.age)
assert ctx.extract({"name": R.user.name, "age": R.user.age}) == {
    "name": "Ada",
    "age": 36,
}
```

`set`、`update`、`mutate`、`drop`、`delete` 和 `clear` 等修改方法会更新同一个 Context，并返回 `None`。不要写成 `ctx = ctx.set(...)`。

## 结构化操作

Context 接受 Ref PyTree 进行批量读写。`extract` 会保持请求的 Python 结构；`mutate` 对选定路径应用函数。各方法接受的确切 schema 请参考 API 文档。

## 隔离

由于 Context 数据可变，当并发分支需要独立状态时，不能隐式共享同一个 Context，调用方应在分支前显式创建复制的 Context。这使隔离边界清晰可见，同时允许顺序执行高效共享同一个 Context。

Node 参数快照与 Context 数据用途不同：Node 调用只在局部冻结参数容器，而从 Context 取得的值保持其原始 Python 类型。
