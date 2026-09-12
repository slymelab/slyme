# PyTree in Slyme

A PyTree is a nested structure whose containers define topology and whose unregistered objects are leaves. Slyme uses separate engines for separate semantics rather than assuming that every traversal should follow the same objects.

## `NODE_ENGINE`

`NODE_ENGINE` registers `Node`, `Wrapper`, their asynchronous variants, and ordinary containers. It is used for physical graph inspection and Ref discovery. Node parameters may contain heterogeneous nested values; the engine describes traversal, not which combinations are legal.

## Object identity

Slyme does not expose a generic Node graph clone. PyTree reconstruction cannot decide which shared references should remain aliases, which values should be copied, or how cyclic application graphs should behave. Call the relevant Node factory or assembly function again and copy application values explicitly when another graph is required.

Context is not registered as a PyTree container. It is an identity-bearing lifetime owner with at most one parent, not a self-contained value tree. Its bound Scope carries the independent C3 visibility graph. `Context.flatten()` provides the visible Ref-to-value mapping when explicit materialization is needed.

## Auto evaluation

Auto evaluation uses `CTX_EVAL_ENGINE` to find registered leaves while treating Context itself as opaque. Ref values read the current Context. Each child Node evaluates in an owned child Context with a distinct child Scope; Slyme disposes that Context before reconstructing the Auto tree from returned values. All traversed containers are reconstructed according to their PyTree handlers, including subtrees with no evaluatable leaves. Ordinary leaves and evaluator results pass through unchanged.
