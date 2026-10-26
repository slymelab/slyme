# Graph Assembly

Use ordinary Python functions to assemble and reuse Node graphs. A function can return a `Node` or a collection of graph elements; no assembly decorator is required.

## Basic Usage

Given application Nodes `load_data`, `process_data`, and `save_data`, a function can assemble a sequential pipeline:

```python
from slyme.node import AnyNode, sequential
from slyme.context import Schema
# Assume nodes are already defined
# from my_nodes import load_data, process_data, save_data

R = Schema({"process_config": Schema.leaf(), "output_path": Schema.leaf()})


def create_data_pipeline(source_path: str) -> AnyNode[None]:
    # 1. Instantiate each Node — pass paths from R via keyword arguments
    load_node = load_data.partial(path=source_path)
    process_node = process_data.partial(config=R.resolve("process_config"))
    save_node = save_data.partial(output=R.resolve("output_path"))

    # 2. Assemble and return a complete Node tree
    return sequential.partial(nodes=[load_node, process_node, save_node])
```

This function creates Nodes without executing them. Invoke the resulting pipeline with a Context when ready:

```python
pipeline = create_data_pipeline("/path/to/data")
# pipeline(ctx)
```

Use return type annotations and a static type checker to verify assembly functions.

## Composition and Dynamic Modification

Assembly functions can call each other. Use partial to derive a customized pipeline from a base graph:

```python
from slyme.node import AnyNode, sequential
from slyme.context import Schema

R = Schema({"default_config": Schema.leaf(), "output_path": Schema.leaf()})


def base_pipeline() -> AnyNode[None]:
    return sequential.partial(
        nodes=[
            load_data.partial(path="default_path"),
            process_data.partial(config=R.resolve("default_config")),
        ]
    )


def custom_pipeline(new_path: str) -> AnyNode[None]:
    # 1. Get the base Node tree
    pipeline = base_pipeline()

    # 2. Derive bindings and replace the sequence
    first, *rest = pipeline.get("nodes")
    return pipeline.partial(nodes=[
        first.partial(path=new_path),
        *rest,
        save_data.partial(output=R.resolve("output_path")),
    ])
```

Each call to `base_pipeline()` creates fresh Nodes and a fresh list; modifications in one pipeline do not change another. Application values passed into multiple graphs remain shared unless explicitly copied.

Assembly functions and parameter structures may nest freely. Node and Wrapper calls return immediate values or awaitables; `sequential` preserves step order for both.
