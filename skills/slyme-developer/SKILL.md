---
name: slyme-developer
description: Build, extend, debug, or review downstream Python applications that use the Slyme functional execution framework. Use for defining and composing Slyme @node, @expression, @wrapper, and @builder functions; wiring Context, Ref/R, Auto, PyTrees, sequential flows, or CLI Arg metadata; preparing and executing node trees; and migrating downstream code away from deprecated Slyme patterns. Do not use for maintaining or changing Slyme framework internals.
---

# Develop with Slyme

Treat Slyme as an installed dependency of the target codebase. Implement application workflows with its public APIs; do not edit Slyme internals.

Before implementing Slyme code, read [references/annotated-example.md](references/annotated-example.md). Use its complete example as the canonical shape and its comments as correctness constraints.

## Workflow

1. Inspect the target project's dependency declaration and existing Slyme code. Match its supported Slyme version and local naming/module conventions.
2. Model runtime state paths first. Prefer the global immutable `R` factory (`R.input.items`) over spelling `Ref("input.items")`.
3. Split behavior by responsibility:
   - Use `@node` for side effects or state transitions. Return a `Context`.
   - Use `@expression` for derived values. Return the derived value, not a `Context`.
   - Use `@wrapper` for cross-cutting behavior around a node. Call `call_next(ctx)` exactly once unless short-circuiting is intentional, and return a `Context`.
   - Use `@builder` only to instantiate and compose node definitions. Do not perform runtime work in it.
4. Wire runtime dependencies through keyword-only build parameters using `R`, `Ref`, or expression definitions.
5. Use `Auto[T]` only when the function body needs the resolved `T`. Keep `Ref[T]` when the function must choose when or against which current `Context` to read/write.
6. Build a definition, call `.prepare()` once at the application boundary, then execute the resulting immutable object with a `Context`.
7. Test the smallest useful unit and the assembled pipeline. Assert returned values and verify the original `Context` remains unchanged after updates.

## Signature Rules

Write exact decorator signatures:

```python
@node
def step(ctx: Context, /, *, source: Auto[str], destination: Ref[str]) -> Context: ...

@expression
def derive(ctx: Context, /, *, source: Auto[str]) -> str: ...

@wrapper
def middleware(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], Context],
    /,
    *,
    label: str,
) -> Context: ...
```

- Put framework-supplied runtime parameters before `/`: exactly `ctx` for `@node` and `@expression`; exactly `ctx, wrapped, call_next` in that order for `@wrapper`.
- Put every caller-supplied build-time parameter after `*` and pass it by keyword.
- Give build-time defaults directly or with `spec(default=...)` / `spec(default_factory=...)`. Prefer `Auto[T]` over the equivalent advanced `spec(auto_eval=True)` form.
- Let `@builder` use a normal Python signature; its parameters configure assembly and it must return a node definition.

## Context and References

- Treat `Context` as hierarchical, structurally immutable state. Always retain or return the new value: `ctx = ctx.set(...)`, `ctx = ctx.update(...)`, or `return ctx.set(...)`.
- Use `R.path.to.value` for ordinary references. Use `Ref(...)` when constructing a path dynamically or when explicit metadata is clearer.
- Annotate a parameter `Ref[T]` when the body receives the reference itself. Read with `value_ = ctx.get(value)` and write with `ctx.set(value, value_)`.
- If both a `Ref` parameter and its concrete value occur in one scope, keep the clean domain name for the reference and add exactly one trailing underscore to the concrete value: `result: Ref[str]` and `result_ = ...`. Do not use `result_ref`, `ref_result`, or double underscores.
- Use `Context.update` for multiple paths and `Context.mutate` for atomic updates plus drops. Use `exists`, `keys`, `to_dict`, or `diff` for inspection and tests.

## Auto and PyTrees

Understand `Auto[T]` as `Annotated[T, spec(auto_eval=True)]`:

- At `.prepare()`, Slyme separates static parameters from parameters requiring evaluation and compiles a batched evaluation plan.
- At execution, before entering the decorated function body, Slyme evaluates `Ref`/`R` and `@expression` leaves against the input `ctx`, then reconstructs their original PyTree shape.
- PyTrees may nest supported containers such as `dict`, `list`, and `tuple`; literal leaves stay unchanged. This permits one `Auto[...]` argument to combine multiple references, expressions, and constants.
- `Auto` is outermost in the annotation: use `Auto[list[str]]`, not `list[Auto[str]]`.
- An `Auto` value is a snapshot resolved from the context supplied at node entry. It does not refresh after nested node calls change `ctx`. In higher-order nodes, keep a `Ref` and call `ctx.get(ref)` at the required time, or deliberately call `eval_tree(current_ctx, tree)`.

## Composition Rules

- A node may hold child nodes and expressions.
- An expression may hold expressions, but not nodes or wrappers.
- Attach wrappers only with `.add_wrappers(...)`; never pass a wrapper as an ordinary parameter.
- Prefer `sequential(nodes=[...])` for linear state-threading. Each child must receive and return the next `Context`.
- Keep definition-time mutation before `.prepare()`. Treat the prepared execution tree as frozen and reusable.
- Leave `@builder` structure checking enabled unless measured construction overhead justifies `@builder(check_structure=False)`.

## CLI Arguments

Attach `Arg` metadata to the same `Ref`/`R` used by the node, then call `parse_and_inject(context=ctx, extra_refs=[...])` before execution. This returns a new populated `Context`. The API also accepts `node=node_def` for dependency-tree discovery, but use it only after a focused test confirms that scanning works in the target Slyme version; prefer explicit `extra_refs` for portable downstream code.

Use `Arg(default=..., help=..., type=..., choices=..., required=..., nargs=..., aliases=..., metavar=...)`. Specify `type` when inference from a default is unavailable or misleading. Slyme handles booleans, generic lists/tuples, JSON dictionaries, enums, literals, and optionals.

## Avoid Deprecated or Incorrect Patterns

- Do not pass positional Scope dictionaries to node factories. Pass explicit keyword arguments with `R`.
- Do not use `Ref.key_path`, `CallKey`, `KeyPathExpr`, or `P`; derive values with `@expression` plus `Auto`.
- Do not mutate a `Context` in place or ignore the `Context` returned by a node.
- Do not manually `ctx.get()` an `Auto` parameter; it is already the resolved concrete value.
- Do not call `.prepare()` repeatedly inside loops or request handlers when the definition is unchanged.
- Do not make application code depend on `slyme` private modules when the symbol is exported from `slyme.node`, `slyme.context`, `slyme.builder`, or `slyme.cli`.

## Verification

Run the target project's formatter, type checker, and focused tests. At minimum, test:

- a node/expression in isolation with a constructed `Context`;
- nested PyTree resolution for every nontrivial `Auto` input;
- wrapper ordering or short-circuit behavior;
- the assembled builder through `.prepare()(ctx)`;
- CLI parsing with explicit `cli_args` when `Arg` metadata is added.
