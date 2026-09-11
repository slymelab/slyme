# Graph Assembly

Use ordinary Python functions to assemble and reuse Node graphs. A function can return a `Node`, an `AsyncNode`, or a collection of graph elements; no assembly decorator is required.

## Basic Usage

Given application Nodes `load_data`, `process_data`, and `save_data`, a function can assemble a sequential pipeline:

```python
from slyme.node import Node, sequential
from slyme.context import Schema
# Assume nodes are already defined
# from my_nodes import load_data, process_data, save_data

R = Schema({"process_config": Schema.leaf(), "output_path": Schema.leaf()})


def create_data_pipeline(source_path: str) -> Node[None]:
    # 1. Instantiate each Node — pass paths from R via keyword arguments
    load_node = load_data(path=source_path)
    process_node = process_data(config=R.resolve("process_config"))
    save_node = save_data(output=R.resolve("output_path"))

    # 2. Assemble and return a complete Node tree
    return sequential(nodes=[load_node, process_node, save_node])
```

This function creates Nodes without executing them. Invoke the resulting pipeline with a Context when ready:

```python
pipeline = create_data_pipeline("/path/to/data")
# pipeline(ctx)
```

Use return type annotations and a static type checker to verify assembly functions.

## Composition and Dynamic Modification

Assembly functions can call each other. Their returned Node graphs remain mutable, so a function can create a base pipeline and customize it:

```python
from slyme.node import Node, sequential
from slyme.context import Schema

R = Schema({"default_config": Schema.leaf(), "output_path": Schema.leaf()})


def base_pipeline() -> Node[None]:
    return sequential(
        nodes=[
            load_data(path="default_path"),
            process_data(config=R.resolve("default_config")),
        ]
    )


def custom_pipeline(new_path: str) -> Node[None]:
    # 1. Get the base Node tree
    pipeline = base_pipeline()

    # 2. Dynamically modify specific Node's build-time parameters
    pipeline.get("nodes")[0].set("path", new_path)
    pipeline.get("nodes").append(save_data(output=R.resolve("output_path")))
    return pipeline
```

Each call to `base_pipeline()` creates fresh Nodes and a fresh list; modifications in one pipeline do not change another. Application values passed into multiple graphs remain shared unless explicitly copied.

Assembly functions and parameter structures may nest freely. Execution APIs retain their declared types: wrapper modes must match their Node, `sequential` accepts synchronous Nodes, and `async_sequential` accepts synchronous or asynchronous Nodes.
