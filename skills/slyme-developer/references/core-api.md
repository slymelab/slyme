# Core API by Example

Treat these snippets as the canonical best-practice template for Slyme code. Follow their structure, lifecycle, signatures, naming, and composition patterns while adapting names and domain logic. The comments are normative and the snippets compose into one module.

## Imports

```python
from collections.abc import Callable, Mapping, Sequence

from slyme.builder import builder
from slyme.cli import parse_and_inject
from slyme.context import ARG, Arg, Context, R, Ref
from slyme.node import Auto, Node, expression, node, sequential_exec, wrapper
```

## Expressions and dependency injection

```python
@expression
def calculate_value(
    # Parameters before `/` are runtime parameters supplied by Slyme.
    # @node and @expression receive exactly one: Context.
    ctx: Context,
    /,  # Separates runtime parameters from build-time parameters.
    *,
    # Parameters after `*` are build-time parameters and must be passed by keyword.
    # Auto means the caller may bind a Ref or @expression. Before this function
    # body starts, Slyme resolves it against ctx and injects the concrete float.
    value: Auto[float],
    # A parameter without Auto is ordinary static configuration. It is bound
    # while building the definition and reused unchanged during execution.
    scale: float = 2.0,
) -> float:
    # Expressions derive and return a value. They do not update Context.
    return value * scale
```

## Atomic state transitions

```python
@node
def increment(
    ctx: Context,
    /,
    *,
    # Keep Ref itself when the function decides when to read or write this path.
    # Name it `counter`, not `counter_ref` or `ref_counter`.
    counter: Ref[int],
) -> Context:
    # The Ref and the concrete value of the same path coexist in this scope, so
    # the value is named with exactly one trailing underscore.
    counter_ = ctx.get(counter) + 1
    # Context is immutable: set returns the next Context and does not mutate ctx.
    return ctx.set(counter, counter_)
```

## Fixed and keyed composition slots

```python
@node
def conditional(
    ctx: Context,
    /,
    *,
    enabled: Auto[bool],
    enabled_node: Node,
    disabled_node: Node,
) -> Context:
    # Use named Node parameters when the children have fixed, distinct roles.
    return enabled_node(ctx) if enabled else disabled_node(ctx)


@node
def dispatch(
    ctx: Context,
    /,
    *,
    key: Auto[str],
    branches: Mapping[str, Node],
) -> Context:
    # A Mapping makes the set of keyed branches an extensible composition slot.
    return branches[key](ctx)
```

## Ordered composition slots and parameter evaluation

```python
@node
def execute(
    ctx: Context,  # Runtime Context; Slyme supplies it when the Exec is called.
    /,
    *,
    # In build(), this receives a Ref. At runtime, Auto performs ctx.get(ref)
    # before entering the body, so this parameter is already the concrete dict.
    auto_with_ref: Auto[dict],
    # In build(), this receives an @expression definition. At runtime, Slyme
    # evaluates that expression and injects its float return value.
    auto_with_expression: Auto[float],
    # Auto traverses nested PyTrees and resolves every Ref/@expression leaf while
    # preserving the prepared container structure and ordinary literal leaves.
    auto_with_pytree: Auto[tuple[object, ...]],
    # Do not use Auto for a path this node must update: the Ref is required by set.
    output: Ref[dict],
    # Do not use Auto when the latest value must be read after child nodes run.
    # Auto would only contain the snapshot taken when execute() was entered.
    changing: Ref[int],
    # Each Sequence is an ordered, extensible composition slot. Keep loop and
    # final execution as separate slots because they are distinct phases.
    loop_nodes: Sequence[Node],
    final_nodes: Sequence[Node],
    # Static control-flow configuration remains an ordinary build-time value.
    rounds: int = 1,
) -> Context:
    for _ in range(rounds):
        # Parent prepare() recursively prepares contained Node definitions.
        # sequential_exec threads each returned Context into the next child.
        ctx = sequential_exec(ctx, loop_nodes)

    # A second node container provides another independently extensible position.
    ctx = sequential_exec(ctx, final_nodes)

    # Read only after all children have run so the value reflects their updates.
    changing_ = ctx.get(changing)

    # `output` is the destination Ref; `output_` is the value for that same path.
    output_ = {
        "from_ref": auto_with_ref,
        "from_expression": auto_with_expression,
        "from_pytree": auto_with_pytree,
        "latest": changing_,
    }

    # update() can write multiple paths atomically. Always retain its return value.
    ctx = ctx.update({changing: changing_})
    # @node must return Context. set() returns the final updated Context.
    return ctx.set(output, output_)
```

## Wrappers

```python
@wrapper
def trace(
    # @wrapper has exactly these three runtime parameters, in this order.
    ctx: Context,
    wrapped: Node,  # The Node on which this wrapper is mounted.
    call_next: Callable[[Context], Context],  # Next wrapper or wrapped Node.
    /,
    *,
    name: str,  # Wrapper-specific build-time configuration.
) -> Context:
    print(f"{name}: start")
    # A normal onion-style wrapper calls the next layer and keeps its Context.
    # Omitting this call intentionally short-circuits the wrapped Node.
    ctx = call_next(ctx)
    print(f"{name}: end")
    return ctx
```

## Build-time assembly

```python
@builder
def build() -> Node:
    # @builder executes only assembly code and returns the outermost Node Def.
    # Calling calculate_value(), increment(), or execute() here does not run them.
    # Create application Refs where the Node tree is assembled instead of
    # threading them through the builder's public interface.
    # Wrappers may only be attached to a Node through add_wrappers().
    return execute(
        # Auto + Ref: source is resolved with the runtime Context.
        auto_with_ref=R.input.source,
        # Auto + Expression: calculate_value is evaluated before execute runs.
        auto_with_expression=calculate_value(
            value=R.input.score(
                metadata={ARG: Arg(type=float, required=True, help="Input score")}
            )
        ),
        # Auto + PyTree: Ref and Expression leaves may occur at arbitrary depth.
        auto_with_pytree=[
            R.input.label,
            {"score": R.input.score},
            calculate_value(value=R.input.score, scale=3.0),
        ],
        # At prepare(), mutable definition containers are frozen: this list becomes
        # a tuple and its dict becomes a read-only mapping. At runtime, the values
        # returned by Ref/expression evaluation retain their own types; a list read
        # from Context, for example, remains that list rather than being frozen.
        changing=R.state.counter,
        output=R.output.result,
        # Reuse the same atomic Node in multiple higher-order composition slots.
        loop_nodes=[increment(counter=R.state.counter)],
        final_nodes=[increment(counter=R.state.counter)],
        rounds=2,
    ).add_wrappers(trace(name="execute"))
```

## Application boundary

```python
def run() -> Context:
    node_def = build()

    # Context.update writes several hierarchical Ref paths and returns a new Context.
    # Use R.a.b directly for a Ref path when no metadata is required.
    ctx = parse_and_inject(
        context=Context().update({
            R.input.source: {"name": "example"},
            R.input.label: "label",
            R.state.counter: 0,
        }),
        # Slyme traverses the Node tree and discovers its Ref dependencies,
        # including the Arg metadata on R.input.score(). Do not repeat tree-owned
        # dependencies in extra_refs; reserve it for Refs outside the Node tree.
        node=node_def,
        cli_args=["--input.score", "2.5"],
    )

    # Build/modify the Def first, then prepare exactly once at the application
    # boundary. The resulting immutable Exec can be safely reused.
    node_exec = node_def.prepare()
    return node_exec(ctx)
```

## Evaluation and structure rules

`Auto` is `Annotated[T, spec(auto_eval=True)]`. It resolves against the Context entering the current node, so it is a snapshot. In a higher-order loop, retain a `Ref` and call `ctx.get(ref)` when the value must reflect child-node updates.

Node structure is directional: nodes may contain nodes and expressions; expressions may contain expressions; wrappers attach only through `.add_wrappers(...)`. Use `sequential(nodes=[...])` when the whole pipeline is a simple linear composition.
