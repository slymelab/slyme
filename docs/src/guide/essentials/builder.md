# Builder

In Slyme, as business logic complexity increases, you often need to compose many Nodes (such as `@node`, `@expression`, `@wrapper`) to build a complex execution tree (Node tree). To better manage and reuse these building logics, Slyme introduced the concept of **Builder** and provides the `@builder` decorator.

Simply put, Builder is a factory function specifically for instantiating and assembling Nodes.

## @builder Decorator

The `@builder` decorator's core responsibility is to wrap your assembly logic and perform a series of safety checks when the function returns, ensuring you build a legal and robust Node tree.

### Basic Usage

You can define a Builder like a normal function, just add the `@builder` decorator:

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import Ref
# Assume nodes are already defined
# from my_nodes import load_data, process_data, save_data

@builder
def create_data_pipeline(source_path: str):
    scope = {
        "config": Ref("process_config"),
        "output": Ref("output_path"),
    }
    # 1. Instantiate each Node (Def phase)
    load_node = load_data(path=source_path)
    process_node = process_data(scope)
    save_node = save_data(scope)

    # 2. Assemble and return a complete Node tree
    return sequential(nodes=[load_node, process_node, save_node])
```

Calling a Builder function does not execute the Node — it merely executes the internal assembly logic and returns the outermost Node Def instance:

```python
pipeline_def = create_data_pipeline("/path/to/data")

# Convert to Exec (execution phase)
pipeline_exec = pipeline_def.prepare()
# ctx = pipeline_exec(ctx)
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

Builder's biggest advantage is **reusability**. You can have one Builder call another Builder, and thanks to Slyme Node's modifiable nature before `.prepare()`, you can easily fine-tune existing building results.

This is very useful when building different variants of pipelines, avoiding a lot of repetitive template code:

```python
from slyme.builder import builder
from slyme.node import sequential
from slyme.context import Ref

def base_scope():
    return {
        "config": Ref("default_config"),
    }

@builder
def base_pipeline():
    scope = base_scope()
    return sequential(nodes=[
        load_data(path="default_path"),
        process_data(scope),
    ])

@builder
def custom_pipeline(new_path: str):
    my_scope = {"output": Ref("output_path")}
    # 1. Get the base Node tree
    pipeline = base_pipeline()

    # 2. Dynamically modify specific Node's build-time parameters
    pipeline["nodes"][0]["path"] = new_path
    pipeline["nodes"].append(
        save_data(my_scope)
    )
    return pipeline
```

Through this approach, you can combine small Builder blocks into large systems like building with LEGO, while maintaining extreme flexibility.
