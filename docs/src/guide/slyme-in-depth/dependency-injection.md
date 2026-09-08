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

`Ref` evaluators batch effective Context extraction. `Node` evaluators call value-producing child Nodes, giving each an owned child Context bound to a distinct child Scope. Synchronous tree evaluation runs child Nodes in evaluation order. Asynchronous tree evaluation schedules child evaluations as tasks; asynchronous children may overlap at suspension points, while each synchronous child runs inline on the event-loop thread until it returns. Their Scope-local writes remain isolated in either mode. More realization types can be added through `EVALUATOR_REGISTRY` without teaching Slyme their internal execution mechanism.

The plan is deliberately temporary. This avoids invalidation bookkeeping when a live Node graph changes between calls.

## Evaluation timing

Auto values are resolved once when their containing Node or Wrapper is entered. An Auto child may return a derived value, but Slyme disposes its Context and registered effects before parent execution continues. Synchronous Auto evaluation rejects asynchronous cleanup before setup; asynchronous evaluation awaits cleanup, including after cancellation. If a higher-order Node explicitly invokes children with its own Context, read a later value through its Ref after those calls instead of relying on the earlier Auto value.
