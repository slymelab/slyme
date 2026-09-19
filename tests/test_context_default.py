from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from slyme.context import (
    DATA_TREE_REF,
    EVALUATORS_REF,
    NODE_TREE_REF,
    Compose,
    Context,
    Schema,
    Scope,
)
from slyme.context.core import ContextPathError
from slyme.context.default import DATA_RULES
from slyme.node import Auto, node
from slyme.node.core import NODE_RULES
from slyme.node.eval import eval_tree
from slyme.utils.execution import await_result
from slyme.utils.tree import AttributeKey, TreeAux, TreeEngine, TreeHandler, TreeRules


@dataclass
class Box:
    value: object


BOX_HANDLER = TreeHandler(
    lambda box: ((box.value,), TreeAux()),
    lambda items, _: Box(next(iter(items))),
)


def test_rule_definitions_support_inspection_without_a_context() -> None:
    @node
    def echo(ctx, *, value):
        return value

    graph = echo(value=42)
    rules = TreeRules.merge((NODE_RULES, DATA_RULES))
    assert list(TreeEngine.iter([graph], rules=rules)) == [42]
    assert list(TreeEngine.iter([graph], rules=DATA_RULES)) == [graph]


def test_defaults_are_root_owned_and_forks_do_not_install_again() -> None:
    left, right = Context(), Context()
    baseline = tuple(left._lifecycle._owned)
    child = left.fork()
    for ref in (DATA_TREE_REF, NODE_TREE_REF, EVALUATORS_REF):
        assert child.get(ref) is left.get(ref)
        assert left.get(ref) is not right.get(ref)
    assert not child._lifecycle._owned
    assert tuple(left._lifecycle._owned) == (*baseline, child._lifecycle)
    data = left.get(DATA_TREE_REF)
    child.effect(lambda: data.add(child.scope, TreeRules({Box: BOX_HANDLER})))
    assert Box in data.resolve(left.scope).handlers
    assert Box not in right.get(DATA_TREE_REF).resolve(right.scope).handlers
    child.dispose()
    assert Box not in data.resolve(left.scope).handlers
    retained = [left.get(ref) for ref in (DATA_TREE_REF, NODE_TREE_REF, EVALUATORS_REF)]
    left.dispose()
    assert all(not compose.entries(left.scope) for compose in retained)
    assert not left._store._data
    assert all(not usage.viewers for usage in left._store._scope_usages.values())
    right.dispose()


def test_unrelated_scope_requires_explicit_configuration_without_root_fallback() -> (
    None
):
    root = Context()
    detached = root.fork(scope=Scope())
    detached.declare({"value": Schema.leaf()})
    detached.set("value", 7)
    assert detached.get("value") == 7
    assert detached.keys() == ("value",)
    with pytest.raises(ContextPathError):
        detached.get(DATA_TREE_REF)
    with pytest.raises(ContextPathError):
        detached.extract(["value"])
    with pytest.raises(ContextPathError):
        eval_tree(detached, [])

    rules = Compose(TreeRules.merge)
    detached.register(DATA_TREE_REF, rules)
    detached.effect(
        lambda: rules.add(detached.scope, root.get(DATA_TREE_REF).resolve(root.scope))
    )
    assert detached.extract(["value"]) == [7]
    with pytest.raises(ContextPathError):
        eval_tree(detached, [])
    evaluators = Compose.merge()
    detached.register(EVALUATORS_REF, evaluators)
    detached.effect(
        lambda: evaluators.add(
            detached.scope, root.get(EVALUATORS_REF).resolve(root.scope)
        )
    )
    assert eval_tree(detached, [root.resolve("value")]) == [7]
    root.dispose()


def test_rule_composition_uses_c3_then_contribution_order() -> None:
    root = Context()
    left = root.fork(scope=root.scope.fork())
    right = root.fork(scope=root.scope.fork())
    joined = root.fork(scope=Scope(parents=(left.scope, right.scope)))
    first = TreeHandler(lambda box: (("first",), TreeAux()), BOX_HANDLER.unflatten)
    second = TreeHandler(lambda box: (("second",), TreeAux()), BOX_HANDLER.unflatten)
    rules = root.get(DATA_TREE_REF)
    remove_left = left.effect(lambda: rules.add(left.scope, TreeRules({Box: first})))
    right.effect(lambda: rules.add(right.scope, TreeRules({Box: second})))
    assert rules.resolve(joined.scope).handlers[Box] is first
    remove_left()
    assert rules.resolve(joined.scope).handlers[Box] is second
    right.effect(lambda: rules.add(right.scope, TreeRules({Box: first})))
    assert rules.resolve(joined.scope).handlers[Box] is second
    prepend = right.effect(
        lambda: rules.add(right.scope, TreeRules({Box: first}), position="prepend")
    )
    assert rules.resolve(joined.scope).handlers[Box] is first
    prepend()
    assert rules.resolve(joined.scope).handlers[Box] is second
    root.dispose()


def test_isolated_tree_rules_do_not_affect_schema_or_parent_rules() -> None:
    root = Context()
    isolated = root.fork(scope=root.scope.fork())
    isolated.set_blocked(DATA_TREE_REF, blocked=True)
    isolated.register(DATA_TREE_REF, Compose(TreeRules.merge))
    isolated.declare({"group": {"value": Schema.leaf()}})
    isolated.set("group.value", 3)
    tree = [isolated.resolve("group.value")]
    assert eval_tree(isolated, tree) is tree
    assert root.get(DATA_TREE_REF).resolve(root.scope).handlers[list]
    assert isolated.extract("group.value") == 3
    root.dispose()


def test_tree_rules_snapshot_handlers_and_preserve_reconstruction() -> None:
    source = {Box: BOX_HANDLER}
    rules = TreeRules(source)
    source.clear()
    leaves, definition = TreeEngine.flatten(Box(1), rules=rules)
    assert leaves == [1]
    assert TreeEngine.unflatten(definition, [2]) == Box(2)
    with pytest.raises(TypeError):
        rules.handlers[Box] = BOX_HANDLER
    empty = TreeRules()
    box = Box(1)
    assert list(TreeEngine.iter(box, rules=empty)) == [box]


def test_resolver_phases_keep_their_composition_order() -> None:
    seen = []

    def pre_one(value, aux):
        seen.append("pre-one")

    def pre_two(value, aux):
        seen.append("pre-two")

    def post_one(value, aux):
        seen.append("post-one")

    def post_two(value, aux):
        seen.append("post-two")
        return BOX_HANDLER if type(value) is Box else None

    rules = TreeRules.merge(
        (
            TreeRules(pre_resolvers=(pre_one,), post_resolvers=(post_one,)),
            TreeRules(pre_resolvers=(pre_two,), post_resolvers=(post_two,)),
        )
    )
    assert list(TreeEngine.iter(Box(1), rules=rules)) == [1]
    assert seen == ["pre-one", "pre-two", "post-one", "post-two"] * 2


def test_evaluation_snapshots_evaluators_before_container_callbacks() -> None:
    ctx = Context()
    evaluators = ctx.get(EVALUATORS_REF)
    remove = ctx.effect(
        lambda: evaluators.add(ctx.scope, {int: lambda ctx, values: [10] * len(values)})
    )

    def flatten(box):
        remove()
        return (box.value,), TreeAux()

    ctx.effect(
        lambda: ctx.get(DATA_TREE_REF).add(
            ctx.scope, TreeRules({Box: TreeHandler(flatten, BOX_HANDLER.unflatten)})
        )
    )
    assert eval_tree(ctx, Box(1)) == Box(10)
    assert eval_tree(ctx, Box(1)) == Box(1)
    ctx.dispose()


async def test_inflight_evaluation_uses_captured_rules_until_reconstruction() -> None:
    ctx = Context()
    entered, finish = asyncio.Event(), asyncio.Event()

    async def evaluate(ctx, values):
        entered.set()
        await finish.wait()
        return [value + 1 for value in values]

    remove_rules = ctx.effect(
        lambda: ctx.get(DATA_TREE_REF).add(ctx.scope, TreeRules({Box: BOX_HANDLER}))
    )
    remove_evaluator = ctx.effect(
        lambda: ctx.get(EVALUATORS_REF).add(ctx.scope, {int: evaluate})
    )
    pending = asyncio.create_task(await_result(eval_tree(ctx, Box(1))))
    await entered.wait()
    remove_rules()
    remove_evaluator()
    finish.set()
    assert await pending == Box(2)
    tree = Box(1)
    assert eval_tree(ctx, tree) is tree
    await ctx.adispose()


def test_node_assembly_stays_independent_of_the_execution_context() -> None:
    @node
    def consume(ctx, *, value):
        return value

    graph = consume(value=Auto(Box(3)))
    left, right = Context(), Context()
    left.effect(
        lambda: left.get(DATA_TREE_REF).add(left.scope, TreeRules({Box: BOX_HANDLER}))
    )
    left.effect(
        lambda: left.get(EVALUATORS_REF).add(
            left.scope, {int: lambda ctx, values: [value * 2 for value in values]}
        )
    )
    assert graph(left) == Box(6)
    assert graph(right) == Box(3)
    rules = left.get(NODE_TREE_REF).resolve(left.scope)
    assert list(TreeEngine.iter(graph, rules=rules)) == [graph.get("value").value]
    left.dispose()
    right.dispose()


def test_context_tree_operations_use_the_same_scoped_rules() -> None:
    root = Context()
    root.declare({"group": {"value": Schema.leaf()}})
    plugin = root.fork()
    handler = TreeHandler(
        lambda box: ((box.value,), TreeAux(children_keys=(AttributeKey("value"),))),
        BOX_HANDLER.unflatten,
    )
    plugin.effect(
        lambda: root.get(DATA_TREE_REF).add(plugin.scope, TreeRules({Box: handler}))
    )
    root.update_tree(Box("group.value"), Box(5))
    assert root.extract(Box("group.value")) == Box(5)
    assert root.extract(Box(root.resolve("group.value")), local=True) == Box(5)
    plugin.dispose()
    assert Box not in root.get(DATA_TREE_REF).resolve(root.scope).handlers
    assert root.get("group.value") == 5
    root.dispose()


def test_failed_default_installation_releases_partial_root(monkeypatch) -> None:
    from slyme.context import core

    install = core._install
    constructed = []

    def fail(ctx):
        constructed.append(ctx)
        install(ctx)
        raise ValueError("installation failed")

    monkeypatch.setattr(core, "_install", fail)
    with pytest.raises(ValueError, match="installation failed"):
        Context()
    ctx = constructed[0]
    assert not ctx._schema._stores
    assert not ctx._store._data
    assert all(not usage.viewers for usage in ctx._store._scope_usages.values())
    assert not ctx._lifecycle._owned
