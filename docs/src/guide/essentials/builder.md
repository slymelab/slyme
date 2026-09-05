# Builder

In Slyme, as business logic complexity increases, you often compose many `@node` and `@wrapper` objects into a Node graph. The `@builder` decorator organizes and reuses that assembly logic.

Simply put, Builder is a factory function specifically for instantiating and assembling Nodes.

## @builder Decorator

The `@builder` decorator marks reusable Node assembly logic. It checks only that the outer result is a `Node` or `AsyncNode`; it does not inspect the returned object's nested parameter graph.

### Basic Usage

You can define a Builder like a normal function, just add the `@builder` decorator:

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import Schema
# Assume nodes are already defined
# from my_nodes import load_data, process_data, save_data

R = Schema({"process_config": ..., "output_path": ...})


@builder
def create_data_pipeline(source_path: str):
    # 1. Instantiate each Node — pass paths from R via keyword arguments
    load_node = load_data(path=source_path)
    process_node = process_data(config=R.process_config)
    save_node = save_data(output=R.output_path)

    # 2. Assemble and return a complete Node tree
    return sequential(nodes=[load_node, process_node, save_node])
```

Calling a Builder function does not execute the result—it runs only the assembly logic and returns the outermost `Node` or `AsyncNode` instance:

```python
pipeline = create_data_pipeline("/path/to/data")
# ctx = pipeline(ctx)
```

::: tip
`@builder` checks the function's direct return value. A missing `return` produces a clear `ValueError`; any other value that is not a `Node` or `AsyncNode` produces a `TypeError`. This is a local root check, not recursive graph validation.
:::

## Composition and Dynamic Modification

Builder's biggest advantage is **reusability**. One Builder can call another, and the returned live Node graph can be modified before or between calls.

This is very useful when building different variants of pipelines, avoiding a lot of repetitive template code:

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import Schema

R = Schema({"default_config": ..., "output_path": ...})


@builder
def base_pipeline():
    return sequential(
        nodes=[
            load_data(path="default_path"),
            process_data(config=R.default_config),
        ]
    )


@builder
def custom_pipeline(new_path: str):
    # 1. Get the base Node tree
    pipeline = base_pipeline()

    # 2. Dynamically modify specific Node's build-time parameters
    pipeline.nodes[0].path = new_path
    pipeline.nodes.append(save_data(output=R.output_path))
    return pipeline
```

Builder functions and ordinary parameter structures may nest freely. Local checks apply only to active execution roles: wrapper modes must match their Node, `sequential` accepts only synchronous Nodes, and `async_sequential` accepts synchronous or asynchronous Nodes.
