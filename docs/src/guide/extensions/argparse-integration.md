# Argparse Integration

::: tip
This feature was added in version 0.1.1.
:::

Slyme provides a built-in `slyme.cli` module to automatically map `Ref` dependencies from the core system into command-line arguments. It is built on top of the Python standard library `argparse`, allowing you to define your data model once and seamlessly gain command-line parsing and Context injection capabilities.

## Core Concepts: `Arg` and `ARG` Metadata

In Slyme, any `Ref` can carry metadata describing how it should behave as an external input. This is achieved using the `Arg` dataclass and the `ARG` metadata key, both exported from `slyme.context`.

`Arg` contains a rich set of configuration options, designed to be compatible with mainstream CLI and configuration tools:

- **`default` / `default_factory`**: The default value of the argument.
- **`help`**: The help description for the argument.
- **`type`**: The argument type. If not provided, Slyme will try to infer it automatically based on the type of `default`.
- **`choices`**: The allowed range of values for the argument.
- **`required`**: Whether the command-line input is required.
- **`nargs`**: The number of command-line arguments to consume.
- **`aliases`**: Aliases for the argument (e.g., `["-lr"]`).
- **`metavar`**: The name displayed in the help message.

## Data Type Support and Conversion Rules

A core advantage of `slyme.cli` is its intelligent type inference and conversion. It automatically registers the corresponding `argparse` processing logic based on the `type` specified in `Arg` (or inferred from the default value). Here is a detailed breakdown of the internal conversion behaviors for various data types:

### 1. Boolean (`bool`)
- **Input Parsing**: The framework automatically converts common strings into boolean values. It supports case-insensitive `yes`/`no`, `true`/`false`, `t`/`f`, `y`/`n`, and `1`/`0`.
- **Argument Behavior**: Defaults to `nargs="?"` and `const=True`. This means you can simply pass `--flag` to imply `True`, or `--flag false` to explicitly state it.
- **Auto `--no-xxx` Flag**: If a boolean argument defaults to `True`, Slyme automatically generates a corresponding negative flag. For example, if the path is `model.use_cache` and it defaults to `True`, a `--no-model-use-cache` flag (equivalent to `action="store_false"`) is added automatically.
- **Default Fallback**: If `required=True` is not set and no default is provided, it falls back to `False`.

### 2. List and Tuple (`list`, `tuple`)
- **Input Parsing**: Automatically maps to argparse's `nargs="+"` (unless `arg.nargs` is explicitly overridden). This allows passing multiple values from the command line.
- **Generic Unpacking**: If a generic type hint is provided (like `list[int]` or `tuple[float, ...]`), the framework extracts the inner type (e.g., `int`, `float`) and applies it to parse every element.
- **Example**: `--server.ports 8080 8081` will be correctly parsed into `[8080, 8081]`.

### 3. Dictionary (`dict`)
- **Input Parsing**: Automatically enables the JSON parser. The command line input must be a valid JSON string.
- **Example**: `--model.config '{"layers": 3, "dim": 512}'`.
- **Note**: Because dictionary objects are difficult to validate using simple `choices` in the CLI layer, `dict` types omit `choices` constraints by default.

### 4. Enum (`Enum`)
- **Input Parsing**: Slyme automatically extracts all `.value` items from the Enum class and registers them as the CLI `choices` constraint.
- **Type Mapping**: The actual parsing type is set to the data type of the Enum values (e.g., `str` or `int`).
- **Default Value Handling**: If an Enum instance is passed as the default (e.g., `Color.RED`), the framework extracts its underlying value to satisfy `argparse`.

### 5. Literal (`Literal`)
- **Input Parsing**: Similar to Enums, Slyme registers all candidate values within the `Literal` as `choices`.
- **Type Mapping**: The type of the argument is mapped to the type of the first element in the `Literal`.
- **Example**: `type=Literal["small", "base"]` restricts user input to these two exact strings.

### 6. Optional (`T | None`)
- **Generic Unpacking**: When a union containing `NoneType` is detected, the framework filters out `None` and extracts the actual type `T` to dispatch the parsing logic. Native Python 3.10 `T | None` syntax is recommended; the equivalent `typing.Optional[T]` remains accepted.

## Comprehensive Example

Combining the type rules above, here is a complete demonstration:

```python
from enum import Enum
from typing import Literal
from slyme.cli import parse_and_inject
from slyme.context import ARG, Arg, Context, Ref, Schema, ref
from slyme.node import node, Auto


class ModelSize(Enum):
    SMALL = "small"
    BASE = "base"


# 1. Declare the application's Schema and Arg metadata
R = Schema(
    {
        "model": {
            "use_cache": ref(
                metadata={ARG: Arg(default=True, help="Whether to use cache")}
            ),
            "config": ref(
                metadata={
                    ARG: Arg(
                        type=dict,
                        required=True,
                        help="Model configuration (JSON string)",
                    )
                }
            ),
            "size": ref(metadata={ARG: Arg(type=ModelSize, default=ModelSize.SMALL)}),
        },
        "server": {
            "ports": ref(
                metadata={
                    ARG: Arg(type=list[int], default=[8080], help="List of ports")
                }
            )
        },
        "run": {
            "mode": ref(
                metadata={ARG: Arg(type=Literal["train", "test"], default="train")}
            )
        },
    }
)


# 2. Define Node
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
    # 3. Instantiate the Node
    server_node = start_server(
        use_cache=R.resolve("model.use_cache"),
        ports=R.resolve("server.ports"),
        config=R.resolve("model.config"),
        size=R.resolve("model.size"),
        mode=R.resolve("run.mode"),
    )

    # Simulating command line execution:
    # python main.py --no-model-use-cache --server.ports 80 443 --model.config '{"debug": true}' --model.size base --run.mode test

    # Discover Arg metadata, parse the CLI, inject values, and execute.
    context = Context(schema=R)
    parse_and_inject(context=context, node=server_node)
    server_node(context)
```

## Core API Reference

### `parse_and_inject`

This API parses arguments independently of Node execution and optionally injects them into a `Context`.

```python
def parse_and_inject(
    context: Context | None = None,
    parser: argparse.ArgumentParser | None = None,
    cli_args: list[str] | None = None,
    node: Any | Iterable[Ref[Any]] | None = None,
    extra_refs: Iterable[Ref[Any]] | None = None,
    extra_args: dict[str, Arg] | None = None,
) -> dict[str, Any] | Context:
```

- **Returns**:
  - If `context` is provided: Updates and returns that same `Context`.
  - If `context` is `None`: Returns the parsed arguments as a `dict[str, Any]`.
- **Argument Sources**: Pass a `node` to scan the dependency tree for Ref metadata, or manually append `extra_refs` and `extra_args`.

It does not create or extend Ref declarations. A supplied Context must already
declare every path that receives a parsed value. Call the Node explicitly after
injection:

```python
context = Context(schema=R)
parse_and_inject(
    context=context,
    node=node_def,
    cli_args=["--model.config", '{"debug": true}'],
)
node_def(context)
```

### `populate_parser` and `prepare_args`

Low-level APIs for fine-grained control. When you already have a custom `argparse.ArgumentParser` instance, you can use these to append Slyme's arguments to your own parser.

- **`prepare_args(node, extra_refs, extra_args)`**: Extracts and merges all Ref and Arg metadata, returning a dictionary `{ "path": Arg }` and handling naming conflicts automatically.
- **`populate_parser(parser, args_map)`**: Mounts the extracted `args_map` into the target `ArgumentParser`.

```python
import argparse
from slyme.cli import prepare_args, populate_parser

# Initialize custom Parser
parser = argparse.ArgumentParser(description="Custom CLI")
parser.add_argument("--verbose", action="store_true")

# Extract dependency parameter map from Node
args_map = prepare_args(node=server_node)

# Mount into custom Parser
populate_parser(parser, args_map)

# Execute unified parsing
args = parser.parse_args()
print(args)
```
