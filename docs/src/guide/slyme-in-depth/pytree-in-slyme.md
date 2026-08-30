# PyTree in Slyme

A PyTree is a nested structure whose containers define topology and whose unregistered objects are leaves. Slyme uses separate engines for separate semantics rather than assuming that every traversal should follow the same objects.

## `NODE_ENGINE`

`NODE_ENGINE` registers `Node`, `Wrapper`, their asynchronous variants, and ordinary containers. It is used for physical graph inspection, rendering, validation, and Ref discovery. Because it can traverse Node relationships, callers performing whole-graph analysis must define their own cycle policy when logical Slot graphs are introduced.

## Structural cloning

`NodeElement.clone()` maps the identity function over `NODE_ENGINE`. Unflattening reconstructs every registered Node, Wrapper, and ordinary parameter container while preserving unregistered leaf objects. The result is an independent physical Node/PyTree structure without an arbitrary deep copy of application values.

`ContextElement.clone()` uses `CONTEXT_ENGINE`, whose only containers are `Context` and `ContextData`. It therefore reconstructs the ContextData hierarchy but preserves stored lists, dictionaries, model objects, and all other leaf identities. Cloning a `ContextView` produces a standalone Context rooted at that subtree.

Both operations follow registered tree edges and expect an acyclic PyTree. They do not preserve alias identity when the same registered container appears at multiple paths.

## Auto evaluation

Auto evaluation uses the Context evaluation engine to resolve registered leaves such as `Ref` and `Node` with the current `Context`, while ordinary leaves pass through unchanged. Static parameter containers are passed directly; a dynamic Auto tree is reconstructed with its evaluated leaves.
