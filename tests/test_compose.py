from __future__ import annotations

import gc
import weakref
from types import MappingProxyType

import pytest

import slyme.context as context_module
from slyme.context import (
    Compose,
    ComposeLayer,
    Context,
    Identity,
    Schema,
    Scope,
    ScopeBinding,
)
from tests.compose_helpers import ValueLayer, collect_values, first_value, merge_values

R = Schema({"hooks": Schema.leaf(), "tools": Schema.leaf()})


def test_composition_exports_its_layer_protocol() -> None:
    assert context_module.Compose is Compose
    assert context_module.ComposeLayer is ComposeLayer
    assert not hasattr(context_module, "Value")


def test_first_value_query_uses_scope_and_registration_precedence() -> None:
    root = Scope(label="root")
    agent = root.fork(label="agent")
    values = Compose(factory=ValueLayer, query=first_value)
    remove_root = values.register(root, "root")
    remove_agent = values.register(agent, "agent")
    remove_later = values.register(agent, "later")

    assert values.resolve(root) == "root"
    assert values.resolve(agent) == "agent"
    override_scope = agent.fork()
    remove_override = values.register(override_scope, "override")
    assert values.resolve(override_scope) == "override"
    remove_override()
    assert values.resolve(override_scope) == "agent"
    remove_agent()
    assert values.resolve(agent) == "later"
    remove_later()
    assert values.resolve(agent) == "root"
    remove_root()
    with pytest.raises(LookupError, match="no value"):
        values.resolve(agent)


def test_query_follows_c3_without_repeating_diamond_ancestors() -> None:
    root = Scope(label="root")
    left = root.fork(label="left")
    right = root.fork(label="right")
    child = Scope(label="child", parents=(left, right))
    values = Compose(factory=ValueLayer, query=collect_values)
    values.register(root, "root")
    values.register(left, "left")
    values.register(right, "right")
    values.register(child, "child-first")
    values.register(child, "child-last")

    assert values.resolve(child) == (
        "child-first",
        "child-last",
        "left",
        "right",
        "root",
    )
    assert values.resolve(child, local=True) == ("child-first", "child-last")
    layers = tuple(values.layers(child))
    assert tuple(tuple(layer.values()) for layer in layers) == (
        ("child-first", "child-last"),
        ("left",),
        ("right",),
        ("root",),
    )
    assert tuple(values.layers(child, local=True)) == layers[:1]


def test_mapping_query_preserves_registrations_and_uses_first_visible_key() -> None:
    root = Scope(label="root")
    child = root.fork(label="child")
    values = Compose(factory=ValueLayer, query=merge_values)
    remove_root = values.register(root, {"shared": "root", "root": 1})
    remove_child = values.register(child, {"shared": "child", "child": 2})
    remove_later = values.register(child, {"shared": "later"})

    assert values.resolve(child) == {"shared": "child", "child": 2, "root": 1}
    remove_child()
    assert values.resolve(child)["shared"] == "later"
    remove_later()
    assert values.resolve(child) == {"shared": "root", "root": 1}
    remove_root()
    assert values.resolve(child) == {}


def test_compose_uses_default_query_and_per_call_override() -> None:
    root = Scope(label="root")
    child = root.fork(label="child")
    values = Compose(
        factory=ValueLayer,
        query=lambda layers: sum(value for layer in layers for value in layer.values()),
    )
    values.register(root, 2)
    values.register(child, 3)

    assert values.resolve(child) == 5
    assert values.resolve(child, local=True) == 3
    assert values.resolve(
        child, lambda layers: [list(layer.values()) for layer in layers]
    ) == [[3], [2]]
    assert values.resolve(child) == 5


def test_registration_preserves_payload_and_assigns_independent_ids() -> None:
    scope = Scope()
    values = Compose(factory=ValueLayer, query=collect_values)
    record = {"value": "same", "metadata": MappingProxyType({"plugin": "example"})}
    remove_first = values.register(scope, record)
    remove_second = values.register(scope, record)
    layer = next(iter(values.layers(scope)))
    first_id, second_id = layer
    assert first_id is not second_id
    assert layer[first_id] is layer[second_id] is record
    snapshot = values.resolve(scope)

    remove_second()
    remove_second()
    assert tuple(layer) == (first_id,)
    assert values.resolve(scope) == (record,)
    remove_first()
    assert len(values) == 0
    assert snapshot == (record, record)
    assert not layer
    assert not tuple(values.layers())


def test_derive_shares_identity_only_in_that_compose() -> None:
    identity = Identity()
    shared = Compose(factory=ValueLayer, query=collect_values)
    independent = Compose(factory=ValueLayer, query=collect_values)

    left = shared.derive(
        parents=Scope(label="left"), binding=ScopeBinding(identity, blocked=True)
    )
    right = shared.derive(
        parents=Scope(label="right"), binding=ScopeBinding(identity, blocked=True)
    )
    shared.register(left, "left")
    shared.register(right, "right")
    independent.register(left, "independent")

    assert shared.resolve(left) == ("left", "right")
    assert shared.resolve(right, local=True) == ("left", "right")
    assert independent.resolve(right) == ()
    assert tuple(shared.layers(left)) == tuple(shared.layers(right))
    assert set(shared._buckets) == {identity}


def test_derive_creates_fresh_scopes_without_changing_parent_binding() -> None:
    parent = Scope()
    values = Compose(factory=ValueLayer, query=collect_values)
    values.register(parent, "parent")
    original = values._scope_bindings[parent]

    first = values.derive(parents=parent, binding=ScopeBinding())
    second = values.derive(parents=parent, binding=ScopeBinding())
    assert first is not second
    assert first.parents == second.parents == (parent,)
    assert values._scope_bindings[parent] is original
    assert values.resolve(parent) == ("parent",)
    assert values.resolve(first) == values.resolve(second) == ("parent",)

    remove = values.register(first, "private")
    assert values.resolve(first) == ("private", "parent")
    assert values.resolve(second) == ("parent",)
    assert values.resolve(parent) == ("parent",)
    remove()
    assert values.resolve(first) == ("parent",)


@pytest.mark.parametrize("single", [False, True])
def test_derive_many_configures_all_composes_on_one_scope(single: bool) -> None:
    parent = Scope(label="parent")
    tools, events, unconfigured = (
        Compose(factory=ValueLayer, query=collect_values),
        Compose(factory=ValueLayer, query=collect_values),
        Compose(factory=ValueLayer, query=collect_values),
    )
    tools.register(parent, "parent tool")
    events.register(parent, "parent event")
    unconfigured.register(parent, "inherited")
    identity = Identity()
    peer = events.derive(parents=(), binding=ScopeBinding(identity, blocked=True))
    events.register(peer, "shared event")
    scope = Compose.derive_many(
        label="derived",
        parents=parent if single else (parent,),
        bindings={
            tools: ScopeBinding(blocked=True),
            events: ScopeBinding(identity, blocked=True),
        },
    )
    assert scope.label == "derived"
    assert scope.parents == (parent,)
    assert scope.mro == (scope, parent)
    assert tools.resolve(scope) == ()
    assert events.resolve(scope) == ("shared event",)
    assert unconfigured.resolve(scope) == ("inherited",)
    assert tools._scope_bindings[scope].identity is not identity
    remove = tools.register(scope, "private tool")
    assert tools.resolve(scope) == ("private tool",)
    assert tools.resolve(parent) == ("parent tool",)
    remove()
    assert tools.resolve(scope) == ()


def test_derive_many_handles_c3_and_empty_configuration() -> None:
    root = Scope()
    left, right = root.fork(), root.fork()
    values = Compose(factory=ValueLayer, query=collect_values)
    values.register(left, "left")
    values.register(right, "right")
    config = ScopeBinding(blocked=False)
    scope = Compose.derive_many(parents=(left, right), bindings={values: config})
    assert scope.mro == (scope, left, right, root)
    assert values._scope_bindings[scope] is config
    assert values.resolve(scope) == ("left", "right")
    empty = Compose.derive_many(parents=(), bindings={})
    assert empty.parents == ()
    assert empty.mro == (empty,)
    assert values.resolve(empty) == ()


def test_derive_many_rejects_inconsistent_parents_without_installing_bindings() -> None:
    values = Compose(factory=ValueLayer, query=collect_values)
    left, right = Scope(), Scope()
    xy, yx = Scope(parents=(left, right)), Scope(parents=(right, left))
    with pytest.raises(TypeError, match="consistent Scope C3"):
        Compose.derive_many(
            parents=(xy, yx), bindings={values: ScopeBinding(blocked=True)}
        )
    assert not values._scope_bindings
    assert not tuple(values.layers())


def test_internal_binding_is_idempotent_and_rejects_rebinding() -> None:
    left = Scope(label="left")
    right = Scope(label="right")
    values = Compose(factory=ValueLayer, query=collect_values)
    first = Identity()
    second = Identity()

    values._bind(left, ScopeBinding(first, blocked=False))
    values._bind(left, ScopeBinding(first, blocked=False))
    with pytest.raises(ValueError, match="immutable"):
        values._bind(left, ScopeBinding(second, blocked=False))

    values._bind(right, ScopeBinding(first, blocked=False))
    values.register(right, "shared")
    assert values.resolve(left) == ("shared",)


def test_read_does_not_bind_and_first_write_prevents_later_rebinding() -> None:
    scope = Scope()
    values = Compose(factory=ValueLayer, query=collect_values)
    identity = Identity()

    assert values.resolve(scope) == ()
    values._bind(scope, ScopeBinding(identity, blocked=False))
    remove = values.register(scope, "value")
    remove()

    assert values.resolve(scope) == ()
    with pytest.raises(ValueError, match="immutable"):
        values._bind(scope, ScopeBinding(Identity(), blocked=False))
    values.register(scope, "new")
    assert values._scope_bindings[scope].identity is identity

    implicit = Scope()
    values.register(implicit, "implicit")
    with pytest.raises(ValueError, match="immutable"):
        values._bind(implicit, ScopeBinding(identity, blocked=False))


def test_empty_binding_does_not_retain_an_unreferenced_scope() -> None:
    values = Compose(factory=ValueLayer, query=collect_values)
    scope = values.derive(parents=Scope(), binding=ScopeBinding())
    scope_ref = weakref.ref(scope)

    del scope
    gc.collect()

    assert scope_ref() is None
    assert not values._scope_bindings


def test_c3_lookup_visits_a_shared_identity_only_once() -> None:
    root = Scope(label="root")
    identity = Identity()
    values = Compose(factory=ValueLayer, query=collect_values)

    left = values.derive(parents=root, binding=ScopeBinding(identity, blocked=False))
    right = values.derive(parents=root, binding=ScopeBinding(identity, blocked=False))
    child = Scope(label="child", parents=(left, right))
    values.register(left, "left")
    values.register(right, "right")
    values.register(root, "root")

    assert values.resolve(child) == ("left", "right", "root")


@pytest.mark.parametrize("label", [None, "", "shared"])
def test_sparse_c3_reads_see_new_bindings_without_allocating_identities(
    label: str | None,
) -> None:
    identity = Identity(label)
    root = Scope()
    left = root.fork()
    right = root.fork()
    unbound = Scope(parents=(left, right))
    child = unbound.fork()
    values = Compose(factory=ValueLayer, query=collect_values)

    assert values.resolve(child) == ()
    values._bind(left, ScopeBinding(identity, blocked=False))
    values._bind(right, ScopeBinding(identity, blocked=False))
    remove = values.register(left, "shared")
    assert values.resolve(child) == ("shared",)
    values.register(root, "root")
    identities_before = dict(values._scope_bindings)
    assert values.resolve(child) == ("shared", "root")
    assert values.resolve(child, local=True) == ()
    assert dict(values._scope_bindings) == identities_before

    remove()
    assert values.resolve(child) == ("root",)
    values.register(right, "new")
    assert values.resolve(child) == ("new", "root")


def test_compose_retains_scope_and_value_until_exact_disposal() -> None:
    class Value:
        pass

    values = Compose(factory=ValueLayer, query=collect_values)
    scope = Scope()
    value = Value()
    scope_ref = weakref.ref(scope)
    value_ref = weakref.ref(value)
    dispose = values.register(scope, value)

    del scope
    del value
    gc.collect()

    assert scope_ref() is not None
    assert value_ref() is not None
    assert len(values) == 1

    dispose()
    gc.collect()

    assert scope_ref() is None
    assert value_ref() is None
    assert len(values) == 0
    dispose()


def test_compose_disposer_does_not_retain_an_already_removed_value() -> None:
    class Value:
        pass

    values = Compose(factory=ValueLayer, query=collect_values)
    scope = Scope()
    value = Value()
    value_ref = weakref.ref(value)
    dispose = values.register(scope, value)

    dispose()
    del value
    gc.collect()

    assert value_ref() is None
    dispose()


def test_bucket_is_removed_after_the_last_shared_identity_entry_leaves() -> None:
    values = Compose(factory=ValueLayer, query=collect_values)
    identity = Identity()
    left = values.derive(parents=Scope(), binding=ScopeBinding(identity, blocked=True))
    right = values.derive(parents=Scope(), binding=ScopeBinding(identity, blocked=True))
    remove_left = values.register(left, "left")
    bucket = values._buckets[identity]
    remove_right = values.register(right, "right")
    assert values._buckets[identity] is bucket
    assert values.resolve(left) == ("left", "right")

    remove_left()
    assert values._buckets[identity] is bucket
    assert len(bucket.registrations) == 1
    remove_right()
    assert not bucket.registrations
    assert not bucket.data
    assert not values._buckets

    remove_new = values.register(left, "new")
    assert values._buckets[identity] is not bucket
    remove_left()
    remove_right()
    assert values.resolve(right) == ("new",)
    remove_new()
    assert not values._buckets


def test_compose_disposer_owns_compose_until_release() -> None:
    values = Compose(factory=ValueLayer, query=collect_values)
    compose_ref = weakref.ref(values)
    remove = values.register(Scope(), "value")
    del values
    gc.collect()
    assert compose_ref() is not None
    remove()
    gc.collect()
    assert compose_ref() is None
    remove()


def test_layer_disposer_owns_cleanup_state_and_releases_it_after_disposal() -> None:
    calls = []

    class Value:
        pass

    class Layer:
        def register(self, token, /, value):
            def dispose():
                assert value is value_ref()
                assert scope_ref() is not None
                assert not values._buckets
                calls.append(token)

            return dispose

    values = Compose(factory=Layer)
    scope, value = Scope(), Value()
    scope_ref, value_ref = weakref.ref(scope), weakref.ref(value)
    dispose = values.register(scope, value)
    layer_ref = weakref.ref(next(iter(values.layers())))
    del scope, value
    gc.collect()
    assert scope_ref() is not None
    assert value_ref() is not None

    dispose()
    dispose()
    gc.collect()
    assert len(calls) == 1
    assert scope_ref() is None
    assert value_ref() is None
    assert layer_ref() is None


@pytest.mark.parametrize("keep_other_entry", [False, True])
def test_value_finalizer_can_add_to_the_same_identity(keep_other_entry: bool) -> None:
    values = Compose(factory=ValueLayer, query=collect_values)
    identity = Identity()
    scope = values.derive(parents=Scope(), binding=ScopeBinding(identity, blocked=True))
    remove_new = []

    class Value:
        def __del__(self):
            remove_new.append(values.register(scope, "new"))

    remove = values.register(scope, Value())
    bucket = values._buckets[identity]
    if keep_other_entry:
        remove_other = values.register(scope, "other")
    remove()
    assert len(remove_new) == 1
    if keep_other_entry:
        assert values._buckets[identity] is bucket
        assert values.resolve(scope) == ("other", "new")
        remove_other()
    else:
        assert not bucket.registrations
        assert not bucket.data
        assert values._buckets[identity] is not bucket
    assert values.resolve(scope) == ("new",)
    remove_new[0]()
    assert not values._buckets


def test_bucket_cleanup_failure_is_replayed_without_removing_a_new_bucket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = Compose(factory=ValueLayer, query=collect_values)
    scope = Scope()
    remove = values.register(scope, "old")
    original = Compose._remove
    error = ValueError("cleanup failed")
    calls = []

    def fail(self, identity, token, cleanup):
        calls.append("cleanup")
        original(self, identity, token, cleanup)
        raise error

    with monkeypatch.context() as patch:
        patch.setattr(Compose, "_remove", fail)
        with pytest.raises(ValueError) as raised:
            remove()
    assert raised.value is error
    assert not values._buckets

    remove_new = values.register(scope, "new")
    with pytest.raises(ValueError) as repeated:
        remove()
    assert repeated.value is error
    assert calls == ["cleanup"]
    assert values.resolve(scope) == ("new",)
    remove_new()


def test_context_identity_cleanup_clears_value_and_preserves_new_tokens() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf(mode="register")}))
    identity = Identity(blocked=True)
    child = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    binding = root._store._data[root.resolve_entry("value")]
    remove = root._store.register(child.scope, root.resolve_entry("value"), "old")
    assert binding._scope_bindings[child.scope].identity.blocked

    child.dispose()
    assert not binding._data
    replacement = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    replacement.register("value", "new")
    remove()
    assert replacement.get("value") == "new"
    root.dispose()
    assert not binding._data


def test_all_values_are_explicit_and_scoped_reads_do_not_leak() -> None:
    left = Scope(label="left")
    right = Scope(label="right")
    combined = Scope(label="combined", parents=(left, right))
    values = Compose(factory=ValueLayer, query=collect_values)
    values.register(left, "left")
    values.register(right, "right")

    assert values.resolve(left) == ("left",)
    assert values.resolve(right) == ("right",)
    assert values.resolve(combined) == ("left", "right")
    assert {value for layer in values.layers() for value in layer.values()} == {
        "left",
        "right",
    }
    with pytest.raises(ValueError, match="requires a Scope"):
        tuple(values.layers(local=True))


def test_context_fork_shares_scope_unless_one_is_explicit() -> None:
    root = Context()
    root.declare(R)
    shared = root.fork()
    child_scope = root.scope.fork(label="child")
    isolated = root.fork(scope=child_scope)

    assert shared.scope is root.scope
    assert isolated.scope is child_scope
    assert isolated.parent is root


def test_context_data_and_scope_identity_are_orthogonal() -> None:
    ref = R.resolve("tools")
    left = Scope(label="left")
    right = Scope(label="right")
    combined = Scope(label="combined", parents=(left, right))
    root = Context(scope=left)
    root.declare(R)
    right_context = root.fork(scope=right)
    combined_context = root.fork(scope=combined)

    root.set(ref, "left")
    right_context.set(ref, "right")

    assert root.get(ref) == "left"
    assert right_context.get(ref) == "right"
    assert combined_context.get(ref) == "left"
    root.delete(ref)
    assert combined_context.get(ref) == "right"


def test_independent_context_roots_do_not_share_data_with_the_same_scope() -> None:
    ref = R.resolve("tools")
    scope = Scope(label="shared-identity")
    left = Context(scope=scope)
    left.declare(R)
    right = Context(scope=scope)
    right.declare(R)

    left.set(ref, "left")

    assert left.get(ref) == "left"
    assert not right.exists(ref)


def test_context_can_shadow_a_compose_as_an_ordinary_leaf() -> None:
    tools_ref = R.resolve("tools")
    root = Context()
    root.declare(Schema({"tools": Schema.leaf(mode="register")}))
    inherited = Compose(factory=ValueLayer, query=collect_values)
    root.register(tools_ref, inherited)
    child = root.fork(scope=root.scope.fork(label="child"))

    inherited.register(root.scope, "root-tool")
    inherited.register(child.scope, "agent-tool")
    assert child.get(tools_ref) is inherited
    assert inherited.resolve(child.scope) == ("agent-tool", "root-tool")

    isolated = Compose(factory=ValueLayer, query=collect_values)
    child.register(tools_ref, isolated)
    isolated.register(child.scope, "isolated-tool")
    assert child.get(tools_ref) is isolated
    assert isolated.resolve(child.scope) == ("isolated-tool",)


def test_context_effect_owns_contributions_to_explicit_scopes() -> None:
    hooks_ref = R.resolve("hooks")
    root = Context()
    root.declare(Schema({"hooks": Schema.leaf(mode="register")}))
    hooks = Compose(factory=ValueLayer, query=collect_values)
    root.register(hooks_ref, hooks)
    child = root.fork(scope=root.scope.fork(label="child"))
    external = Scope(label="external")

    remove_bound = child.effect(
        lambda: child.get(hooks_ref).register(
            child.scope,
            "bound",
        )
    )
    child.effect(lambda: child.get(hooks_ref).register(external, "external"))

    assert hooks.resolve(child.scope) == ("bound",)
    assert hooks.resolve(external) == ("external",)

    remove_bound()
    remove_bound()
    assert hooks.resolve(child.scope) == ()
    child.dispose()
    assert hooks.resolve(external) == ()
    root.dispose()


def test_flattened_context_shares_compose_but_not_scope_identity() -> None:
    ref = R.resolve("hooks")
    ctx = Context()
    ctx.declare(R)
    hooks = Compose(factory=ValueLayer, query=collect_values)
    ctx.set(ref, hooks)
    hooks.register(ctx.scope, "handler")

    snapshot = Context()
    snapshot.declare(R)
    snapshot.update({ref: ctx.get(ref)})
    assert snapshot.get(ref) is hooks
    assert hooks.resolve(ctx.scope) == ("handler",)
    assert hooks.resolve(snapshot.scope) == ()


def test_query_is_optional_until_resolve_and_override_does_not_set_a_default() -> None:
    scope = Scope()
    values = Compose(factory=ValueLayer)
    with pytest.raises(ValueError, match="requires a query"):
        values.resolve(scope)
    assert not tuple(values.layers(scope))
    assert not values._scope_bindings
    remove = values.register(scope, None)
    assert values.resolve(scope, lambda layers: next(iter(layers))) == next(
        iter(values.layers(scope))
    )
    with pytest.raises(ValueError, match="requires a query"):
        values.resolve(scope)
    remove()


def test_visible_containers_are_live_but_collect_is_a_snapshot() -> None:
    scope = Scope()
    values = Compose(factory=ValueLayer, query=collect_values)
    remove_first = values.register(scope, "first")
    layer = next(iter(values.layers(scope)))
    snapshot = values.resolve(scope)
    remove_second = values.register(scope, "second")
    assert next(iter(values.layers(scope))) is layer
    assert tuple(layer.values()) == ("first", "second")
    assert snapshot == ("first",)
    remove_first()
    remove_second()
    assert not layer
    assert not tuple(values.layers())


def test_query_can_stop_before_reading_parent_bindings() -> None:
    parent = Scope()
    child = parent.fork()
    values = Compose(factory=ValueLayer, query=first_value)
    values.register(parent, "parent")
    values.register(child, "child")

    class Bindings(weakref.WeakKeyDictionary):
        def get(self, scope, default=None):
            if scope is parent:
                pytest.fail("A short-circuit query must not visit later Scopes.")
            return super().get(scope, default)

    values._scope_bindings = Bindings(values._scope_bindings)
    assert values.resolve(child) == "child"


def test_factory_is_lazy_and_registration_failure_does_not_leave_a_bucket() -> None:
    created = []

    class CheckedLayer(ValueLayer):
        def register(self, token, /, value):
            if value == "conflict":
                raise ValueError("conflicting registration")
            return super().register(token, value)

    def factory():
        data = CheckedLayer()
        created.append(data)
        return data

    values = Compose(factory=factory)
    scope = Scope()
    assert tuple(values.layers(scope)) == ()
    assert created == []
    with pytest.raises(ValueError, match="conflicting"):
        values.register(scope, "conflict")
    assert len(created) == 1
    assert not values._buckets
    remove = values.register(scope, "accepted")
    with pytest.raises(ValueError, match="conflicting"):
        values.register(scope, "conflict")
    assert len(created) == 2
    assert tuple(next(iter(values.layers(scope))).values()) == ("accepted",)
    assert len(values) == 1
    remove()
    assert not values._buckets


def test_factory_failure_does_not_publish_data() -> None:
    def factory():
        raise ValueError("factory failed")

    values = Compose(factory=factory)
    with pytest.raises(ValueError, match="factory failed"):
        values.register(Scope(), 1)
    assert not values._buckets
    assert len(values) == 0


def test_custom_container_can_aggregate_zero_while_registrations_remain_live() -> None:
    class Total:
        def __init__(self):
            self.amount = 0
            self.contributions = {}

        def __bool__(self):
            pytest.fail("Container truthiness must not decide its lifetime.")

        def register(self, token, /, amount):
            self.contributions[token] = amount
            self.amount += amount

            def dispose():
                self.amount -= self.contributions.pop(token)

            return dispose

    values = Compose(
        factory=Total,
        query=lambda layers: sum(layer.amount for layer in layers),
    )
    scope = Scope()
    remove_positive = values.register(scope, 3)
    remove_negative = values.register(scope, -3)
    layer = next(iter(values.layers(scope)))
    assert values.resolve(scope) == 0
    assert len(values) == 2
    remove_positive()
    assert values.resolve(scope) == -3
    assert next(iter(values.layers(scope))) is layer
    remove_negative()
    assert not values._buckets
    assert len(values) == 0


def test_custom_storage_controls_order_and_deletes_exact_duplicates() -> None:
    class OrderedLayer(list):
        def register(self, token, /, value, *, position="append"):
            if position == "prepend":
                self.insert(0, (token, value))
            else:
                self.append((token, value))

            def dispose():
                self[:] = [entry for entry in self if entry[0] is not token]

            return dispose

    values = Compose(
        factory=OrderedLayer,
        query=lambda layers: tuple(value for layer in layers for _, value in layer),
    )
    scope = Scope()
    remove_first = values.register(scope, "same")
    remove_middle = values.register(scope, "middle", position="prepend")
    remove_last = values.register(scope, value="same", position="prepend")
    assert values.resolve(scope) == ("same", "middle", "same")
    remove_last()
    remove_last()
    assert values.resolve(scope) == ("middle", "same")
    remove_first()
    assert values.resolve(scope) == ("middle",)
    remove_middle()
    assert len(values) == 0


def test_custom_disposer_failure_is_shared_without_repeating_cleanup() -> None:
    calls = []
    error = ValueError("custom cleanup failed")

    class FailingLayer(ValueLayer):
        def register(self, token, /, value):
            cleanup = super().register(token, value)

            def dispose():
                calls.append(token)
                cleanup()
                raise error

            return dispose

    values = Compose(factory=FailingLayer)
    scope = Scope()
    dispose = values.register(scope, "old")
    with pytest.raises(ValueError) as first:
        dispose()
    assert first.value is error
    assert not values._buckets
    values.register(scope, "new")
    with pytest.raises(ValueError) as second:
        dispose()
    assert second.value is error
    assert len(calls) == 1
    assert tuple(next(iter(values.layers(scope))).values()) == ("new",)


def test_custom_keyed_storage_looks_up_one_name_and_restores_hidden_contributions() -> (
    None
):
    class NamedLayer:
        def __init__(self):
            self.by_name = {}

        def register(self, token, /, name, value):
            self.by_name.setdefault(name, {})[token] = value

            def dispose():
                entries = self.by_name[name]
                del entries[token]
                if not entries:
                    del self.by_name[name]

            return dispose

    def find_search(layers):
        for layer in layers:
            entries = layer.by_name.get("search")
            if entries:
                return next(iter(entries.values()))
        raise LookupError("search")

    tools = Compose(
        factory=NamedLayer,
        query=find_search,
    )
    root = Scope()
    child = root.fork()
    remove_root = tools.register(root, "search", "root")
    remove_other = tools.register(root, "unrelated", "other")
    remove_child = tools.register(child, name="search", value="child")
    remove_later = tools.register(child, "search", value="later")
    assert tools.resolve(child) == "child"
    remove_child()
    assert tools.resolve(child) == "later"
    remove_later()
    assert tools.resolve(child) == "root"
    remove_root()
    with pytest.raises(LookupError, match="search"):
        tools.resolve(child)
    assert len(tools) == 1
    remove_other()
    assert not tuple(tools.layers())


def test_layer_can_register_without_a_value_and_accept_framework_names_as_options() -> (
    None
):
    class Flags:
        def __init__(self, enabled):
            self.enabled = enabled
            self.options = {}

        def register(self, token, /, **options):
            self.options[token] = options

            def dispose():
                del self.options[token]

            return dispose

    values = Compose(factory=lambda: Flags(enabled=True))
    scope = Scope()
    remove_empty = values.register(scope)
    remove_options = values.register(
        scope, scope="business scope", token="business token"
    )
    layer = next(iter(values.layers(scope)))
    assert layer.enabled
    assert list(layer.options.values()) == [
        {},
        {"scope": "business scope", "token": "business token"},
    ]
    remove_empty()
    assert len(layer.options) == 1
    remove_options()
    assert not tuple(values.layers())
