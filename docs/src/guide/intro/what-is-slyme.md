# What is Slyme?

Slyme (pronounced /slaɪm/) is a highly composable functional execution framework. As the recursive naming suggests (**S**LYME **L**ets **Y**ou **M**old **E**verything), it enables developers to seamlessly build arbitrarily complex execution flows based on simple, reusable functions, without needing to master cumbersome APIs or syntax.

::: tip
Just want to try Slyme? Jump to the [Quick Start](/guide/intro/quick-start).
:::

## Core Advantages

- **Native Python Development Experience**: Slyme has no heavy object-oriented boilerplate code and no steep learning curve. You only need to master some basic Python concepts, including Python functions and native data structures (such as dictionaries, lists, tuples, etc.), and understand a few core concepts to get started quickly.
- **Unlimited Composability**: The basic execution unit in Slyme is [Node](/guide/essentials/node), which is responsible for executing user-defined functions. Node supports **unlimited composition** — a Node can contain other Nodes, or be contained by other Nodes. Interestingly, thanks to PyTree augmentation, this containment relationship can be directly represented through native Python data structures, such as lists or dictionaries. Unlimited composition enables Slyme to build arbitrarily complex execution flows, with complete decoupling between Nodes.
- **Functional/Concurrency Safety**: Slyme is designed as a functional execution framework, where the "state" (i.e., [Context](/guide/essentials/context)) exchanged between Nodes is **structurally immutable**, making state management under concurrent execution simpler and safer.
- **Seamless Collaboration**: Slyme's Node design is highly decoupled. These Nodes communicate through Context, which allows community/development teams to independently develop their own features and perform unit testing, reducing code conflicts during development and allowing developers to focus on logic implementation rather than being bound by deep system coupling and tedious "glue code".

## Core Concepts

To understand how Slyme works, you need to understand the following core concepts:

### Context

[Context](/guide/essentials/context) is Slyme's core data structure. It behaves like a dictionary (`dict`) in Python, with two key differences: it is **hierarchical** and **structurally immutable**. [Ref](/guide/essentials/context#ref) is a class used to access/modify Context, similar to a dictionary key. You can access paths like `a.b` through Ref, or modify values like `c.d`. Each modification to Context returns a new Context object. Internally, Context uses **Copy-On-Write** mechanism to reuse structures and improve execution efficiency.

### Node

As mentioned earlier, [Node](/guide/essentials/node) is Slyme's basic execution unit, responsible for executing user-defined functions. Depending on the Node type, it can be categorized as follows:

- [**@node**](/guide/essentials/node#at-node): Slyme's basic execution unit, responsible for accepting a Context object, executing a user function, and returning a new Context object (or the original Context if no modifications were made). During this process, the user function retrieves values from Context, executes custom logic, writes the execution result to Context and returns it, similar to traditional functions that accept input parameters, execute logic, and return results.
- [**@expression**](/guide/essentials/node#at-expression): Similar to @node, @expression's user function retrieves values from Context, processes them, and then **directly returns the value itself** (rather than a Context object). This behavior is similar to Python's @property (or Vue's computed properties).
- [**@wrapper**](/guide/essentials/node#at-wrapper): @wrapper functions like middleware, responsible for intercepting, modifying, or enhancing the Node's execution process, such as adding logs before/after @node execution, recording performance, handling exceptions, etc.

::: info
@node / @expression / @wrapper support both regular functions and `async def` functions. The legacy @async_node / @async_expression / @async_wrapper decorators are deprecated and will be removed in Slyme 0.2.0.
:::

### Builder

[@builder](/guide/essentials/builder) is used to assemble Node structures. Users can build arbitrary Node flows within custom functions, then hand them over to @builder for automatic structure validation. A @builder function can call other @builder functions for more flexible organization of the building process.

### Build-time / Execution-time Separation

In Slyme, build-time and execution-time are two distinct concepts, corresponding to different points in time (see [Lifecycle](/guide/essentials/lifecycle)).

- **Build-time**: During build-time, users assemble Node structures and can make arbitrary modifications to them.
- **Execution-time**: During execution-time, the built Node structure is converted into a fixed execution structure for safe and efficient execution. The high-level `.run()` method performs this automatically; `.prepare()` exposes the immutable Exec for lower-level reuse.
