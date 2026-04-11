# Dependency Injection

In complex data flow and workflow orchestration, how to elegantly decouple "business logic" from "configuration data/runtime state" is a core challenge. Slyme adopts a functional-style design, providing a powerful **dependency injection** mechanism that allows Nodes to remain pure and only focus on business computation.

Slyme currently implements dependency injection at two levels:
1. **Build-time Injection**: Parameter injection when using Scope to initialize Nodes.
2. **Runtime Injection**: Auto evaluation (Auto Eval) when Nodes execute.

Below, we will deeply explore the specific implementation details of these two mechanisms.

## 1. Node Initialization Based on Scope

When building workflows, we typically define various configuration items at different levels (such as global configuration, module configuration, component configuration). Slyme allows us, when creating Nodes, to pass multiple dictionaries (called Scope), and the underlying layer automatically injects needed parameters into Nodes through function signature analysis.

### 1.1 Signature Analysis

During decorator application, Slyme uses `inspect.signature` and `typing.get_type_hints` to analyze function signatures and type hints. During this process, Slyme collects and merges `Spec` objects (containing default values, whether auto-evaluation is needed, etc.) for each build-time parameter, and records parameter names for later use.

### 1.2 Scope Resolution

When creating a Node (@node / @expression / @wrapper), it supports passing multiple positional arguments `*scopes` and keyword arguments `**kwargs`. The specific injection logic is completed by the `resolve_arguments` function:

- **ChainMap Priority Chain**: Slyme internally uses the efficient standard library data structure `collections.ChainMap` to combine all Scopes. The combination order is `ChainMap(overrides, *reversed(scopes))`.
- **Lookup Rules**: This means when looking up a parameter, it follows the priority order of **kwargs (explicit override) > last scope > ... > first scope**. If the same key exists in multiple scopes, the later passed scope overrides the previous ones.
- **Parameter Cleaning**: After obtaining raw data through resolution, the `process_kwargs` function performs strict validation based on the `specs` generated in the previous step. It throws exceptions for unknown parameters (preventing typos), and fills in default values or calls `default_factory` for missing parameters according to the `Spec` definition.

## 2. Node Auto Evaluation (Auto Eval)

Static configuration is often insufficient. In many scenarios, parameters needed by Nodes are dynamic, such as depending on the previous step's computation result in Context, or being a lazily executed expression. Slyme provides auto evaluation (Auto Eval) mechanism, allowing Nodes to transparently obtain dynamic dependencies at runtime.

### 2.1 Declarative Evaluation Annotation

Developers don't need to manually call `ctx.get()` inside Nodes. They only need to use `Auto` (which is `Annotated[T, Spec(auto_eval=True)]` underneath) in function signatures to mark that the parameter supports auto evaluation.

### 2.2 Compile-time Preparation

To ensure runtime efficiency, Slyme doesn't scan all parameters every time a Node executes. Related work is moved forward to the `prepare` phase when Nodes transition from definition state (`*Def`) to execution state (`*Exec`):

1. **Separate Parameters**: In `*Exec`'s `__init__` method, `_prepare_eval` is called. It checks whether values contain objects needing evaluation based on the parameter's `Spec`. Parameters not needing evaluation are put into `raw_kwargs` for direct pre-binding, while dynamic parameters are put into `eval_kwargs`.
2. **Generate Evaluation Plan (EvaluationPlan)**: For `eval_kwargs`, Slyme calls `prepare_eval_plan` to generate an optimized evaluation plan.
3. **PyTree Flatten and Batch Processing**:
   - Dependencies may be complex nested structures (such as dictionaries containing lists, lists containing `Ref` objects). Slyme uses the underlying `CTX_EVAL_ENGINE` (based on PyTree) to "flatten" nested structures into a one-dimensional list of leaf nodes.
   - Then, traversing these leaf nodes, it looks up corresponding evaluators in the `EVALUATOR_REGISTRY` registry (such as `ref_evaluator` for `Ref` type, `expression_evaluator` for `Expression`).
   - Grouping the same evaluators together generates a batch-based execution plan `EvaluationPlan`. This design merges scattered data access, greatly optimizing performance.

::: tip
The evaluation plan is generated across parameters. That is, if a Node has multiple parameters annotated with `Auto`, Slyme processes them together, summarizing all the same evaluation types for batch processing, greatly improving evaluation efficiency.
:::

### 2.3 Runtime Execution

When `*Exec` is truly called via `__call__(ctx)`, the final step of dependency injection begins:

1. **Execute Evaluation Plan**: Call `execute_eval_plan(ctx, eval_plan)`.
2. **Batch Extraction**: The execution engine calls Evaluators in batches. For example, for all Ref objects, `ref_evaluator` calls the underlying efficient `ctx.extract(refs)` in one go to get values corresponding to all paths; for expressions, it passes `ctx` for batch computation. Note that when batch evaluating Refs, Context Hook is called only once (passing batched Refs and values), making it convenient for Hook developers to optimize performance.
3. **Structure Restoration (Unflatten)**: After obtaining all computed leaf node values, reassemble them into the original dict/list structure according to the original nested structure.
4. **Transparent Execution**: Finally, these dynamically computed dependencies are merged with static `raw_kwargs` and passed to the developer-defined Node function. From the developer's perspective, they receive fully unpacked pure data.

### 2.4 Notes: Timing of Auto Eval and Higher-Order Nodes

::: warning
The evaluation object of `Auto` is the **initially passed `ctx` parameter** when the function is called. Evaluation happens **before** the Node function body truly executes.

If you are creating a **higher-order Node** (for example, inside a @node function you loop through and call other @nodes, returning a new `ctx`), please be sure to note: parameter values injected via `Auto` **will not** automatically update with the new `ctx` generated by your internal calls to other @nodes.

For example, if you are writing a training loop @node and need to get the latest loss after each step executes:
- **Wrong Approach**: Mark the `loss` parameter as `Auto`. This way, you can only get the old data evaluated when entering the loop.
- **Correct Approach**: Keep the `loss` parameter as a normal `Ref`, so you can manually call `ctx.get(loss)` after getting the latest `ctx` inside each loop body to dynamically get the latest value.

If you want to dynamically evaluate complex nested structures (similar to structures supported by `Auto`) inside a higher-order @node function, you can manually call the underlying evaluation API:
```python
from slyme.node.eval import eval_tree, async_eval_tree

# Inside a higher-order @node's execution logic, manually evaluate complex nested structures
resolved_values = eval_tree(current_ctx, complex_ref_structure)
```
:::
