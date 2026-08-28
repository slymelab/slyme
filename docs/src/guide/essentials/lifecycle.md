# Lifecycle

Slyme uses one live `Node` graph rather than separate definition and execution trees. Creating a decorated function builds a mutable Node; calling it executes that same Node from a local snapshot.

## Build and modify

```python
from slyme.context import Context, R
from slyme.node import Auto, node

@node
def process(ctx: Context, /, *, timeout: int = 30, data: Auto[list]):
    return timeout, data

task = process(data=[R.user.age, R.user.name])
task.timeout = 60
```

Node parameters and wrappers may be changed between calls. A change never requires recompiling the whole graph and becomes visible on the next call.

## Call-local snapshot

At the start of each Node or Wrapper call, Slyme:

1. copies that object's current parameter mapping;
2. recursively freezes ordinary Python containers (`list` to `tuple`, `dict` to a read-only mapping);
3. treats Node-like objects as leaves, so it does not recursively prepare the composition graph;
4. validates parameters, builds the temporary Auto evaluation plan and wrapper chain;
5. executes the user function.

The snapshot remains stable for that call. Mutations made concurrently or later are observed only by later calls. This model also allows future Slot relationships and Node graphs to contain cycles without a recursive preparation pass following them.

## Auto values

Container freezing applies to structures supplied as Node parameters, not to values retrieved from `Context`:

```python
ctx = Context()
ctx.update({R.a: 1, R.b: 2, R.items: [1, 2]})

process(data=[R.a, R.b])(ctx)  # data is (1, 2)
process(data=R.items)(ctx)     # data is the list stored in Context
```

Call a Node directly with `node(ctx)` when managing Context yourself. Use `node.run(...)` as the application boundary when Slyme should prepare external inputs, validate `Arg` metadata, and extract outputs.
