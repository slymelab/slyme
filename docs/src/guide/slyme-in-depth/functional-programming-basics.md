# Functional Programming Basics

Slyme is a highly composable functional execution framework. In traditional Object-Oriented Programming (OOP), developers are accustomed to encapsulating state and methods that modify state within classes; in Slyme, we adopt the philosophy of Functional Programming (FP).

While you don't need to be an expert in functional programming to use Slyme smoothly, understanding these underlying design concepts will help you write more elegant, robust, and testable code.

## Core Concepts and Their Mapping to Slyme

Functional programming has several key pillars, and Slyme's entire architecture is built around these pillars.

### 1. Data Immutability

In functional programming, once data is created, it should not be modified. Any "modification" of data should actually return a brand new data copy containing the new state.

**Manifest in Slyme:**
* **Structurally Immutable Context**: In Slyme, Context is the core data structure. It is hierarchical and structurally immutable. Each modification to Context (such as setting a value) returns a new Context object.
* **Copy-On-Write Mechanism**: To avoid performance overhead from frequently creating objects, Context internally uses Copy-On-Write mechanism to reuse unmodified structures, greatly improving execution efficiency.
* **Immutability of Execution-time Structures**: Slyme strictly separates Node's declaration period (Def) and execution period (Exec). After calling `.prepare()`, the mutable declaration object is completely transformed into an immutable execution object (Exec), ensuring runtime structural safety.

### 2. Balancing Function Purity and Practicality

In strict functional concepts, a pure function (Pure Function) means: given the same input, it always returns the same output, and does not produce any observable side effects (Side Effects, such as reading/writing databases, making network requests, etc.) during computation.

**Manifest in Slyme:**
* **Practical Balance**: It should be made clear that Slyme **does not** follow strict side-effect control like pure functional programming languages. We have struck a balance between framework **usability** and **purity**. Real-world business systems cannot do without I/O, so you can completely implement various database operations or network requests in custom Nodes (which is also an important reason why @async_node was created).
* **Purity is Defined by You**: The Slyme framework itself provides a perfectly pure-function-logic-carrying architecture system. This means: **if your custom functions are pure functions, then the entire Slyme Node system constructed from them is also strictly pure**. We have handed over side-effect control to developers.
* **Explicit State Tracking**: Even if you introduce side effects, Slyme's standard @node signature still requires the function to accept a Context object and return a new Context object. This makes the impact on system state flow explicit and traceable, even with I/O.

### 3. Separation of Data and Logic

Unlike Object-Oriented Programming which bundles data and methods together, functional programming advocates keeping data structures as simple and transparent as possible, and making processing logic independent functions focused on computation.

**Manifest in Slyme:**
* **Extreme Decoupling**: In Slyme, Context is only responsible for storing and passing state, while Node is only responsible for pure business logic computation.
* **Auto Evaluation and Dependency Injection**: By setting `auto_eval=True` (or using the built-in `Auto` type hint annotation), Slyme automatically resolves actual values from Context and injects them into parameters before Node execution. This design completely decouples "where to get data" from "how to process data".

### 4. Higher-Order Functions and Unlimited Composition

Functions can not only process data, but can also receive other functions as parameters, or return functions. Using this higher-order feature, we can combine simple small functions like LEGO bricks into complex systems.

**Manifest in Slyme:**
* **Unlimited Composition Based on PyTree**: Node supports unlimited composition — a Node can contain other Nodes, or be contained by other Nodes, and this relationship can be directly represented through native Python data structures (such as lists or dictionaries).
* **@wrapper Onion Model**: @wrapper acts as middleware, accepting a target node as a parameter and returning an enhanced node. This is essentially an elegant implementation of higher-order functions. @wrapper follows the classic "onion model," allowing you to intercept and enhance Node execution.
* **@builder Assembler**: @builder is used to assemble Node structures. Users can build arbitrary Node flows within custom functions. One @builder can call other @builders, greatly improving the reuse rate of logic modules.

## Engineering Benefits of Functional Design

Slyme chose the functional path not for academic purity, but because this paradigm can practically solve pain points in modern complex software engineering:

1. **Natural Concurrency Safety**: In complex I/O-intensive or high-concurrency scenarios, shared mutable state is often the beginning of nightmares (you need to introduce various Lock mechanisms). In Slyme, because Context is structurally immutable, multi-threaded or async coroutine (@async_node) concurrent reading of data or deriving new state can be done without burden, making state management under concurrent execution extremely simple and safe.
2. **Excellent Unit Testing Experience**: Testing a complex object-oriented instance often requires carefully Mocking various internal states and dependency classes. In Slyme, testing a pure Node is very simple: you only need to construct an input Context, execute the Node, and then assert whether the values in the returned Context meet expectations. No implicit state, extremely clean test logic.
3. **Eliminate Implicit Dependencies, Reduce Conflicts**: Slyme's Node design is highly decoupled. These Nodes strictly communicate through Context. This allows different development teams to independently develop their own features and perform unit testing, greatly reducing code conflicts during multi-person collaboration.
