# Lifecycle

Slyme's Node system adopts a strict **two-stage architecture** (Build-time Def and Execution-time Exec). This design fundamentally decouples the "assembly" from the "running" of logic, ensuring high flexibility while providing a solid guarantee for execution-time safety and performance optimization.

A Node typically goes through four complete lifecycle stages from creation to final execution: **Build** -> **Modify** -> **Prepare** -> **Execute**.

## 1. Build Phase

When you call a `@node`, `@expression`, or `@wrapper` decorated function and pass configuration parameters, you are not executing this business logic — you are instantiating a **mutable definition object (Def)**.

```python
from slyme.node import node, Auto
from slyme.context import Context, R

@node
def process_data(ctx: Context, /, *, timeout: int = 30, data: Auto[list], max_len: int) -> Context:
    return ctx

# Build phase: instantiate Def object
my_node_def = process_data(max_len=1024)  # At this point, data is UNDEFINED
```

During this phase, Slyme performs initial parameter collection and processes marker constants like `UNSET` (explicitly trigger default value logic). If some required parameters are not provided, they can be assigned during the modification phase.

To better manage building logic, we generally recommend using [`@builder`](/guide/essentials/builder) to assemble complex Node trees.

## 2. Dynamic Modification Phase

Before the Def object's `.prepare()` is called, it is **completely mutable**. Slyme allows you to use the `[]` operator to dynamically access and deeply modify its build-time parameters.

This gives the system great flexibility. For example, you can fine-tune an existing Builder product without rewriting a lot of repetitive code:

```python
# Dynamic modification phase: fine-tune Def parameters
my_node_def["timeout"] = 60
my_node_def["data"] = [R.user.age, R.user.name]
```

## 3. Prepare Phase

When the Node tree structure and parameters are fully modified, you need to call the `.prepare()` method. This step marks the Node's formal transition from "build-time" to "execution-time", returning an **immutable execution object (Exec)**.

```python
# Prepare phase: convert Def to immutable Exec
my_node_exec = my_node_def.prepare()
```

During this phase, Slyme's internal framework performs some checks and optimizations:
1. **Structure and Parameter Validation**: Check that all `UNDEFINED` have been correctly assigned values.
2. **Parameter Freezing**: This is a key operation for execution-time safety and performance optimization.
3. **Performance Optimization**:
    - For @node, Slyme combines its mounted @wrappers with itself into a chained function (onion model) and caches it.
    - Slyme deeply analyzes build-time parameters, pre-analyzing and caching the parts that need auto-evaluation to improve runtime performance (see [Dependency Injection](/guide/slyme-in-depth/dependency-injection) for the in-depth principle of auto-evaluation).
    - Since the product (Exec object) after `.prepare()` method call is immutable, Slyme will consider adding more aggressive optimizations (like JIT) in future versions to further improve execution efficiency.

::: tip Parameter Freezing Mechanism
When `.prepare()` is called, Slyme's internal Prepare Engine performs deep traversal and structural optimization (freezing) on the Node's received parameters. **Mutable structures (like `list` or `dict`) passed by users during the build phase are converted to immutable structures (like `tuple` or `MappingProxyType`, etc.) after prepare.**

This means once prepare is complete, the Node's parameter structure is completely locked and cannot be accidentally modified, ensuring efficient and concurrency-safe execution.
:::

## 4. Execute Phase

Finally, pass `Context` to the Exec object to trigger the actual business logic execution:

```python
# Execute phase: pass Context to execute business logic
new_ctx = my_node_exec(Context())
```

During this phase, one of Slyme's most powerful features — **Auto auto-evaluation and injection** — takes effect. The framework deeply traverses the frozen parameter structure, resolves Refs and @expressions in it to actual values in Context, and injects them into the target function.

## Common Confusion Points with Auto Injection

Due to the **parameter freezing mechanism during the prepare phase** combined with **Auto auto-injection**, there is a confusing scenario that requires special attention: **differences in injected mutable structures**.

Please look at the comparison of the following two scenarios:

```python
from typing import Any
from slyme.node import node, Auto
from slyme.context import Context, R

@node
def process(ctx: Context, /, *, data: Auto[Any]) -> Context:
    print(f"Type: {type(data)}, Value: {data}")
    return ctx
```

**Scenario A: Concatenated a `list` containing `Ref` at build-time**

```python
# 1. Build: pass a Python list with multiple Refs inside
node_def_a = process(data=[R.a, R.b])

# 2. Prepare: due to parameter freezing mechanism, the internal list structure is converted to tuple!
exec_a = node_def_a.prepare()
# At this point, exec_a's internal data parameter structure is actually: (R.a, R.b)

# 3. Execute: Auto auto-evaluation engine resolves this tuple and injects the final value
ctx_a = Context().update({R.a: 1, R.b: 2})
exec_a(ctx_a)
# Print output => Type: <class 'tuple'>, Value: (1, 2)
```

**Scenario B: Directly passed a `Ref` pointing to a `list` at build-time**

```python
# 1. Build: pass a single Ref
node_def_b = process(data=R.my_list)

# 2. Prepare: Ref itself is an immutable leaf node, remains unchanged
exec_b = node_def_b.prepare()
# At this point, exec_b's internal data parameter structure is still: R.my_list

# 3. Execute: Auto auto-evaluation engine resolves this Ref, directly retrieves the value from Context and injects it
ctx_b = Context().set(R.my_list, [1, 2])
exec_b(ctx_b)
# Print output => Type: <class 'list'>, Value: [1, 2]
```

**Summary:**
- If you concatenated a list at **build-time** (like `[Ref(...), Ref(...)]`), after prepare freezing, you get a `tuple` at execution-time.
- If you only passed a `Ref`, and that `Ref` points to an original `list` in Context, you get the original `list` at execution-time.
- The difference in behavior above corresponds to Slyme's core philosophy: Node structure and its configuration parameters are immutable at runtime, while the user data stored in Context is not restricted.
