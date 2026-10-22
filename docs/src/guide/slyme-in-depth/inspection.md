# Inspection

`slyme.inspection.inspect(value, tree_rules=..., interpreters=..., type_names=..., env=...)` returns an in-memory inspection graph with configurable raw-value retention. It observes the current structure; it does not execute a Node graph or predict a future execution trace. All configuration is explicit; no Context is required. For ordinary data, pass `DATA_RULES` from `slyme.tree.common`. Applications can obtain their scoped configuration using `resolve_inspection_options(ctx)`:

```python
from slyme.context import Context, Ref, Schema
from slyme.context.inspection import resolve_inspection_options
from slyme.inspection import inspect
from slyme.node import Auto, node


@node
def task(ctx, *, value):
    return value


ctx = Context()
ctx.declare({"input": Schema.leaf()})
ctx.set("input", 42)
graph = task(value=Auto({"items": [Ref("input")]}))

options = resolve_inspection_options(ctx)
document = inspect(graph, **options, env=ctx)
assert "value" not in document["objects"][document["root"]["$ref"]]
context_document = inspect(ctx, **options)  # Context observes its own values.
ctx.dispose()
```

## Names and responsibilities

Names follow their roles rather than sharing one grammatical form. `inspection` names the feature; `inspect()` performs it and returns an `InspectionGraph`. An `Interpreter` is a synchronous function, `Interpretation` is its returned value and environment, and `interpretation` identifies its record or relationship in the graph. `interpreters` maps types to these functions; `interpreter_resolver` selects one and has type `InterpreterResolver`. `resolve_inspection_options(ctx)` resolves scoped configuration without inspecting a value. Structural expansion separately uses the existing `TreeRules`, `TreeHandler` and `TreeResolver` names.

## Two independent operations

**Children** come from `TreeRules` and `flatten_one()`. Defaults cover data containers, Node/Wrapper bindings and metadata, Auto payloads, Context ownership children and declarations, Scopes, Refs, and Compose layers. Node and Wrapper values inside any parameter continue expanding. `Wrapper.compose()` creates a temporary call chain; inspection does not infer relationships from its closures or mutate the original objects.

**Interpretation** uses a mapping from exact types to synchronous functions. Each function receives `(value, env)` and returns `Interpretation(value, env=...)` or `None`. Omitting `env` inherits the input environment; explicitly passing `None` clears it. Returning `None` means unavailable; `Interpretation(None)` means an observed value of `None`.

With a Context environment, default interpreters read Refs through `ctx.get()` and call a Compose's configured query through `resolve(ctx.scope)`. Compose without a query remains unavailable. Context and ContextView interpret their own visible Ref-to-value mappings and select their owning Context as the result's environment. With these adapters, `inspect(target_ctx, **options)` needs no extra environment; `inspect(node, **options)` without an environment still describes its stored structure. The configuration source, target object and interpretation environment are independent.

Both structural children and interpreted values continue through structure expansion and interpretation. Children inherit their current environment; only the interpreted result uses an environment explicitly supplied by `Interpretation.env`. A query can therefore expose Nodes nested in arbitrary containers without executing those Nodes. The inspector never evaluates Auto, invokes Node/Wrapper bodies, awaits values, drives generators, or calls arbitrary `repr`, getters, or iteration protocols. Unknown objects remain opaque references. Queries and user-supplied adapters are ordinary Python code: keep them synchronous, lightweight, stable during the call, and free of side effects. They are not sandboxed or interrupted by the inspector.

## Extension

Root Contexts install four register-mode Composes, exposed by constants from `slyme.context`:

| Ref | Path | Contribution |
| --- | --- | --- |
| `INSPECTION_TREE_RULES_REF` | `$.inspection.tree_rules` | `TreeRules` |
| `INSPECTION_INTERPRETERS_REF` | `$.inspection.interpreters` | Exact type → synchronous interpreter |
| `INSPECTION_TYPE_NAMES_REF` | `$.inspection.type_names` | Exact type → semantic type name |
| `INSPECTION_RAW_VALUE_TYPES_REF` | `$.inspection.raw_value_types` | Exact type → `None`; keys allow raw-value retention |

Register through `ctx.effect()` to own contribution cleanup. Each layer rejects duplicate exact types; child layers override inherited entries in Scope C3 order. Withdrawal reveals the next applicable entry. Subclasses need separate registrations. An interpreter returning `None` disables an inherited observation.

`resolve_inspection_options(ctx)` resolves configuration using `ctx.scope` and returns keyword arguments: `tree_rules`, `interpreters`, `type_names`, and `raw_value_types`. It does not observe objects or choose an `env`. All configurations are shallow snapshots; `raw_value_types` is a fresh `dict[type, None]` whose keys form the allowlist. Resolve again to observe configuration changes. Switching interpretation environments does not reselect configuration. Missing configuration raises rather than falling back to the root or built-in adapters.

`type_names` names types; `raw_value_types` independently controls which records contain a raw `value`. A collection is an exact-type allowlist, not an encoder or a serialization validator; `None` retains every original value. Calling `inspect()` directly defaults to an empty allowlist. To retain all values with Context configuration, resolve `options = resolve_inspection_options(ctx)`, then pass `tree_rules=options["tree_rules"]`, `interpreters=options["interpreters"]`, `type_names=options["type_names"]` and `raw_value_types=None` explicitly to `inspect()`. Context defaults register `SCALAR_TYPES` from `slyme.inspection.common`: `NoneType`, `bool`, `int`, `float`, and `str`. Add types with `ctx.effect(lambda: compose.register(ctx.scope, {MyType: None}))`, where `compose = ctx.get(INSPECTION_RAW_VALUE_TYPES_REF)`. It uses `TypeLayer[None]`: duplicate types in one layer are rejected, and withdrawal reveals inherited entries. To replace inherited defaults, first derive a Context with `bindings={compose: ScopeBinding(blocked=True)}`, then register the desired types there. An empty visible composition retains no raw values. Subclasses require explicit inclusion. Neither setting changes structural expansion or interpretation.

Semantic type names identify object-table records independently of Python class names. They do not change structural handlers or interpreters and apply equally to scalars and other objects. Unknown objects need no semantic type to be displayed generically.

```python
from dataclasses import dataclass

from slyme.context import Context, Metadata, INSPECTION_TREE_RULES_REF, INSPECTION_TYPE_NAMES_REF
from slyme.context.inspection import resolve_inspection_options
from slyme.inspection import inspect
from slyme.tree import AttributeKey, TreeAux, TreeHandler, TreeRules


@dataclass(frozen=True)
class Description(Metadata):
    text: str


rules = TreeRules(
    {
        Description: TreeHandler(
            lambda value: (
                (value.text,),
                TreeAux(children_keys=(AttributeKey("text"),)),
            ),
            None,
        ),
    }
)
ctx = Context()
plugin = ctx.derive()
plugin.effect(
    lambda: plugin.get(INSPECTION_TREE_RULES_REF).register(plugin.scope, rules)
)
plugin.effect(
    lambda: plugin.get(INSPECTION_TYPE_NAMES_REF).register(
        plugin.scope, {Description: "example.description"}
    )
)
document = inspect(Description("A task"), **resolve_inspection_options(plugin))
ctx.dispose()
```

### Dispatch control

`tree_rules` maps exact types to structural handlers through `TreeRules`; `interpreters` is an exact-type mapping to interpreters. The optional `tree_resolver=TreeResolver(...)` and `interpreter_resolver=callback` independently override those defaults:

| Resolver result | Tree | Interpretation |
| --- | --- | --- |
| `False` | Use the exact-type tree rule | Use the exact-type interpreter |
| `True` | Skip structural expansion | Skip interpretation without creating a record |
| Handler | Use the returned `TreeHandler` | Use the returned `Interpreter` |

The tree resolver receives `value` and runs once per object. Inspection accepts only `takes_aux=False`; structure must not depend on a path, environment, or visit count. The interpreter resolver receives `(value, env)` and runs once per object/environment pair. It can select an interpreter even when no type rule exists. It selects a function, not an `Interpretation` result. `InterpreterResolver` is the exported callable type; no resolver list or extra dispatch object is needed. Resolvers are explicit per-call arguments, separate from the rule snapshots returned by `resolve_inspection_options()`.

Stopping one side does not stop the other. Set both resolvers to return `True` for an object to leave only its reference and basic description. An interpreter returning `None` still records `unavailable`; a resolver returning `True` does not invoke it. Resolver exceptions propagate. Scalars use the same dispatch as every other value; there is no built-in terminal type.

Reconstruction metadata in `TreeAux.metadata` is not included in the graph. All structural entries use `role="child"`. Their keys preserve the original TreeKey objects, including shared identity, and pass through the same structural and interpretation rules as values. No key's `resolve()` or `codify()` method is called. Missing `children_keys` uses ordinal indices, which also participate in dispatch.

`slyme.inspection.common` provides optional `KEY_INSPECTION_TREE_RULES` and `KEY_INSPECTION_TYPE_NAMES` for standard Tree keys. Each key exposes one child containing its name, mapping key, or index, tagged respectively as `slyme.tree.attribute_key`, `slyme.tree.mapping_key`, or `slyme.tree.sequence_key`. Root Contexts install these ordinary, overridable rules. They are not built into the inspector.

Framework adapters live in `slyme.context.inspection` and `slyme.node.inspection` and are installed by `context/default.py`. The public `inspect` function directly exposes the engine; it has no Context-specific wrapper.

Inspection is not a redaction mechanism: an interpreter adds a relation without replacing the original value. Applications exporting observations must apply their own encoding, truncation and redaction policy.

## Inspection graph

The returned dictionary contains a root reference and an object table:

```typescript
type Reference = { $ref: string };
interface InspectionGraph {
  root: Reference;
  objects: Record<string, {
    kind: string;
    value?: unknown;
    type?: string;
    label?: string;
    attributes?: Record<string, unknown>;
    entries: Array<{ role: string; key?: Reference; value: Reference }>;
  }>;
}
```

Object IDs are local to the graph. Shared objects and cycles reuse references. Every admitted value has a record; types allowed by `raw_value_types` include the original object in `value`, or all types when it is `None`. Omission does not mean the value is `None`: an allowed `None` is stored explicitly. Synthetic interpretation records have no raw `value`. Root and entry references always point into the object table; entries retain order. The source object's `interpretation` entry identifies its observation; no reverse `source` or `parent` entries are stored. Consumers can index incoming entries when needed.

`kind` describes the representation; optional `type` identifies its semantics; `label` is display-only. Built-in semantic types are `slyme.node`, `slyme.wrapper`, `slyme.auto`, `slyme.context`, `slyme.context_view`, `slyme.ref`, `slyme.scope`, `slyme.identity`, `slyme.scope_binding`, `slyme.ref_entry`, `slyme.ref_declaration`, `slyme.ref_leaf_config`, `slyme.ref_container_config`, and `slyme.compose`. These identifiers and adapter field names are independent of implementation module paths and storage attributes. A type does not imply that structural expansion succeeded.

| Kind | Meaning |
| --- | --- |
| `object` | A structurally expanded object, with `child` entry roles |
| `opaque` | An object reference with a type label, without structural expansion |
| `interpretation` | An observation with `env` and, when resolved, `value`; `value_env` records an environment change |

Interpretation `attributes.state` is `resolved` or `unavailable`. Runtime errors from handlers, child iterators, resolvers and interpreters propagate without returning a partial graph. Ordinary exceptions gain object, environment type, depth and operation context through `enrich_exception`; their identity, traceback and cause are preserved. Control exceptions propagate unchanged. Budget and depth truncation remain part of the returned graph.

Container and function classes have no special display classification. A function without a registered handler stays opaque; a custom container with a handler is an `object`, just like a list. Consumers use semantic types and configured descriptions for specialized displays.

Children are recorded once per object identity. Interpretation is recorded once per object/environment identity pair. Equality is never invoked: equal but distinct values or environments remain distinct, while the same object is reused. This applies to scalars too; Python may reuse immutable objects. An environment override emits `value_env` when it is not the original object. A shared container is followed under each environment so its descendants receive the right environment. Annotating an environment does not by itself expand that object. Object IDs cannot identify objects across observations.

The graph strongly retains allowed raw values without copying, truncating or encoding them. With `raw_value_types=None`, this includes containers, functions and environment objects; the graph may retain cycles and is not generally JSON serializable. All values are retained internally during traversal; excluded values are not directly retained by the returned graph. An allowed container can still hold excluded objects transitively. Mutable payloads can change after inspection, while recorded relations remain unchanged. Release the graph when those references are no longer needed. Retention does not keep disposed external resources usable.

Serialization belongs to consumers. Context defaults omit raw Node, Context, function and container objects, allowing ordinary finite scalar observations to be JSON encoded. The allowlist is not a universal guarantee: NaN fails strict JSON, large integers can lose precision in other languages, and explicitly allowed containers can contain unsupported values or cycles. Exporters own those policies, string limits and redaction. Inspection does not define a wire format or reconstruction format.

No interpreter means no interpretation entry; an interpreter returning `None` records `unavailable`; returning `Interpretation(None)` records a resolved `None`. Missing children or values in an opaque or truncated observation are not evidence of absence. Relationship snapshots in `tests/fixtures/inspection` normalize IDs and omit raw payloads and display labels while preserving entry order; they are test projections, not an export format.

## Limits

`budget: int | None = None` limits the sum of object records and relationship entries. Every record, including scalars and interpretation records, costs one unit; every entry costs one unit. Reusing an object costs no additional record, but a new relationship to it still costs one entry. A supplied budget must be positive so the root record can always be represented. The inspector admits each relationship with its new targets together. At the first admission that cannot fit, traversal stops adding relationships; some budget can remain unused. Existing records are marked with `attributes.truncated=true` and `reason="budget"`; no extra truncation record is allocated. All records remain reachable and no reference points to an absent record. Filling the budget alone does not mark a record as truncated: dispatch and expansion may still run to determine whether an additional relationship is needed. Empty containers and uninterpreted leaves require no further budget.

`max_depth: int | None = None` limits logical exploration from root depth zero. Tree child values and keys, and interpretation results, add one level. Synthetic interpretation records and environment annotations add no exploration depth; annotated environments are not followed. At the depth limit, resolvers, handlers and interpreters still run to determine whether more content exists. Empty containers, uninterpreted leaves and unavailable interpretations are complete, not truncated. An actual child or interpretation result beyond the limit is omitted, and its containing record gets `reason="max_depth"`. A child iterator stops at its first omitted child. Breadth-first traversal uses the shallowest path for each value/environment pair. Cached structural children are not followed past the limit in another environment; shared records can retain relations from shallower visits while being marked incomplete for a deeper visit. `max_depth=0` inspects the root without following its children or interpreted results; interpretation records and environment annotations may still be present. Negative depths are rejected.

Both limits default to unlimited. Output consumers such as debug interfaces can choose their own limits. They bound graph records, relationships and exploration depth, not payload sizes, retained memory or callback time. A depth limit alone cannot bound an infinite child iterator; use a finite budget. An interpreter producing an endless chain of fresh objects needs a finite budget or depth limit. A callback that never returns cannot be interrupted by either limit. Incomplete observations can lack their usual entries; consumers must check truncation flags.

The engine uses an explicit work queue and supports deep or cyclic graphs without recursive Python traversal. Tree's whole-tree operations also use explicit stacks, but visit shared objects separately for each occurrence and do not support cycles. Inspection records shared objects and cycles as references.
