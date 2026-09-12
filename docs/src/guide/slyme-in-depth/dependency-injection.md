# Dependency Injection

Slyme binds build parameters by keyword and optionally evaluates their values with the runtime `Context`.

## Spec collection

Every keyword-only function parameter produces a `Spec`. Defaults, `default_factory`, and `auto_eval` may be configured explicitly; `Auto[T]` is shorthand for `Spec(auto_eval=True)`.

Factory calls validate keyword names and apply defaults immediately. A missing required value becomes `UNDEFINED` and is rejected when the Node or Wrapper is called.

## Call-time evaluation

For each call, Slyme:

1. reads the object's current parameters;
2. separates parameters by their declared `auto_eval` flag;
3. flattens Auto parameters according to PyTree rules;
4. batches leaves by evaluator type;
5. resolves them with the supplied Context, reconstructs Auto containers, and invokes the user function, passing non-Auto values directly.

`Ref` evaluators batch effective Context extraction. `Node` evaluators call value-producing child Nodes, giving each an owned child Context bound to a distinct child Scope. Synchronous tree evaluation runs child Nodes in evaluation order. Asynchronous tree evaluation schedules child evaluations as tasks; asynchronous children may overlap at suspension points, while each synchronous child runs inline on the event-loop thread until it returns. Their Scope-local writes remain isolated in either mode. More realization types can be added through `EVALUATOR_REGISTRY` without teaching Slyme their internal execution mechanism.

`eval_tree(ctx, tree)` performs traversal, batched evaluation, and reconstruction in one call. Ordinary leaves and evaluator results retain their identities; returned values are not recursively evaluated. Auto containers are reconstructed even when no leaf requires evaluation.

## Evaluation timing

Each Node or Wrapper call takes a shallow snapshot of its parameter bindings. Auto traversal and evaluation occur immediately before its user function is invoked. A Wrapper that calls `call_next` repeatedly triggers a fresh traversal each time, observing in-place changes to Auto containers and current Context values; replacing a Node parameter binding affects the next Node call. A short-circuiting Wrapper does not traverse or evaluate the wrapped Node's Auto parameters.

An Auto child may return a derived value, but Slyme disposes its Context and registered effects before parent execution continues. Asynchronous child execution or cleanup makes the enclosing call awaitable; cancellation also waits for child cleanup. If a higher-order Node explicitly invokes children with its own Context, read a later value through its Ref after those calls instead of relying on the earlier Auto value.
