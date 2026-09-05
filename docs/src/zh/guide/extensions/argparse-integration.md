# 自动参数解析

::: tip
此功能在版本 0.1.1 中被添加。
:::

Slyme 提供了一个内置的 `slyme.cli` 模块，用于将核心系统中的 `Ref` 依赖描述自动映射为命令行参数。它基于 Python 标准库 `argparse` 构建，让你只需定义一次数据模型，就能无缝获得命令行解析和 Context 注入能力。

## 核心概念：`Arg` 与 `ARG` 元数据

在 Slyme 中，任何 `Ref` 都可以携带描述自身如何作为外部输入的元数据。这是通过 `Arg` 数据类和 `ARG` 元数据键实现的，两者都可以直接从 `slyme.context` 导入。

`Arg` 包含了丰富的配置项，兼容主流的命令行和配置工具设计：

- **`default` / `default_factory`**：参数的默认值。
- **`help`**：参数的帮助描述信息。
- **`type`**：参数类型。如果不提供，Slyme 会尝试根据 `default` 的类型进行自动推断。
- **`choices`**：允许的参数值范围。
- **`required`**：外部输入是否必需。`Node.run()` 可以从 `inputs`、已有 Context，或者在 `use_argparse=True` 时从命令行获得该输入。
- **`nargs`**：消费的命令行参数个数。
- **`aliases`**：参数的别名（例如 `["-lr"]`）。
- **`metavar`**：在帮助信息中显示的名称。

## 数据类型支持与转换规则

`slyme.cli` 的核心优势在于其智能的类型推断与转换。它会根据 `Arg` 指定的 `type`（或通过默认值推断出的类型）自动注册对应的 argparse 处理逻辑。以下是框架内部对各类数据类型的详细转换行为：

### 1. 布尔类型 (`bool`)
- **输入解析**：框架会自动将常见的字符串转换为布尔值，支持大小写不敏感的 `yes`/`no`, `true`/`false`, `t`/`f`, `y`/`n`, `1`/`0`。
- **参数行为**：默认使用 `nargs="?"` 和 `const=True`。这意味着你可以直接输入 `--flag` 来表示 `True`，或输入 `--flag false` 来显式指定 `False`。
- **反向标志 (`--no-xxx`)**：如果一个布尔参数的默认值为 `True`，Slyme 会自动生成对应的反向标志。例如参数路径为 `model.use_cache`，默认 `True`，则会自动追加 `--no-model-use-cache` 标志（等同于 `action="store_false"`）。
- **默认值回退**：若未要求 `required=True` 且没有提供默认值，框架会自动默认赋值为 `False`。

### 2. 列表与元组 (`list`, `tuple`)
- **输入解析**：自动映射为 argparse 的 `nargs="+"`（除非显式指定了 `arg.nargs`）。这允许你在命令行中提供多个值。
- **泛型解包**：如果提供了泛型注解（如 `list[int]` 或 `tuple[float, ...]`)，框架会自动提取其内部的泛型参数（如 `int`、`float`）并用作每一个元素的类型转换函数。
- **示例**：`--server.ports 8080 8081` 会被解析为 `[8080, 8081]`。

### 3. 字典 (`dict`)
- **输入解析**：自动启用 JSON 解析器。在命令行中传入的必须是合法的 JSON 字符串。
- **示例**：`--model.config '{"layers": 3, "dim": 512}'`。
- **注意**：由于字典对象难以在 CLI 层进行直接比较，框架默认不对 `dict` 类型应用 `choices` 限制。

### 4. 枚举 (`Enum`)
- **输入解析**：Slyme 会自动提取枚举类中的所有 `.value`，并将其注册为 CLI 的 `choices` 限制。
- **类型映射**：参数的实际解析类型会被设置为枚举值的数据类型（如 `str` 或 `int`）。
- **默认值处理**：如果传入了枚举实例作为默认值（如 `Color.RED`），框架会自动提取它的值以适应 argparse。

### 5. 字面量 (`Literal`)
- **输入解析**：与枚举类似，Slyme 会将 `Literal` 中的所有候选值自动注册为 `choices`。
- **类型映射**：参数的类型会被设定为 `Literal` 内第一个元素的数据类型。
- **示例**：`type=Literal["small", "base"]` 会限制用户的输入只能是这两个字符串之一。

### 6. 可选类型 (`T | None`)
- **泛型解包**：当检测到包含 `NoneType` 的联合类型时，框架会自动过滤掉 `None`，并提取实际类型 `T` 进行解析逻辑分发。推荐使用 Python 3.10 原生的 `T | None` 语法；等价的 `typing.Optional[T]` 仍然可用。

## 综合使用示例

结合上面的类型规则，这里提供一个完整的演示用例：

```python
from enum import Enum
from typing import Literal
from slyme.context import ARG, Arg, Context, Ref, Schema
from slyme.node import node, Auto


class ModelSize(Enum):
    SMALL = "small"
    BASE = "base"


# 1. 声明应用的 Ref 与 Arg 元数据
R = Schema(
    {
        "model": {
            "use_cache": Ref(metadata={ARG: Arg(default=True, help="是否使用缓存")}),
            "config": Ref(
                metadata={
                    ARG: Arg(type=dict, required=True, help="模型配置(JSON字符串)")
                }
            ),
            "size": Ref(metadata={ARG: Arg(type=ModelSize, default=ModelSize.SMALL)}),
        },
        "server": {
            "ports": Ref(
                metadata={ARG: Arg(type=list[int], default=[8080], help="端口列表")}
            )
        },
        "run": {
            "mode": Ref(
                metadata={ARG: Arg(type=Literal["train", "test"], default="train")}
            )
        },
    }
)


# 2. 定义 Node
@node
def start_server(
    ctx: Context,
    /,
    *,
    use_cache: Auto[bool],
    ports: Auto[list[int]],
    config: Auto[dict],
    size: Auto[str],
    mode: Auto[str],
):
    print(
        f"Cache: {use_cache}, Ports: {ports}, Config: {config}, Size: {size}, Mode: {mode}"
    )
    return ctx


if __name__ == "__main__":
    # 3. 实例化 Node
    server_node = start_server(
        use_cache=R.model.use_cache,
        ports=R.server.ports,
        config=R.model.config,
        size=R.model.size,
        mode=R.run.mode,
    )

    # 模拟在命令行执行：
    # python main.py --no-model-use-cache --server.ports 80 443 --model.config '{"debug": true}' --model.size base --run.mode test

    # 通过标准应用边界发现 Arg 元数据、解析命令行、校验必需输入、
    # prepare 并执行 Node。
    final_context = server_node.run(use_argparse=True)
```

## 核心 API 参考

### `Node.run`

对于可执行的 Node 树，`run()` 是推荐的应用边界。设置 `use_argparse=True` 后，它会发现树中的所有 `Arg` 并解析命令行参数。已经通过 `context` 或 `inputs` 提供的值可以满足必需参数，并成为解析器默认值；显式传入的命令行值优先级更高。

```python
result = node_def.run(
    use_argparse=True,
    cli_args=["--model.config", '{"debug": true}'],
)
```

省略 `cli_args` 时会解析 `sys.argv[1:]`。与其他 `run()` 调用一样，你也可以把已有 Context 作为第一个位置参数传入，通过 `inputs` 提供程序输入，通过 `outputs` 指定待提取的 Ref PyTree，或者设置 `return_context=True` 返回 `(output, context)`。

如果在 `use_argparse=False` 时提供 `cli_args`，`run()` 会抛出 `ValueError`，而不是静默忽略这些参数。

### `parse_and_inject`

这是一个底层 API，用于独立于 Node 执行过程解析参数，并可选择将结果注入 `Context`。

```python
def parse_and_inject(
    context: Context | None = None,
    parser: argparse.ArgumentParser | None = None,
    cli_args: list[str] | None = None,
    node: Any | Iterable[RefLike] | None = None,
    extra_refs: Iterable[RefLike] | None = None,
    extra_args: dict[str, Arg] | None = None,
) -> dict[str, Any] | Context:
```

- **返回值**：
  - 若提供 `context`：原地更新并返回同一个 `Context`。
  - 若 `context` 为 `None`：返回解析得到的 `dict[str, Any]`。
- **参数来源**：你可以传入 `node` 自动扫描依赖树中所需的全部 `Ref`，或者手动提供 `extra_refs` 和 `extra_args` 来追加。

与 `Node.run()` 不同，这个底层函数不会创建或扩展 Ref 声明。若传入
Context，它必须已经声明所有将要写入解析结果的路径。

### `populate_parser` 与 `prepare_args`

用于底层控制的 API。当你已经有一个自定义的 `argparse.ArgumentParser` 实例时，可以借此将 Slyme 的参数追加到你自己的解析器中。

- **`prepare_args(node, extra_refs, extra_args)`**：提取并合并所有的 `Ref` 和 `Arg`，返回字典 `{ "path": Arg }`，自动处理冲突。
- **`populate_parser(parser, args_map)`**：将提取出的 `args_map` 挂载到指定的 `ArgumentParser`。

```python
import argparse
from slyme.cli import prepare_args, populate_parser

# 初始化自定义 Parser
parser = argparse.ArgumentParser(description="Custom CLI")
parser.add_argument("--verbose", action="store_true")

# 提取 Node 依赖的参数映射
args_map = prepare_args(node=server_node)

# 挂载到自定义 Parser 中
populate_parser(parser, args_map)

# 统一执行解析
args = parser.parse_args()
print(args)
```
