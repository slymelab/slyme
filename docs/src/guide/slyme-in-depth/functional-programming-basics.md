# Functional Programming Basics

Slyme uses functional ideas selectively rather than requiring the entire runtime to be immutable.

- A Node's user function is explicit about its runtime `Context` and build parameters.
- A Node may return any value, including temporary control information for higher-order execution.
- Context data is mutable in place; mutation methods return `None`.
- Node and Wrapper calls pass their current static parameter containers directly.
- The live composition graph remains mutable, so later calls can observe structural changes.
- Structural isolation is explicit: call a Node factory or Builder again for another graph, and use `Context.fork()` for a live local data layer.

Purity remains an application choice. A value-producing Node can be pure; an I/O Node can perform side effects; a higher-order Node can coordinate children. Slyme models their composition and lifecycle without attempting to model the internal details of those Python operations.

Every child Node evaluated through `Auto` receives its own Context fork. Explicit concurrent orchestration should likewise create one fork per branch when local writes must be isolated. Forks preserve application leaf identities, so they do not make shared Python objects thread-safe or undo external side effects; isolate those resources separately when needed.
