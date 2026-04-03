# What is Slyme?

Slyme (pronounced /slaɪm/) is a highly composable functional execution framework. True to its acronym—**S**LYME **L**ets **Y**ou **M**old **E**verything—it empowers developers to seamlessly build complex, concurrency-safe execution pipelines out of simple, reusable functional blocks.

::: tip
Just want to try it out? Skip to the [Quickstart](/guide/intro/get-started).
:::

## Highlights

- **Native Python Experience**: Developing with Slyme feels entirely natural. You only need to understand basic Python functions and native data structures (like dictionaries, lists, and tuples). There is no heavy object-oriented boilerplate or steep learning curve required.
- **Infinite Composability (Fractal Structure)**: Slyme's architecture is inherently fractal. A [Node](/guide/core-concepts/node) can seamlessly encapsulate sub-nodes, while simultaneously being embedded as a sub-node within another larger Node. This allows you to build infinitely complex systems from simple, reusable blocks.
- **Functional & Concurrency-Safe**: State mutations are handled purely functionally. Because the runtime relies on an immutable context with Copy-On-Write (COW) mechanics, node executions are thread-safe and inherently designed for safe asynchronous concurrency.
- **Seamless Collaboration & Easy Testing**: Slyme's decoupled design makes it perfect for teamwork and open-source contributions. Developers can build independent Nodes. Since each Node is essentially a function interacting with a [Context](/guide/core-concepts/context), unit testing is completely straightforward.
- **Flexible PyTree Architecture**: Under the hood, Slyme is powered by a robust [PyTree](/guide/advanced-usage/pytree-in-slyme) engine. It can dynamically parse, traverse, and rebuild almost any nested combination of native Python data types and Slyme node structures.

## Core Concepts

To understand how Slyme molds everything together, you only need to grasp a few core concepts (which we will explore in detail in the following sections):

### 1. Context
The [Context](/guide/core-concepts/context) is the lifeblood of Slyme. It is an immutable, Copy-On-Write data container that flows through your execution graph. Instead of mutating state globally, functions extract what they need from the Context and return an updated Context safely.

### 2. The Node Series
Slyme's execution units are divided into specific families to handle different tasks:
- [**Node**](/guide/core-concepts/node): The primary unit that takes a Context and returns a new Context.
- [**Expression**](/guide/core-concepts/node): Computes and returns specific values from the Context.
- [**Wrapper**](/guide/core-concepts/node): Acts as middleware to intercept, modify, or augment the execution of other Nodes.

*(Note: All of these have `Async` equivalents for asynchronous execution).*

### 3. Builder
The [Builder](/guide/core-concepts/builder) is a structural pattern (often used via the `@builder` decorator) that helps you assemble and compose complex PyTree node structures out of simpler ones during the initialization phase.

### 4. Build-time / Run-time Separation
Slyme enforces a strict boundary between two distinct phases:
- **Build-time (Definition)**: Where you construct, mutate, and wire together your Node definitions (e.g., `NodeDef`).
- **Run-time (Execution)**: Where definitions are frozen into highly optimized, immutable execution structures (e.g., `NodeExec`) that process your Contexts safely and efficiently.
