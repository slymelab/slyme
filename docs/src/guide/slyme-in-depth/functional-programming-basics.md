# Functional Programming Basics

Slyme uses functional ideas selectively rather than requiring the entire runtime to be immutable.

- A Node's user function is explicit about its runtime `Context` and build parameters.
- A Node may return any value, including temporary control information for higher-order execution.
- Context data is mutable in place; mutation methods return `None`.
- Node and Wrapper calls pass their current static parameter containers directly.
- The live composition graph remains mutable, so later calls can observe structural changes.
- Structural isolation is explicit through `NodeElement.clone()` and `ContextElement.clone()`.

Purity remains an application choice. A value-producing Node can be pure; an I/O Node can perform side effects; a higher-order Node can coordinate children. Slyme models their composition and lifecycle without attempting to model the internal details of those Python operations.

For concurrency, call `.clone()` when branches require independent registered structure. Clones preserve unregistered leaf identities and therefore do not make arbitrary shared Python objects thread-safe; copy those application values separately when needed.
