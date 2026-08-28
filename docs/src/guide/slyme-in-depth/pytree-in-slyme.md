# PyTree in Slyme

A PyTree is a nested structure whose containers define topology and whose unregistered objects are leaves. Slyme uses separate engines for separate semantics rather than assuming that every traversal should follow the same objects.

## `NODE_ENGINE`

`NODE_ENGINE` registers `Node`, `Wrapper`, their asynchronous variants, and ordinary containers. It is used for physical graph inspection, rendering, validation, and Ref discovery. Because it can traverse Node relationships, callers performing whole-graph analysis must define their own cycle policy when logical Slot graphs are introduced.

## `NODE_SNAPSHOT_ENGINE`

Every Node and Wrapper call uses a small snapshot engine that registers only ordinary Python containers:

- `list` becomes `tuple`;
- `tuple` remains `tuple`;
- `dict` becomes `MappingProxyType`;
- an existing `MappingProxyType` remains read-only.

Node, Wrapper, and future Slot objects are not registered, so they are leaves. The engine therefore freezes parameter data recursively without walking composition edges or getting trapped by a Node graph cycle.

The resulting snapshot exists only for one call. The live Node graph remains mutable and later calls create new snapshots.

## Auto evaluation

Auto evaluation uses the Context evaluation engine after the local snapshot has been created. Registered leaves such as `Ref` and `Node` are resolved with the current `Context`, while ordinary leaves pass through unchanged. Evaluation results retrieved from Context are not frozen again.
