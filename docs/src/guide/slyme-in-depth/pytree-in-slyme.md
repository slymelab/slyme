# Understanding PyTree in Slyme

In [Functional Programming Basics](/guide/slyme-in-depth/functional-programming-basics), we mentioned Slyme's core design philosophy: separation of data and logic, immutability, and unlimited composition based on higher-order functions. To elegantly support these philosophies at the lower level, Slyme internally implements a powerful data structure processing tool — **PyTree**.

PyTree first gained prominence in machine learning frameworks like JAX, allowing us to treat arbitrarily nested Python data structures (such as lists, dictionaries, custom objects) as a "tree," separating the tree's "structure (TreeDef)" from its "leaf nodes (Leaves)".

Slyme built a lightweight, explicit, and instance-isolated PyTree engine from scratch. This article will take you deep into understanding its core mechanism and how it drives the entire Slyme Node system.

## Core Mechanism: Flatten and Unflatten

PyTree's core capability lies in "flattening" complex objects into a one-dimensional list of leaf nodes, and later "unflattening" them perfectly back using the same structure.

In Slyme's implementation, any type to be registered with the PyTree engine needs to provide two functions:
1. **`flatten`**: Accepts a container object, returns an iterator of child nodes (children), and a `PyTreeAux` object. `PyTreeAux` stores auxiliary metadata (metadata) and child node path keys (children_keys) needed to restore the structure.
2. **`unflatten`**: Accepts an iterator of child nodes and the previously generated `PyTreeAux`, and reconstructs the original container object.

For example, for data of type `list[dict[str, int]]` like `[{"a": 1}, {"b": 2, "c": 3}]`, PyTree deeply traverses this nested structure: first, for the outermost list, two children are obtained: `{"a": 1}` and `{"b": 2, "c": 3}`, while their Keys are recorded as `SequenceKey(0)` and `SequenceKey(1)` respectively, representing list index access indices. These Keys are stored in `PyTreeAux`; then, for each dictionary, further expansion yields children `[1]` and `[2, 3]`, with corresponding Keys `MappingKey("a")`, `MappingKey("b")`, and `MappingKey("c")`, representing dictionary access keys. Ultimately, the entire structure is flattened into a leaf element list `[1, 2, 3]` and definition information representing the original data structure (used for restoration). At this point, we can very simply perform batch operations on this list, such as computing squares, etc. Finally, the `unflatten` operation uses the converted leaf values to create new data identical to the original data structure, resulting in `[{"a": 1}, {"b": 4, "c": 9}]`. The entire process is recursive, and you can also register flattening/unflatten logic for certain custom classes, so PyTree can very flexibly and deeply parse various nested objects.

## Precise Path Tracking

Slyme's PyTree not only handles data, but also comes with a precise path tracking system. By introducing `PyTreeKey` (and its subclasses like `SequenceKey`, `MappingKey`, `AttributeKey`), Slyme can precisely record each leaf node's position in the original structure during tree traversal.

* **`SequenceKey(index)`**: Represents the index in a list or tuple.
* **`MappingKey(key)`**: Represents the key in a dictionary.
* **`AttributeKey(name)`**: Represents the attribute name of an object.

::: warning Deprecated
`CallKey` (a `PyTreeKey` subclass for function-call paths) and `KeyPathExpr` / `P` (the proxy object for building key paths) are **deprecated** since slyme 0.1.1 and will be removed in 0.2.0. They were only used by `Ref.key_path`, which is also deprecated. Use [`@expression`](/guide/essentials/node#at-expression) + [`Auto`](/guide/essentials/node#spec) for dynamic value resolution instead.
:::

## Instance-Isolated Engine (PyTreeEngine)

Unlike JAX which uses a global single registry, Slyme introduces the concept of `PyTreeEngine` class. This makes registration behavior instance-isolated.

You can create multiple different engines and register different `flatten` and `unflatten` logic for the same data type. This feature is the magic source of Slyme's compilation system.

## PyTree's Magic in the Node System: Dual-Engine Architecture

After understanding the basics, let's look at how PyTree supports the Slyme framework. When converting user-declared nodes (NodeDef) into executable nodes (NodeExec), Slyme cleverly utilizes two different `PyTreeEngine`s.

Recall that Slyme emphasizes immutability of execution-time structures: after calling `.prepare()`, mutable declaration objects are completely transformed into immutable execution objects. This is accomplished through the collaboration of the following two engines:

### Engine One: `NODE_ENGINE` (Type-Preserving)
This is the standard resolution engine. In this engine, after `NodeDef` is flattened and then unflattened, it is still `NodeDef`; `list` is still `list`. This engine is mainly used for the system's pre-run structural review, visualization rendering, and legality validation.

### Engine Two: `NODE_PREPARE_ENGINE` (Type Transformation and Compilation)
This is Slyme's "compiler" engine. When developers call `.prepare()` to compile the node tree, Slyme uses `NODE_PREPARE_ENGINE` to traverse the entire tree. This engine registers special unflatten logic:
1. **Mutable Container Immutabilization**: It converts mutable types in the original structure (`list`) back to immutable `tuple`, and mutable `dict` back to read-only `MappingProxyType`.
2. **Def to Exec Dimensionality Elevation**: Most importantly, it converts all declaration-period `NodeDef`, `ExpressionDef`, and `WrapperDef` back to their corresponding execution-period objects `NodeExec`, `ExpressionExec`, and `WrapperExec`.

Through this approach, a simple PyTree traversal `map` operation completes the compilation of the entire complex system from "declaration state" to "execution state," completely removing mutable state and ensuring concurrent execution safety.

## Other Applications of PyTree in Slyme

As introduced in previous documentation, PyTree plays an important role in Context value retrieval, Node auto-evaluation, and other aspects. It enables users to directly use native Python lists, dictionaries, tuples, and other data structures to represent data, and can be deeply parsed by Slyme automatically.

## Summary

In Slyme, PyTree is not just a toolkit for processing nested dictionaries and lists — it is the cornerstone of framework metaprogramming. By decoupling the decomposition and recombination of data structures, and cooperating with an instantiated multi-engine architecture, Slyme achieves highly flexible node composition and an immutable execution lifecycle in an extremely elegant and Pythonic way.
