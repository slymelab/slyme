# What is Slyme?

Slyme (pronounced /slaɪm/) is a highly composable functional execution framework. As the recursive naming suggests (**S**LYME **L**ets **Y**ou **M**old **E**verything), it enables developers to seamlessly build arbitrarily complex execution flows based on simple, reusable functions, without needing to master cumbersome APIs or syntax.

::: tip
Just want to try Slyme? Jump to the [Quick Start](/guide/intro/quick-start).
:::

## Core Advantages

- **Native Python Development Experience**: Slyme has no heavy object-oriented boilerplate code and no steep learning curve. You only need to master some basic Python concepts, including Python functions and native data structures (such as dictionaries, lists, tuples, etc.), and understand a few core concepts to get started quickly.
- **Unlimited Composability**: The basic execution unit in Slyme is [Node](/guide/essentials/node), which is responsible for executing user-defined functions. Node supports **unlimited composition** — a Node can contain other Nodes, or be contained by other Nodes. Interestingly, thanks to PyTree augmentation, this containment relationship can be directly represented through native Python data structures, such as lists or dictionaries. Unlimited composition enables Slyme to build arbitrarily complex execution flows, with complete decoupling between Nodes.
- **Explicit State Boundaries**: Context data is mutable for efficient long-lived workflows. Callers explicitly copy it when concurrent branches require isolation, while each Node invocation receives a stable parameter snapshot.
- **Seamless Collaboration**: Slyme's Node design is highly decoupled. These Nodes communicate through Context, which allows community/development teams to independently develop their own features and perform unit testing, reducing code conflicts during development and allowing developers to focus on logic implementation rather than being bound by deep system coupling and tedious "glue code".

## Core Concepts

To understand how Slyme works, you need to understand the following core concepts:

### Context

[Context](/guide/essentials/context) is Slyme's hierarchical runtime data store. Its outer attributes are stable while its data is mutable in place. [Ref](/guide/essentials/context#ref) identifies paths such as `a.b`; explicitly copy Context when concurrent branches require isolated data.

### Node

As mentioned earlier, [Node](/guide/essentials/node) is Slyme's basic execution unit:

- [**@node**](/guide/essentials/node): Accepts a Context at runtime and may mutate Context, perform side effects, coordinate child Nodes, or return a derived value.
- [**@wrapper**](/guide/essentials/node#wrappers): Middleware that intercepts or enhances Node execution, for example tracing, timing, retry, or exception handling.

::: info
@node and @wrapper support both regular functions and `async def` functions.
:::

### Builder

[@builder](/guide/essentials/builder) is used to assemble Node structures. Users can build arbitrary Node flows within custom functions, then hand them over to @builder for automatic structure validation. A @builder function can call other @builder functions for more flexible organization of the building process.

### Live Graph and Call-local Snapshots

Slyme keeps one mutable Node graph across assembly and execution (see [Lifecycle](/guide/essentials/lifecycle)).

- **Between calls**: Users may assemble or modify Node parameters, wrappers, and composition structures.
- **During a call**: The current Node freezes its ordinary Python containers into a local immutable snapshot, resolves dynamic values with the supplied Context, and executes from that snapshot. Later graph changes affect later calls.
