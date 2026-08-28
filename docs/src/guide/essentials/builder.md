# Builder

In Slyme, as business logic complexity increases, you often compose many `@node` and `@wrapper` objects into a Node graph. The `@builder` decorator organizes and reuses that assembly logic.

Simply put, Builder is a factory function specifically for instantiating and assembling Nodes.

## @builder Decorator

The `@builder` decorator's core responsibility is to wrap your assembly logic and perform a series of safety checks when the function returns, ensuring you build a legal and robust Node tree.

### Basic Usage

You can define a Builder like a normal function, just add the `@builder` decorator:

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import R
# Assume nodes are already defined
# from my_nodes import load_data, process_data, save_data

@builder
def create_data_pipeline(source_path: str):
    # 1. Instantiate each Node — pass Refs directly via keyword arguments
    load_node = load_data(path=source_path)
    process_node = process_data(config=R.process_config)
    save_node = save_data(output=R.output_path)

    # 2. Assemble and return a complete Node tree
    return sequential(nodes=[load_node, process_node, save_node])
```

Calling a Builder function does not execute the Node—it runs only the assembly logic and returns the outermost Node instance:

```python
pipeline = create_data_pipeline("/path/to/data")
# ctx = pipeline(ctx)
```

::: tip
`@builder` automatically checks the function's return value. If you forget the `return` when writing complex branching logic (causing it to return `None`), the framework will raise a clear `ValueError` exception, reminding you to return the built Node instance.
:::

### Structure Validation

By default, `@builder` automatically calls internal `check_node_structure` for deep structural legality validation of the entire Node tree before returning. As mentioned in the [Node Structure Validation](/guide/essentials/node#node-struct) chapter, Slyme has strict constraints on the mutual holding relationships between different Node types (for example, `@wrapper` can only be mounted as middleware and cannot be passed as a parameter to `@node`, etc.).

If in some special scenarios (like an extremely frequently called internal sub-Builder, for performance reasons) you need to turn off this structural validation, you can explicitly pass `check_structure=False`:

```python
from slyme.builder import builder

@builder(check_structure=False)
def fast_internal_builder():
    # The Node returned here will skip structure validation
    return load_data(path="...")
```

## Composition and Dynamic Modification

Builder's biggest advantage is **reusability**. One Builder can call another, and the returned live Node graph can be modified before or between calls.

This is very useful when building different variants of pipelines, avoiding a lot of repetitive template code:

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import R

@builder
def base_pipeline():
    return sequential(nodes=[
        load_data(path="default_path"),
        process_data(config=R.default_config),
    ])

@builder
def custom_pipeline(new_path: str):
    # 1. Get the base Node tree
    pipeline = base_pipeline()

    # 2. Dynamically modify specific Node's build-time parameters
    pipeline.nodes[0].path = new_path
    pipeline.nodes.append(
        save_data(output=R.output_path)
    )
    return pipeline
```

Through this approach, you can combine small Builder blocks into large systems like building with LEGO, while maintaining extreme flexibility.
