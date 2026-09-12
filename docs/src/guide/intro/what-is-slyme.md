# What is Slyme?

Slyme (pronounced /slaɪm/) is a highly composable functional execution framework. As the recursive naming suggests (**S**LYME **L**ets **Y**ou **M**old **E**verything), it enables developers to seamlessly build arbitrarily complex execution flows based on simple, reusable functions, without needing to master cumbersome APIs or syntax.

::: tip
Just want to try Slyme? Jump to the [Quick Start](/guide/intro/quick-start).
:::

## Core Advantages

- **Native Python Development Experience**: Slyme has no heavy object-oriented boilerplate code and no steep learning curve. You only need to master some basic Python concepts, including Python functions and native data structures (such as dictionaries, lists, tuples, etc.), and understand a few core concepts to get started quickly.
- **Unlimited Composability**: The basic execution unit in Slyme is [Node](/guide/essentials/node), which is responsible for executing user-defined functions. Node supports **unlimited composition** — a Node can contain other Nodes, or be contained by other Nodes. Interestingly, thanks to Tree augmentation, this containment relationship can be directly represented through native Python data structures, such as lists or dictionaries. Unlimited composition enables Slyme to build arbitrarily complex execution flows, with complete decoupling between Nodes.
- **Explicit Lifetime and Visibility**: Context provides single-parent lifetime ownership, while immutable Scope objects provide independent C3 visibility. `flatten()` exposes the visible Ref-to-value mapping, and `Compose` manages ordered, reversible values by Scope.
- **Seamless Collaboration**: Slyme's Node design is highly decoupled. These Nodes communicate through Context, which allows community/development teams to independently develop their own features and perform unit testing, reducing code conflicts during development and allowing developers to focus on logic implementation rather than being bound by deep system coupling and tedious "glue code".

## Core Concepts

To understand how Slyme works, you need to understand the following core concepts:

### Context

[Context](/guide/essentials/context) owns runtime data, child lifetimes, and reversible effects. [Ref](/guide/essentials/context#ref) identifies paths such as `a.b`; each Context binds a [Scope](/guide/essentials/context#scope) that determines C3 data visibility. [Compose](/guide/essentials/context#compose) combines values registered for visible Scopes.

### Node

As mentioned earlier, [Node](/guide/essentials/node) is Slyme's basic execution unit:

- [**@node**](/guide/essentials/node): Accepts a Context at runtime and may mutate Context, perform side effects, coordinate child Nodes, or return a derived value.
- [**@wrapper**](/guide/essentials/node#wrappers): Middleware that intercepts or enhances Node execution, for example tracing, timing, retry, or exception handling.

::: info
@node and @wrapper support both regular functions and `async def` functions.
:::

### Graph Assembly

[Ordinary Python functions](/guide/essentials/graph-assembly) assemble reusable Node graphs. They can call other assembly functions and return individual Nodes or structured collections. Node and Wrapper parameters may use arbitrary nested structures.

### Live Graph and Explicit Layers

Slyme keeps one mutable Node graph across assembly and execution (see [Lifecycle](/guide/essentials/lifecycle)).

- **Between calls**: Users may assemble or modify Node parameters, wrappers, and composition structures.
- **During a call**: The current Node passes static parameter containers directly, resolves dynamic values with the supplied Context, and executes. In-call mutations to static containers remain on the live Node.
- **When isolation is needed**: Call the Node factory or assembly function again for an independently configurable graph. Context `fork()` creates an owned lifetime child and shares its Scope by default; pass a child Scope for a separate data layer. `Context(context.flatten(), schema=context.schema)` explicitly materializes visible bindings into a new application root. Stored application objects remain shared.
