# Inspection

`slyme.inspection.inspect()` builds an in-memory graph of an object's structure and environment-dependent interpretations. It accepts explicit configuration and requires no Context.

```python
from slyme.inspection import Interpretation, inspect
from slyme.tree.common import DATA_RULES

class Lookup:
    def __init__(self, name):
        self.name = name

shared = [Lookup("answer")]
value = [shared, shared]
graph = inspect(
    value,
    env={"answer": 42},
    tree_rules=DATA_RULES,
    interpreters={Lookup: lambda value, env: Interpretation(env[value.name])},
    raw_value_types=(type(None), bool, int, float, str),
)
root = graph["objects"][graph["root"]["$ref"]]
assert root["entries"][0]["value"] == root["entries"][1]["value"]
```

## Structure and interpretation

`tree_rules: TreeRules` supplies exact-type structural handlers. `flatten_one()` obtains each object's children once; shared objects and cycles reuse references. Keys and values both participate in inspection. Without explicit child keys, ordinal indices serve as keys. Reconstruction metadata is not included.

`interpreters` maps exact types to synchronous `Interpreter` functions accepting `(value, env)`. Return `Interpretation(value)` to inherit the environment, `Interpretation(value, env=...)` to select another environment, or `None` when unavailable. Explicit `env=None` clears the environment. `Interpretation(None)` is a resolved value, not an unavailable result.

Structural children and interpreted results both undergo further expansion and interpretation. Children inherit their current environment; an interpretation's environment override applies only to its result. Structure is cached per object identity; interpretation is recorded once per object/environment identity pair. Shared containers are followed under each environment. Equality is not used for deduplication.

Callbacks must be synchronous, stable during inspection and free of side effects. They are ordinary Python code, not sandboxed. Unknown objects stay opaque. The inspector does not invoke arbitrary getters, `repr()`, generators or awaitables; explicit handlers and interpreters determine what is accessed.

## Dispatch and descriptions

Optional resolvers independently override structural and interpretation dispatch:

| Result | `tree_resolver` | `interpreter_resolver` |
| --- | --- | --- |
| `False` | Use the exact-type tree handler | Use the exact-type interpreter |
| `True` | Skip structural expansion | Skip interpretation without recording it |
| Handler | Use the returned `TreeHandler` | Use the returned `Interpreter` |

Supply a `TreeResolver` with `takes_aux=False` for structural dispatch. It receives the value; structure cannot depend on a path or environment. The `InterpreterResolver` callable receives `(value, env)`. Stopping either operation does not stop the other.

`type_names` maps exact types to optional semantic names, independently of structural handlers and interpreters. `raw_value_types` is an exact-type allowlist controlling which records retain their original value. Its default is empty; subclasses require separate inclusion. Neither option changes traversal.

## Graph and ownership

`InspectionGraph` contains `root: {"$ref": str}` and `objects: dict[str, InspectionObject]`. IDs are local to one inspection. Every reference points to a record in the table; relationship order is preserved.

Records contain `kind` and `entries`, with optional `value`, `type`, `label` and `attributes`. Each `InspectionEntry` has `role`, a `value` reference and optionally a `key` reference.

| Kind | Contents |
| --- | --- |
| `object` | Structural `child` entries |
| `opaque` | A value's identity and type label, without structural expansion |
| `interpretation` | `env`, a resolved `value`, and `value_env` when the environment changes |

The source record links to each interpretation through an `interpretation` entry. No reverse source or parent links are stored. Interpretation attributes contain `state="resolved"` or `state="unavailable"`. Environment annotations alone do not cause those objects to be traversed.

Allowed raw values are retained by reference, not copied or encoded. All visited values and environments are retained during traversal to preserve identity. The returned graph can retain excluded objects indirectly through allowed containers. Mutating a retained value does not update recorded relationships. Consumers own serialization and redaction: finite scalars can be JSON encoded, but NaN, large integers, unsupported objects and cycles require serializer-specific policies. Inspection is not a reconstruction format.

Handler, iterator, resolver and interpreter errors propagate rather than becoming graph records. Ordinary exceptions gain operation, object, environment type and depth information through `enrich_exception`; control exceptions propagate unchanged.

## Limits

`budget: int | None = None` limits object records plus relationship entries. Each costs one unit. Reusing an object saves a record, not an edge. A budget must be positive to accommodate the root. Each relationship and its new targets are admitted together; the first failed admission stops new relationships and marks the affected record with `attributes.truncated=True, reason="budget"`. Some capacity may remain unused. Filling the budget exactly does not itself mark truncation; callbacks may still run to determine whether further content exists. All records remain reachable.

`max_depth: int | None = None` limits exploration from root depth zero. Child keys, child values and interpreted results add one level; interpretation records and environment annotations do not. At the limit, callbacks still run: only actual omitted children or results produce `reason="max_depth"`. Empty containers, uninterpreted leaves and unavailable interpretations need not be truncated. Child iteration stops at the first omitted child. Breadth-first traversal visits each object/environment pair through its shallowest path. Shared records can retain relationships from shallower visits while being marked incomplete for another, deeper visit. Negative depths are rejected.

Both limits default to unlimited. They bound output and exploration, not callback duration or raw payload size. A depth limit alone does not bound an infinite child iterator; supply a finite budget. Neither limit interrupts a callback that never returns. Consumers must check truncation attributes before treating missing relationships as absent.
