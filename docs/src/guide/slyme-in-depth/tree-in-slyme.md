# Trees in Slyme

A tree is a nested structure whose containers define topology and whose unregistered objects are leaves. `slyme.utils.tree` provides stateless `TreeEngine` algorithms and immutable `TreeRules`. Every traversal receives `rules=` explicitly; the engine owns no registrations or Context.

## Rules and dispatch

`TreeRules` contains an exact-type handler mapping plus ordered `pre_resolvers` and `post_resolvers`. `TreeHandler(flatten, unflatten)` describes one container; `unflatten=None` permits traversal but rejects reconstruction. Dispatch checks an explicit `is_leaf` predicate, pre-resolvers, the exact type, then post-resolvers. Subclasses remain opaque unless explicitly handled; Python MRO lookup is not implicit.

A rule snapshot copies its handler mapping. `TreeRules.merge()` keeps the first handler for each type and concatenates each resolver phase independently. A `TreeDef` retains the reconstruction functions used during flattening, so rebuilding does not look up current rules.

For traversal without a Context, explicitly import `DATA_RULES` from `slyme.context.default` and `NODE_RULES` from `slyme.node.core`. The former handles ordinary data containers; the latter handles only Node, Wrapper, and Auto. Combine them with `TreeRules.merge((NODE_RULES, DATA_RULES))` for graph inspection. These immutable definitions are not included in `__all__` or re-exported at package level. `_install` and Schema's declaration rules remain private implementation details.

## Context-owned defaults

Root `Context()` installs three register-mode fields from `slyme.context.default`. Each contains its own Compose:

| Ref constant | Path | Contributions |
| --- | --- | --- |
| `DATA_TREE_REF` | `$.tree.data` | `TreeRules` for Auto, `extract`, and `update_tree` |
| `NODE_TREE_REF` | `$.tree.node` | `TreeRules` for explicit Node graph inspection |
| `EVALUATORS_REF` | `$.eval.handlers` | Exact-type evaluator mappings |

These constants are also exported by `slyme.context`. Data rules expand list, tuple, dict, and MappingProxyType. Node rules additionally traverse Node and Wrapper bindings and Auto payloads. Node, Wrapper, and Auto are traversal-only containers, with no reconstruction function.

Each operation resolves its rule and evaluator mappings once, before traversal or asynchronous suspension. Contributions added or removed afterward affect subsequent operations, not that operation's dispatch or reconstruction. Capturing a callable does not extend the lifetime of resources managed by its owner.

Compose resolves contributions in Scope C3 order, then local insertion order; the first handler for a type wins. Use `position="prepend"` to override an earlier contribution in the same Scope. Dispose a contribution to reveal the next applicable definition:

```python
from dataclasses import dataclass

from slyme.context import DATA_TREE_REF, Context
from slyme.utils.tree import TreeAux, TreeEngine, TreeHandler, TreeRules

@dataclass
class Box:
    value: object

ctx = Context()
plugin = ctx.fork()
rules = TreeRules({
    Box: TreeHandler(
        lambda box: ((box.value,), TreeAux()),
        lambda items, _: Box(next(iter(items))),
    ),
})
plugin.effect(lambda: ctx.get(DATA_TREE_REF).add(plugin.scope, rules))

effective = ctx.get(DATA_TREE_REF).resolve(ctx.scope)
leaves, definition = TreeEngine.flatten(Box(1), rules=effective)
assert leaves == [1]
assert TreeEngine.unflatten(definition, [2]) == Box(2)

plugin.dispose()
assert Box not in ctx.get(DATA_TREE_REF).resolve(ctx.scope).handlers
ctx.dispose()
```

## Scope isolation

Ordinary forks reuse their parent's configuration. A child Scope inherits it through C3. A fork bound to an unrelated `Scope()` sees no default values: install the required Compose objects and contributions explicitly. There is no fallback to `ctx.root`. A separate root `Context()` installs independent defaults.

Create `child = ctx.derive(bindings={DATA_TREE_REF: ScopeBinding(blocked=True)})`, then call `child.register(DATA_TREE_REF, Compose(TreeRules.merge))` to install an independent rule composition for that field. An empty composition produces empty rules, not implicit defaults. Node assembly remains Context-independent; execution uses the supplied Context. Graph inspection explicitly resolves `NODE_TREE_REF` and passes those rules to TreeEngine.

Schema declaration uses private, immutable dict-only rules and does not read runtime configuration. Configuring a data tree cannot change how Schema interprets declarations.

## Identity and evaluation

Tree traversal treats each occurrence independently. Reconstruction does not preserve shared container aliases and does not support cycles. Ordinary leaves and evaluator results retain their identities. Context is opaque to TreeEngine; `Context.flatten()` exposes its visible Ref-to-value mapping, including `$` when visible.

Auto uses data rules to find leaves. Ref and Node evaluators match exact types; subclasses need separate contributions to `EVALUATORS_REF`. A Ref reads the current Context. Each child Node runs in an owned child Context with a distinct child Scope, which is disposed before the parent receives its result. All traversed containers are reconstructed, even without evaluatable leaves; evaluator results are not traversed again.
