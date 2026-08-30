# Dependency Injection

Slyme binds build parameters by keyword and optionally evaluates their values with the runtime `Context`.

## Spec collection

Every keyword-only function parameter produces a `Spec`. Defaults, `default_factory`, and `auto_eval` may be configured explicitly; `Auto[T]` is shorthand for `Spec(auto_eval=True)`.

Factory calls validate keyword names and apply defaults immediately. A missing required value becomes `UNDEFINED` and is rejected when the Node or Wrapper is called.

## Call-time evaluation

For each call, Slyme:

1. reads the object's current parameters;
2. separates static values from Auto-evaluated values;
3. creates an `EvaluationPlan` for the dynamic values;
4. batches leaves by evaluator type;
5. resolves them with the supplied Context and invokes the user function, passing static containers directly.

`Ref` evaluators batch Context extraction. `Node` evaluators call value-producing child Nodes. More realization types can be added through `EVALUATOR_REGISTRY` without teaching Slyme their internal execution mechanism.

The plan is deliberately temporary. This avoids invalidation bookkeeping when a live Node graph or future Slot registry changes between calls.

## Evaluation timing

Auto values are resolved once when their containing Node or Wrapper is entered. If a higher-order Node invokes children that later mutate Context, read the latest value explicitly with its `Ref` after those calls instead of relying on an earlier Auto value.
