# Core API by Example

The comments are part of the example: preserve these semantics while adapting names and domain logic.

```python
from collections.abc import Callable, Sequence

from slyme.builder import builder
from slyme.cli import parse_and_inject
from slyme.context import ARG, Arg, Context, R, Ref
from slyme.node import Auto, Node, expression, node, sequential_exec, wrapper


@expression
def calculate_value(
    ctx: Context,  # Framework-supplied runtime parameter.
    /,
    *,
    value: Auto[float],  # Keyword-only build parameter; receives a resolved value.
    scale: float = 2.0,  # Plain build-time configuration.
) -> float:
    return value * scale  # Expressions return values and do not update Context.


@node
def increment(ctx: Context, /, *, counter: Ref[int]) -> Context:
    counter_ = ctx.get(counter) + 1
    # Ref keeps the domain name; its value uses one trailing underscore.
    return ctx.set(counter, counter_)


@node
def execute(
    ctx: Context,
    /,
    *,
    auto_with_ref: Auto[dict],              # A Ref is resolved through Context.
    auto_with_expression: Auto[float],       # An expression is evaluated.
    auto_with_pytree: Auto[tuple[object, ...]],  # Ref/expression leaves are resolved deeply.
    output: Ref[dict],                       # Keep Ref when this node writes the path.
    changing: Ref[int],                      # Keep Ref for reads after child nodes run.
    loop_nodes: Sequence[Node],              # Flexible higher-order extension point.
    final_nodes: Sequence[Node],
    rounds: int = 1,
) -> Context:
    for _ in range(rounds):
        ctx = sequential_exec(ctx, loop_nodes)

    ctx = sequential_exec(ctx, final_nodes)
    changing_ = ctx.get(changing)  # Read the latest Context, not an Auto snapshot.
    output_ = {
        "from_ref": auto_with_ref,
        "from_expression": auto_with_expression,
        "from_pytree": auto_with_pytree,
        "latest": changing_,
    }
    ctx = ctx.update({changing: changing_})  # Batch updates use the returned Context.
    return ctx.set(output, output_)


@wrapper
def trace(
    ctx: Context,
    wrapped: Node,
    call_next: Callable[[Context], Context],
    /,  # A wrapper has exactly these three runtime parameters in this order.
    *,
    name: str,
) -> Context:
    print(f"{name}: start")
    ctx = call_next(ctx)  # Continue the wrapper/node chain.
    print(f"{name}: end")
    return ctx


@builder
def build(
    *,
    source: Ref[dict],
    score: Ref[float],
    label: Ref[str],
    counter: Ref[int],
    output: Ref[dict],
) -> Node:
    return execute(
        auto_with_ref=source,
        auto_with_expression=calculate_value(value=score),
        auto_with_pytree=[
            label,
            {"score": score},
            calculate_value(value=score, scale=3.0),
        ],
        # At prepare(), the list is frozen to a tuple. Resolved Ref/expression
        # values retain their own types and are not recursively frozen.
        changing=counter,
        output=output,
        loop_nodes=[increment(counter=counter)],
        final_nodes=[increment(counter=counter)],
        rounds=2,
    ).add_wrappers(trace(name="execute"))


def run() -> Context:
    # Create concrete Refs at the application boundary, never as module globals.
    source = R.input.source()
    score = R.input.score(
        metadata={ARG: Arg(type=float, required=True, help="Input score")}
    )
    label = R.input.label()
    counter = R.state.counter()
    output = R.output.result()

    node_def = build(
        source=source,
        score=score,
        label=label,
        counter=counter,
        output=output,
    )
    ctx = parse_and_inject(
        context=Context().update({
            source: {"name": "example"},
            label: "label",
            counter: 0,
        }),
        extra_refs=[score],
        cli_args=["--input.score", "2.5"],
    )

    node_exec = node_def.prepare()  # Freeze once after assembly.
    return node_exec(ctx)
```

`Auto` is `Annotated[T, spec(auto_eval=True)]`. It resolves against the Context entering the current node, so it is a snapshot. In a higher-order loop, retain a `Ref` and call `ctx.get(ref)` when the value must reflect child-node updates.

Node structure is directional: nodes may contain nodes and expressions; expressions may contain expressions; wrappers attach only through `.add_wrappers(...)`. Use `sequential(nodes=[...])` when the whole pipeline is a simple linear composition.
