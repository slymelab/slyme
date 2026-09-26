from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from slyme.context import Compose, Context, Identity, Schema, Scope, ScopeBinding
from slyme.context.core import ContextPathError
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result
from tests.compose_helpers import ValueLayer, collect_values


async def test_await_result_runs_sync_cleanup_immediately_without_scheduling() -> None:
    events: list[str] = []
    ctx = Context()
    ctx.effect(lambda: lambda: events.append("cleaned"))
    tasks = asyncio.all_tasks()
    result = await_result(ctx.dispose())
    assert events == ["cleaned"]
    assert not ctx._lifecycle._effects
    assert asyncio.all_tasks() == tasks
    assert await result is None
    assert await await_result(ctx.dispose()) is None
    assert events == ["cleaned"]


def test_await_result_preserves_immediate_cleanup_errors() -> None:
    failure = ValueError("cleanup failed")
    ctx = Context()

    def cleanup() -> None:
        raise failure

    ctx.effect(lambda: cleanup)
    previous = None
    for _ in range(2):
        with pytest.raises(BaseExceptionGroup) as caught:
            await_result(ctx.dispose())
        assert caught.value.exceptions == (failure,)
        if previous is not None:
            assert caught.value is previous
        previous = caught.value
    assert not ctx._lifecycle._effects


async def test_await_result_retains_cleanup_result_after_waiter_cancellation() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    completed = asyncio.Event()
    calls = 0
    failure = ValueError("late cleanup failure")
    ctx = Context()

    async def cleanup() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        completed.set()
        raise failure

    ctx.effect(lambda: cleanup)
    result = await_result(ctx.dispose())
    assert not started.is_set()
    waiter = asyncio.ensure_future(result)
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    finish.set()
    await completed.wait()
    for _ in range(2):
        with pytest.raises(BaseExceptionGroup) as caught:
            await await_result(ctx.dispose())
        assert caught.value.exceptions == (failure,)
    assert calls == 1 and not ctx._lifecycle._effects


def test_context_disposes_direct_ownership_in_lifo_order_recursively() -> None:
    events: list[str] = []
    ctx = Context()

    def setup(name: str):
        events.append(f"setup:{name}")
        return lambda: events.append(f"dispose:{name}")

    ctx.effect(lambda: setup("first"))
    child = ctx.fork()
    ctx.effect(lambda: setup("last"))
    child.effect(lambda: setup("child"))

    assert events == ["setup:first", "setup:last", "setup:child"]
    ctx.dispose()
    ctx.dispose()
    assert events == [
        "setup:first",
        "setup:last",
        "setup:child",
        "dispose:last",
        "dispose:child",
        "dispose:first",
    ]


def test_context_effect_can_be_disposed_early_exactly_once() -> None:
    calls = 0
    ctx = Context()

    def cleanup() -> None:
        nonlocal calls
        calls += 1

    dispose = ctx.effect(lambda: cleanup)
    dispose()
    dispose()
    ctx.dispose()
    assert calls == 1


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_mixed_ownership_preserves_lifo_after_arbitrary_early_release(
    asynchronous: bool,
) -> None:
    events: list[str] = []
    root = Context()
    root.effect(lambda: lambda: events.append("first"))
    early_child = root.fork()
    early_child.effect(lambda: lambda: events.append("early-child"))
    early_effect = root.effect(lambda: lambda: events.append("early-effect"))
    child = root.fork()
    child.effect(lambda: lambda: events.append("child"))
    root.effect(lambda: lambda: events.append("last"))

    early_effect()
    early_child.dispose()
    assert events == ["early-effect", "early-child"]
    if asynchronous:
        await await_result(root.dispose())
    else:
        root.dispose()
    assert events == ["early-effect", "early-child", "last", "child", "first"]
    assert not root._lifecycle._effects


def test_sync_disposal_snapshot_handles_sibling_cleanup_and_failure() -> None:
    events: list[str] = []
    root = Context()
    child = root.fork()
    failure = ValueError("child cleanup failed")

    def cleanup() -> None:
        events.append("child")
        raise failure

    child.effect(lambda: cleanup)
    root.effect(lambda: lambda: events.append("middle"))
    root.effect(lambda: child.dispose)

    with pytest.raises(BaseExceptionGroup) as caught:
        root.dispose()
    child_error = caught.value.exceptions[0]
    assert isinstance(child_error, BaseExceptionGroup)
    assert child_error.exceptions == (failure,)
    assert caught.value.exceptions == (child_error, child_error)
    assert events == ["child", "middle"]
    assert not root._lifecycle._effects


async def test_early_async_disposal_stays_owned_until_cleanup_finishes() -> None:
    root = Context()
    child = root.fork()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def cleanup() -> None:
        started.set()
        await finish.wait()

    release = child.effect(lambda: cleanup)
    effect = next(iter(child._lifecycle._effects))
    early = asyncio.create_task(await_result(release()))
    await started.wait()
    assert effect in child._lifecycle._effects
    disposing = asyncio.create_task(await_result(root.dispose()))
    await asyncio.sleep(0)
    assert child in root.children
    assert not disposing.done()
    finish.set()
    await asyncio.gather(early, disposing)
    assert not child._lifecycle._effects and not root._lifecycle._effects


@pytest.mark.parametrize("async_setup", [False, True])
@pytest.mark.parametrize("fails", [False, True])
async def test_lifo_cleanup_waits_for_child_before_releasing_earlier_resources(
    async_setup: bool, fails: bool
) -> None:
    events = []
    started = asyncio.Event()
    finish = asyncio.Event()
    failure = ValueError("child cleanup")
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": 1})
    root.effect(lambda: lambda: events.append("first"))
    child = root.fork()

    async def cleanup() -> None:
        events.append("child:start")
        started.set()
        await finish.wait()
        assert child.get("value") == 1
        events.append("child:finish")
        if fails:
            raise failure

    async def setup():
        await asyncio.sleep(0)
        return cleanup

    registration = child.effect(setup if async_setup else lambda: cleanup)
    root.effect(lambda: lambda: events.append("last"))
    pending = root.dispose()
    assert events == ["last"]
    waiter = asyncio.create_task(await_result(pending))
    await started.wait()
    assert events == ["last", "child:start"]
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert events == ["last", "child:start"]
    finish.set()
    for _ in range(2):
        if fails:
            with pytest.raises(BaseExceptionGroup) as caught:
                await await_result(root.dispose())
            child_error = caught.value.exceptions[0]
            assert isinstance(child_error, BaseExceptionGroup)
            assert child_error.exceptions == (failure,)
        else:
            await await_result(root.dispose())
    await await_result(registration)
    assert events == ["last", "child:start", "child:finish", "first"]
    assert not root._lifecycle._effects and not child._lifecycle._effects


def test_failed_sync_effect_disposal_replays_error_without_repeating_cleanup() -> None:
    calls = 0
    ctx = Context()

    def cleanup() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("cleanup failed")

    dispose = ctx.effect(lambda: cleanup)
    with pytest.raises(ValueError, match="cleanup failed"):
        dispose()
    with pytest.raises(ValueError, match="cleanup failed"):
        dispose()
    assert calls == 1
    ctx.dispose()


def test_context_owns_bindings_declarations_and_compose_effects() -> None:
    schema = Schema(
        {
            "hooks": Schema.leaf(mode="register"),
            "stable": Schema.leaf(),
        }
    )
    ctx = Context()
    ctx.declare(schema)
    hooks = Compose(factory=ValueLayer, query=collect_values)
    ctx.register("hooks", hooks)
    remove_plugin = ctx.declare({"plugin": {"value": Schema.leaf()}})
    ctx.set("plugin.value", 1)
    first = {"handler": "first", "metadata": {"owner": "test"}}
    ctx.effect(lambda: hooks.register(ctx.scope, first))
    ctx.effect(lambda: hooks.register(ctx.scope, "second"))

    assert hooks.resolve(ctx.scope) == (first, "second")
    assert hooks.resolve(ctx.scope)[0]["metadata"] == {"owner": "test"}
    with pytest.raises(ContextPathError, match="register"):
        ctx.set("hooks", Compose(factory=ValueLayer, query=collect_values))

    remove_plugin()
    remove_plugin()
    with pytest.raises(KeyError, match="plugin"):
        schema.resolve("plugin.value")
    ctx.dispose()
    assert hooks.resolve(ctx.scope) == ()


def test_compose_effect_keeps_its_target_when_the_context_leaf_is_replaced() -> None:
    schema = Schema({"hooks": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    hooks = Compose(factory=ValueLayer, query=collect_values)
    ctx.set("hooks", hooks)
    other = ctx.scope.fork()

    dispose = ctx.effect(lambda: ctx.get("hooks").register(other, "other"))
    assert hooks.resolve(ctx.scope) == ()
    assert hooks.resolve(other) == ("other",)
    replacement = Compose(factory=ValueLayer, query=collect_values)
    ctx.set("hooks", replacement)
    ctx.effect(lambda: ctx.get("hooks").register(other, "replacement"))
    dispose()
    assert hooks.resolve(other) == ()
    assert replacement.resolve(other) == ("replacement",)
    ctx.dispose()
    assert replacement.resolve(other) == ()


def test_scope_data_survives_until_its_last_context_viewer_is_disposed() -> None:
    class Payload:
        pass

    schema = Schema({"value": Schema.leaf()})
    root = Context()
    root.declare(schema)
    shared_scope = root.scope.fork()
    writer = root.fork(scope=shared_scope)
    reader = root.fork(scope=shared_scope)
    payload = Payload()
    payload_ref = weakref.ref(payload)
    writer.set("value", payload)

    writer.dispose()
    assert reader.get("value") is payload
    del payload
    reader.dispose()
    gc.collect()

    assert payload_ref() is None
    root.dispose()


def test_scope_viewer_sets_clear_after_the_last_context_leaves() -> None:
    root = Context()
    child_scope = root.scope.fork()
    child = root.fork(scope=child_scope)
    sibling = root.fork()
    viewers = root._store._scope_usages
    assert viewers is not None

    root_viewers = viewers[root.scope].viewers
    child_viewers = viewers[child_scope].viewers
    assert root_viewers == {root, child, sibling}
    assert child_viewers == {child}
    child.dispose()
    assert not child_viewers
    assert viewers[child_scope].viewers is child_viewers
    assert viewers[root.scope].viewers is root_viewers
    sibling.dispose()
    assert root_viewers == {root}
    root.dispose()
    assert not root_viewers
    assert all(not usage.viewers for usage in viewers.values())


def test_reused_scope_does_not_restore_data_or_repeat_old_context_disposal() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    scope = root.scope.fork()
    first = root.fork(scope=scope)
    viewers = root._store._scope_usages
    assert viewers is not None
    old = viewers[scope].viewers
    first.set("value", "old")
    first.dispose()
    assert not old
    assert viewers[scope].viewers is old

    second = root.fork(scope=scope)
    assert viewers[scope].viewers is old
    assert not second.exists("value")
    second.set("value", "new")
    first.dispose()
    assert viewers[scope].viewers == {second}
    assert second.get("value") == "new"
    root.dispose()
    assert all(not usage.viewers for usage in viewers.values())


def test_context_registers_and_releases_every_scope_in_its_mro() -> None:
    root = Context()
    parent_scope = root.scope.fork()
    initial_effects = tuple(root._lifecycle._effects)
    owner = root.fork(scope=parent_scope)
    viewers = root._store._scope_usages
    assert viewers is not None
    before = {scope: set(usage.viewers) for scope, usage in viewers.items()}
    child_scope = parent_scope.fork()

    child = root.fork(scope=child_scope)
    assert all(child in viewers[scope].viewers for scope in child_scope.mro)
    child.dispose()
    before[child_scope] = set()
    assert {scope: usage.viewers for scope, usage in viewers.items()} == before
    assert tuple(root._lifecycle._effects) == (
        *initial_effects,
        root._children[owner],
    )
    root.dispose()


def test_context_disposal_cleans_scope_indexes_and_binding_data() -> None:
    class Payload:
        pass

    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    child = root.fork(scope=root.scope.fork())
    payload = Payload()
    payload_ref = weakref.ref(payload)
    child.set("value", payload)
    del payload
    binding = root._store._data[root.resolve_entry("value")]
    identity_scopes = next(iter(binding._data.values())).scopes
    viewers = root._store._scope_usages
    assert viewers is not None
    scope_viewers = viewers[child.scope].viewers

    child.dispose()
    child.dispose()
    assert not scope_viewers
    assert not identity_scopes
    assert viewers[child.scope].viewers is scope_viewers
    assert viewers[child.scope].entries == {root.resolve_entry("value")}
    assert viewers[root.scope].viewers == {root}
    assert not binding._data
    gc.collect()
    assert payload_ref() is None
    root.dispose()
    assert not root._store._data


def test_binding_scope_release_is_idempotent() -> None:
    shared_identity = Identity("shared", blocked=True)
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    child = root.derive(
        bindings={"value": ScopeBinding(shared_identity, blocked=False)}
    )
    child.set("value", "old")
    binding = root._store._data[root.resolve_entry("value")]
    binding.release_scope(child.scope)
    binding.release_scope(child.scope)
    assert not binding._data
    child.set("value", "new")
    assert binding._data[shared_identity].scopes == {child.scope}
    assert child.get("value") == "new"
    root.dispose()


def test_scope_release_only_notifies_bindings_used_by_that_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context()
    root.declare(Schema({"first": Schema.leaf(), "second": Schema.leaf()}))
    parent_scope = root.scope.fork()
    child_scope = parent_scope.fork()
    child = root.fork(scope=child_scope)
    child.set("first", "child")
    child.set("second", "child")
    bindings = tuple(
        root._store._data[root.resolve_entry(path)] for path in ("first", "second")
    )
    calls = []
    original = type(bindings[0]).release_scope

    def record(self, scope):
        calls.append((self, scope))
        original(self, scope)

    with monkeypatch.context() as patch:
        patch.setattr(type(bindings[0]), "release_scope", record)
        child.dispose()
    assert len(calls) == len(bindings)
    assert set(calls) == {(binding, child_scope) for binding in bindings}
    root.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("effect_failure", [False, True])
async def test_scope_cleanup_failure_finishes_other_bindings_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
    asynchronous: bool,
    effect_failure: bool,
) -> None:
    root = Context()
    root.declare(Schema({"first": Schema.leaf(), "second": Schema.leaf()}))
    initial_effects = tuple(root._lifecycle._effects)
    parent_scope = root.scope.fork()
    parent = root.fork(scope=parent_scope)
    parent.update({"first": "parent", "second": "parent"})
    child = root.fork(scope=parent_scope.fork())
    child.update({"first": "child", "second": "child"})
    parent.dispose()
    bindings = tuple(
        root._store._data[root.resolve_entry(path)] for path in ("first", "second")
    )
    original = type(bindings[0]).release_scope
    failures = [ValueError("first scope failed"), ValueError("parent scope failed")]
    calls = []

    def fail(self, scope):
        calls.append((self, scope))
        original(self, scope)
        if self is bindings[0]:
            raise failures[0 if scope is child.scope else 1]

    primary = ValueError("effect failed")

    def cleanup():
        raise primary

    if effect_failure:
        child.effect(lambda: cleanup)
    previous = None
    with monkeypatch.context() as patch:
        patch.setattr(type(bindings[0]), "release_scope", fail)
        for _ in range(2):
            with pytest.raises(BaseExceptionGroup) as raised:
                if asynchronous:
                    await await_result(child.dispose())
                else:
                    child.dispose()
            if effect_failure:
                cleanup_error = raised.value.__context__
                assert isinstance(cleanup_error, BaseExceptionGroup)
                assert cleanup_error.exceptions == (primary,)
            release_error = raised.value
            assert release_error.__cause__ is None
            assert release_error.message == "Failed to release expired Scopes"
            assert release_error.exceptions == tuple(failures)
            if previous is not None:
                assert raised.value is previous
            previous = raised.value

    assert len(calls) == 2 * len(bindings)
    assert set(calls[: len(bindings)]) == {
        (binding, child.scope) for binding in bindings
    }
    assert set(calls[len(bindings) :]) == {
        (binding, parent_scope) for binding in bindings
    }
    assert not root.children
    assert tuple(root._lifecycle._effects) == initial_effects
    assert root._store._scope_usages is not None
    assert root._store._scope_usages[root.scope].viewers == {root}
    for scope in (child.scope, parent_scope):
        assert not root._store._scope_usages[scope].viewers
        assert root._store._scope_usages[scope].entries == {
            root.resolve_entry("first"),
            root.resolve_entry("second"),
        }
    for binding in bindings:
        assert not binding._data
    with pytest.raises(RuntimeError, match="disposed"):
        child.get("first")
    root.dispose()
    assert not root._store._data


def test_failed_binding_restore_rolls_back_new_scope_usage(monkeypatch) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    initial_effects = tuple(root._lifecycle._effects)
    identity = Identity(blocked=True)
    previous = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    scope = previous.scope
    previous.dispose()
    writer = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    writer.set("value", "live")
    binding = root._store._data[root.resolve_entry("value")]
    viewers = root._store._scope_usages
    assert viewers is not None
    before = {scope: set(usage.viewers) for scope, usage in viewers.items()}
    original = type(binding).restore_scope

    def fail(self, scope):
        if original(self, scope):
            raise ValueError("restore failed")
        return False

    with monkeypatch.context() as patch:
        patch.setattr(type(binding), "restore_scope", fail)
        with pytest.raises(ValueError, match="restore failed"):
            root.fork(scope=scope)
    assert {scope: usage.viewers for scope, usage in viewers.items()} == before
    assert binding._data[identity].scopes == {writer.scope}
    assert writer.get("value") == "live"
    assert root.children == (writer,)
    assert tuple(root._lifecycle._effects) == (*initial_effects, root._children[writer])
    root.dispose()


def test_scope_reuse_after_disposal_restores_bindings_without_old_values() -> None:
    root = Context()
    root.declare(Schema({"first": Schema.leaf(), "second": Schema.leaf()}))
    scope = root.scope.fork()
    writer = root.fork(scope=scope)

    class Payload:
        pass

    payloads = {path: Payload() for path in ("first", "second")}
    references = [weakref.ref(payload) for payload in payloads.values()]
    writer.update(payloads)
    del payloads
    bindings = {
        path: root._store._data[root.resolve_entry(path)]
        for path in ("first", "second")
    }
    identities = {
        path: binding._scope_bindings[scope].identity
        for path, binding in bindings.items()
    }
    writer.dispose()
    gc.collect()
    assert all(reference() is None for reference in references)
    assert all(not binding._data for binding in bindings.values())
    assert not root._store._scope_usages[scope].viewers

    reader = root.fork(scope=scope)
    for path, binding in bindings.items():
        assert not reader.exists(path)
        assert binding._scope_bindings[scope].identity is identities[path]
        assert binding.scopes == (scope,)
    assert root._store._scope_usages[scope].entries == {
        root.resolve_entry("first"),
        root.resolve_entry("second"),
    }
    reader.update({"first": "new first", "second": "new second"})
    writer.dispose()
    assert reader.get("first") == "new first"
    assert reader.get("second") == "new second"
    reader.dispose()
    assert all(not binding._data for binding in bindings.values())
    assert not root._store._scope_usages[scope].viewers
    root.dispose()


def test_reacquiring_a_released_mro_restores_each_scopes_binding_index() -> None:
    root = Context()
    root.declare({"parent_value": Schema.leaf(), "child_value": Schema.leaf()})
    parent_scope = root.scope.fork()
    parent = root.fork(scope=parent_scope)
    child_scope = parent_scope.fork()
    writer = root.fork(scope=child_scope)

    class Payload:
        pass

    parent_payload, child_payload = Payload(), Payload()
    parent_ref, child_ref = weakref.ref(parent_payload), weakref.ref(child_payload)
    parent.set("parent_value", parent_payload)
    writer.set("child_value", child_payload)
    del parent_payload, child_payload
    parent.dispose()
    assert writer.get("parent_value") is parent_ref()
    writer.dispose()
    gc.collect()
    assert parent_ref() is None and child_ref() is None

    reader = root.fork(scope=child_scope)
    assert not reader.exists("parent_value")
    assert not reader.exists("child_value")
    assert root._store._scope_usages[parent_scope].entries == {
        root.resolve_entry("parent_value")
    }
    assert root._store._scope_usages[child_scope].entries == {
        root.resolve_entry("child_value")
    }
    for scope in (parent_scope, child_scope):
        assert root._store._scope_usages[scope].viewers == {reader}
    new_parent = root.fork(scope=parent_scope)
    new_parent.set("parent_value", "new parent")
    reader.set("child_value", "new child")
    new_parent.dispose()
    assert reader.get("parent_value") == "new parent"
    assert reader.get("child_value") == "new child"
    reader.dispose()
    assert not root._store._scope_usages[parent_scope].viewers
    assert not root._store._scope_usages[child_scope].viewers
    for path in ("parent_value", "child_value"):
        assert not root._store._data[root.resolve_entry(path)]._data
    root.dispose()


def test_released_scope_reuses_identity_with_new_data() -> None:
    shared_identity = Identity("shared", blocked=True)
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    writer = root.derive(
        bindings={"value": ScopeBinding(shared_identity, blocked=False)}
    )
    scope = writer.scope

    class Payload:
        pass

    payload = Payload()
    payload_ref = weakref.ref(payload)
    writer.set("value", payload)
    del payload
    binding = root._store._data[root.resolve_entry("value")]
    old_scopes = binding._data[shared_identity].scopes
    writer.dispose()
    gc.collect()
    assert payload_ref() is None
    assert not old_scopes
    assert not binding._data

    reader = root.fork(scope=scope)
    assert not reader.exists("value")
    reader.set("value", "new")
    assert binding._data[shared_identity].scopes is not old_scopes
    assert binding._data[shared_identity].scopes == {scope}
    writer.dispose()
    assert reader.get("value") == "new"
    root.dispose()
    assert not binding._data


@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_scope_restoration_failure_uses_context_disposal(
    monkeypatch: pytest.MonkeyPatch,
    cleanup_fails: bool,
) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    initial_effects = tuple(root._lifecycle._effects)
    root.set("value", "live")
    binding = root._store._data[root.resolve_entry("value")]
    previous = root.derive(bindings={"value": ScopeBinding(blocked=True)})
    child_scope = previous.scope
    previous.dispose()
    acquisition_error = ValueError("restore failed")
    cleanup_error = ValueError("cleanup failed")
    original = type(binding).release_scope
    original_restore = type(binding).restore_scope
    release_scope = type(root._store).release_scope
    constructed, released = [], []

    def fail_restore(self, scope):
        child = next(iter(root._store._scope_usages[scope].viewers))
        assert root._children[child] in root._lifecycle._effects
        assert all(
            child in root._store._scope_usages[parent].viewers
            for parent in child.scope.mro
        )
        constructed.append(child)
        if original_restore(self, scope):
            raise acquisition_error
        return False

    def fail_release(self, scope):
        original(self, scope)
        if scope is child_scope and cleanup_fails:
            raise cleanup_error

    def record_release(store, viewer, scope):
        released.append(viewer)
        release_scope(store, viewer, scope)

    with monkeypatch.context() as patch:
        patch.setattr(type(binding), "restore_scope", fail_restore)
        patch.setattr(type(binding), "release_scope", fail_release)
        patch.setattr(type(root._store), "release_scope", record_release)
        if cleanup_fails:
            with pytest.raises(
                BaseExceptionGroup,
                match="Failed to release expired Scopes",
            ) as raised:
                root.fork(scope=child_scope)
            assert raised.value.exceptions == (cleanup_error,)
            assert raised.value.__context__ is acquisition_error
            with pytest.raises(BaseExceptionGroup) as repeated:
                constructed[0].dispose()
            assert repeated.value is raised.value
        else:
            with pytest.raises(ValueError) as raised:
                root.fork(scope=child_scope)
            assert raised.value is acquisition_error
            assert constructed[0].dispose() is None
    assert released == constructed
    assert len(constructed) == 1
    assert not root.children
    with pytest.raises(RuntimeError, match="disposed"):
        constructed[0]._lifecycle.assert_readable()
    assert root._store._scope_usages is not None
    assert root._store._scope_usages[root.scope].viewers == {root}
    assert not root._store._scope_usages[child_scope].viewers
    assert tuple(root._lifecycle._effects) == initial_effects
    assert root.get("value") == "live"
    root.dispose()


def test_bound_identity_data_survives_until_its_last_viewer_is_disposed() -> None:
    class Payload:
        pass

    schema = Schema({"value": Schema.leaf()})
    root = Context()
    root.declare(schema)
    identity = Identity(blocked=True)
    writer = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    reader = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    payload = Payload()
    payload_ref = weakref.ref(payload)
    writer.set("value", payload)

    writer.dispose()
    assert reader.get("value") is payload
    del payload
    reader.dispose()
    gc.collect()

    assert payload_ref() is None
    late_reader = root.fork(scope=reader.scope)
    assert not late_reader.exists("value")
    late_reader.dispose()
    root.dispose()


def test_bound_identity_keeps_registration_owned_by_its_context() -> None:
    schema = Schema({"value": Schema.leaf(mode="register")})
    root = Context()
    root.declare(schema)
    identity = Identity(blocked=True)
    owner = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    viewer = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    owner.register("value", "temporary")

    assert viewer.get("value") == "temporary"
    owner.dispose()
    assert not viewer.exists("value")
    viewer.dispose()
    root.dispose()


def test_identity_index_tracks_isolated_scopes_after_value_deletion() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    identity = Identity(blocked=True)
    writer = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    viewer = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    binding = root._store._data[root.resolve_entry("value")]

    scopes = binding._data[identity].scopes
    assert scopes == {writer.scope, viewer.scope}

    writer.set("value", "first")
    writer.delete("value")
    assert not writer.exists("value")
    assert not viewer.exists("value")
    assert binding._data[identity].scopes is scopes
    writer.set("value", "second")
    writer.dispose()
    assert scopes == {viewer.scope}
    assert viewer.get("value") == "second"

    viewer.dispose()
    assert not scopes
    assert not binding._data
    assert binding._scope_bindings[writer.scope].identity is identity
    root.dispose()


def test_identity_reuse_starts_new_ownership_without_reviving_old_data() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    identity = Identity(blocked=True)
    first = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    first.set("value", "old")
    binding = root._store._data[root.resolve_entry("value")]
    old = binding._data[identity].scopes
    first.dispose()
    assert not old

    second = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    assert binding._data[identity].scopes is not old
    assert not second.exists("value")
    second.set("value", "new")
    first.dispose()
    assert second.get("value") == "new"
    root.dispose()
    assert not binding._data


def test_schema_disposal_releases_payload_without_mutating_detached_scope_set() -> None:
    class Payload:
        pass

    root = Context()
    remove_schema = root.declare({"value": Schema.leaf(mode="register")})
    payload = Payload()
    payload_ref = weakref.ref(payload)
    root.register("value", payload)
    binding = root._store._data[root.resolve_entry("value")]
    binding_ref = weakref.ref(binding)
    scopes = next(iter(binding._data.values())).scopes
    del binding, payload

    remove_schema()
    gc.collect()
    assert set(root._store._data) == {
        root.resolve_entry(ref.path) for ref in root.get("$").flatten()
    }
    assert binding_ref() is not None
    assert not binding_ref()._data
    assert payload_ref() is None
    assert scopes == {root.scope}
    root.dispose()
    gc.collect()
    assert binding_ref() is None


@pytest.mark.parametrize("descendant", [False, True])
@pytest.mark.parametrize("reader_first", [False, True])
def test_reused_scope_protects_identity_without_reading_or_binding_again(
    descendant: bool,
    reader_first: bool,
) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    identity = Identity(blocked=True)
    previous = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    saved_scope = previous.scope
    previous.set("value", "old")
    previous.dispose()
    binding = root._store._data[root.resolve_entry("value")]
    assert not binding._data

    reader_scope = saved_scope.fork() if descendant else saved_scope
    if reader_first:
        reader = root.fork(scope=reader_scope)
    writer = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    writer.set("value", "new")
    if not reader_first:
        reader = root.fork(scope=reader_scope)

    writer.dispose()
    assert binding._data[identity].scopes == {saved_scope}
    assert reader.get("value") == "new"
    reader.dispose()
    assert not binding._data
    root.dispose()


def test_identity_index_keeps_both_observed_parents_of_a_diamond_scope() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    identity = Identity(blocked=True)
    left = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    right = root.derive(bindings={"value": ScopeBinding(identity, blocked=False)})
    reader = root.fork(scope=Scope(parents=(left.scope, right.scope)))
    left.set("value", "shared")
    binding = root._store._data[root.resolve_entry("value")]

    left.dispose()
    right.dispose()
    assert binding._data[identity].scopes == {left.scope, right.scope}
    assert reader.get("value") == "shared"
    reader.dispose()
    assert not binding._data
    root.dispose()


def test_scope_release_does_not_scan_other_identity_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    sessions = [root.fork(scope=root.scope.fork()) for _ in range(1000)]
    for number, session in enumerate(sessions):
        session.set("value", number)
    binding = root._store._data[root.resolve_entry("value")]
    scope_refs = [weakref.ref(session.scope) for session in sessions]
    assert len(binding._data) == 1000

    def reject_scan():
        pytest.fail("Scope release must not scan every Scope-to-identity binding.")

    monkeypatch.setattr(binding._scope_bindings, "items", reject_scan)
    sessions[0].dispose()
    assert len(binding._data) == 999
    assert sessions[-1].get("value") == 999
    for session in sessions[1:]:
        session.dispose()
    assert not binding._data

    del session
    sessions.clear()
    gc.collect()
    assert all(scope_ref() is None for scope_ref in scope_refs)
    assert not binding._scope_bindings
    root.dispose()


def test_isolated_contexts_can_share_one_private_identity() -> None:
    schema = Schema({"service": Schema.leaf()})
    root = Context()
    root.declare(schema)
    root.update({"service": "root"})
    identity = Identity(blocked=True)
    left = root.derive(bindings={"service": ScopeBinding(identity, blocked=False)})
    right = root.derive(bindings={"service": ScopeBinding(identity, blocked=False)})

    assert not left.exists("service")
    assert not right.exists("service")
    left.set("service", "isolated")
    assert right.get("service") == "isolated"

    left.dispose()
    assert right.get("service") == "isolated"
    right.dispose()

    later = root.derive(bindings={"service": ScopeBinding(identity, blocked=False)})
    assert not later.exists("service")
    later.dispose()
    root.dispose()


def test_shared_scope_distinguishes_assignment_and_registration_ownership() -> None:
    schema = Schema(
        {
            "set_value": Schema.leaf(),
            "registered_value": Schema.leaf(mode="register"),
        }
    )
    root = Context()
    root.declare(schema)
    shared_scope = root.scope.fork()
    owner = root.fork(scope=shared_scope)
    viewer = root.fork(scope=shared_scope)

    owner.set("set_value", "scope-owned")
    owner.register("registered_value", "context-owned")
    with pytest.raises(ContextPathError, match="register"):
        viewer.set("registered_value", "new")

    owner.dispose()
    assert viewer.get("set_value") == "scope-owned"
    assert not viewer.exists("registered_value")

    viewer.dispose()
    late_viewer = root.fork(scope=shared_scope)
    assert not late_viewer.exists("set_value")
    assert not late_viewer.exists("registered_value")
    late_viewer.dispose()
    root.dispose()


def test_descendant_scope_keeps_ancestor_data_visible() -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context()
    root.declare(schema)
    ancestor_scope = root.scope.fork()
    owner = root.fork(scope=ancestor_scope)
    viewer = root.fork(scope=ancestor_scope.fork())
    owner.set("value", "ancestor")

    owner.dispose()
    assert viewer.get("value") == "ancestor"
    viewer.dispose()
    root.dispose()


def test_same_scope_does_not_share_independent_context_data_roots() -> None:
    schema = Schema({"value": Schema.leaf()})
    left = Context()
    left.declare(schema)
    right = Context(scope=left.scope)
    right.declare(schema)
    left.set("value", 1)

    assert not right.exists("value")
    left.dispose()
    right.dispose()


def test_parent_strongly_owns_undisposed_children() -> None:
    root = Context()
    child = root.fork()
    child_ref = weakref.ref(child)
    del child
    gc.collect()

    assert child_ref() is not None
    root.dispose()
    gc.collect()
    assert child_ref() is None


def test_disposed_context_rejects_data_and_lifecycle_operations() -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"value": 1})
    ctx.dispose()

    for operation in (
        lambda: ctx.resolve("value"),
        lambda: ctx.resolve_entry("value"),
        lambda: ctx.get("value"),
        lambda: ctx.extract({}),
        lambda: ctx.set("value", 2),
        lambda: ctx.delete("value"),
        lambda: ctx.update({}),
        lambda: ctx.drop([]),
        lambda: ctx.fork(),
        lambda: ctx.effect(lambda: lambda: None),
    ):
        with pytest.raises(RuntimeError, match="disposed"):
            operation()


def test_cleanup_can_read_context_but_cannot_start_new_mutation() -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"value": 1})
    observed: list[int] = []

    def cleanup() -> None:
        observed.append(ctx.get("value"))
        with pytest.raises(RuntimeError, match="being disposed"):
            ctx.set("value", 2)
        with pytest.raises(RuntimeError, match="being disposed"):
            ctx.delete("value")

    ctx.effect(lambda: cleanup)
    ctx.dispose()
    assert observed == [1]


def test_dispose_returns_async_completion_without_starting_a_loop() -> None:
    events: list[str] = []
    ctx = Context()
    ctx.effect(lambda: lambda: events.append("sync"))
    child = ctx.fork()

    async def cleanup() -> None:
        events.append("async")

    child.effect(lambda: cleanup)
    pending = ctx.dispose()
    assert events == []

    asyncio.run(await_result(pending))
    assert events == ["async", "sync"]


async def test_async_dispose_is_shared_and_runs_cleanup_once() -> None:
    calls = 0
    ctx = Context()

    async def cleanup() -> None:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1

    ctx.effect(lambda: cleanup)
    await asyncio.gather(await_result(ctx.dispose()), await_result(ctx.dispose()))
    await await_result(ctx.dispose())
    assert calls == 1


async def test_dispose_marks_context_before_scheduling_cleanup() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    ctx = Context()

    async def cleanup() -> None:
        started.set()
        await release.wait()

    ctx.effect(lambda: cleanup)
    pending = ctx.dispose()
    assert not started.is_set()
    with pytest.raises(RuntimeError, match="being disposed"):
        ctx.fork()
    task = asyncio.create_task(await_result(pending))
    await started.wait()
    release.set()
    await task


async def test_cancelling_a_dispose_waiter_does_not_cancel_cleanup() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    ctx = Context()

    async def cleanup() -> None:
        started.set()
        await release.wait()
        finished.set()

    ctx.effect(lambda: cleanup)
    waiter = asyncio.create_task(await_result(ctx.dispose()))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not finished.is_set()

    release.set()
    await await_result(ctx.dispose())
    assert finished.is_set()


async def test_async_cleanup_failure_does_not_skip_remaining_cleanup() -> None:
    events: list[str] = []
    ctx = Context()

    async def cleanup(name: str) -> None:
        events.append(name)

    async def fail() -> None:
        events.append("fail")
        raise ValueError("cleanup failed")

    ctx.effect(lambda: lambda: cleanup("first"))
    ctx.effect(lambda: fail)
    ctx.effect(lambda: lambda: cleanup("last"))

    with pytest.raises(BaseExceptionGroup) as caught:
        await await_result(ctx.dispose())
    assert isinstance(caught.value.exceptions[0], ValueError)
    assert str(caught.value.exceptions[0]) == "cleanup failed"
    with pytest.raises(BaseExceptionGroup) as replayed:
        await await_result(ctx.dispose())
    assert replayed.value is caught.value
    assert events == ["last", "fail", "first"]


async def test_cancelled_async_cleanup_does_not_skip_remaining_cleanup() -> None:
    events: list[str] = []
    ctx = Context()

    async def cleanup(name: str) -> None:
        events.append(name)

    async def cancel() -> None:
        events.append("cancel")
        raise asyncio.CancelledError

    ctx.effect(lambda: lambda: cleanup("first"))
    ctx.effect(lambda: cancel)
    ctx.effect(lambda: lambda: cleanup("last"))

    with pytest.raises(BaseExceptionGroup) as caught:
        await await_result(ctx.dispose())
    assert isinstance(caught.value.exceptions[0], asyncio.CancelledError)
    with pytest.raises(BaseExceptionGroup) as replayed:
        await await_result(ctx.dispose())
    assert replayed.value is caught.value
    assert events == ["last", "cancel", "first"]


def test_parent_disposal_blocks_new_effects_in_active_children() -> None:
    root = Context()
    child = root.fork()

    async def async_cleanup() -> None:
        pass

    root.effect(lambda: lambda: child.effect(lambda: async_cleanup))

    with pytest.raises(BaseExceptionGroup) as caught:
        root.dispose()
    assert isinstance(caught.value.exceptions[0], RuntimeError)
    assert "ancestor Lifecycle is being disposed" in str(caught.value.exceptions[0])
    with pytest.raises(RuntimeError, match="disposed"):
        child.fork()


def test_dispose_blocks_descendant_mutations_before_first_cleanup() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": 1})
    events: list[str] = []
    root.effect(lambda: lambda: events.append("first"))
    child = root.fork()
    child.effect(lambda: lambda: events.append("child"))
    grandchild = child.fork()
    grandchild.effect(lambda: lambda: events.append("grandchild"))
    sibling = root.fork(scope=root.scope.fork())
    sibling.effect(lambda: lambda: events.append("sibling"))

    def assert_mutations_blocked(ctx: Context) -> None:
        assert ctx.get("value") == 1
        for operation in (
            lambda: ctx.set("value", 2),
            lambda: ctx.delete("value"),
            lambda: ctx.update({"value": 2}),
            lambda: ctx.drop(["value"]),
            lambda: ctx.register("value", 2),
            lambda: ctx.declare({"other": Schema.leaf()}),
            lambda: ctx.effect(lambda: lambda: None),
            lambda: ctx.fork(),
            lambda: Context(parent=ctx),
            lambda: ctx.derive(bindings={"value": ScopeBinding()}),
            lambda: ctx.derive(bindings={"value": ScopeBinding(blocked=True)}),
        ):
            with pytest.raises(RuntimeError, match="being disposed"):
                operation()

    def cleanup() -> None:
        events.append("last")
        for ctx in (root, child, grandchild, sibling):
            assert_mutations_blocked(ctx)

    root.effect(lambda: cleanup)
    root.dispose()
    assert events == ["last", "sibling", "grandchild", "child", "first"]


async def test_dispose_pending_child_can_dispose_while_parent_cleanup_waits() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": 1})
    child = root.fork()
    grandchild = child.fork()
    events: list[str] = []
    child.effect(lambda: lambda: events.append("child"))
    started = asyncio.Event()
    finish = asyncio.Event()

    async def cleanup() -> None:
        events.append("parent:start")
        started.set()
        await finish.wait()
        events.append("parent:finish")

    root.effect(lambda: cleanup)
    pending = root.dispose()
    assert not started.is_set()
    for ctx in (child, grandchild):
        assert ctx.get("value") == 1
        with pytest.raises(RuntimeError, match="being disposed"):
            ctx.set("value", 2)

    waiter = asyncio.create_task(await_result(pending))
    await started.wait()
    child.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        grandchild.get("value")
    finish.set()
    await waiter
    assert events == ["parent:start", "child", "parent:finish"]


@pytest.mark.parametrize("fail", [False, True])
async def test_parent_disposal_preserves_child_cleanup_in_progress(fail: bool) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": 1})
    child = root.fork()
    grandchild = child.fork()
    started = asyncio.Event()
    finish = asyncio.Event()
    calls = 0
    failure = ValueError("child cleanup failed")

    async def cleanup() -> None:
        nonlocal calls
        calls += 1
        started.set()
        await finish.wait()
        if fail:
            raise failure

    child.effect(lambda: cleanup)
    child_pending = child.dispose()
    child_waiter = asyncio.create_task(await_result(child_pending))
    await started.wait()
    root_pending = root.dispose()
    assert grandchild.get("value") == 1
    with pytest.raises(RuntimeError, match="being disposed"):
        grandchild.set("value", 2)
    finish.set()
    results = await asyncio.gather(
        child_waiter, await_result(root_pending), return_exceptions=True
    )
    if fail:
        child_error, parent_error = results
        assert isinstance(child_error, BaseExceptionGroup)
        assert isinstance(parent_error, BaseExceptionGroup)
        assert child_error.exceptions == (failure,)
        assert parent_error.exceptions == (child_error,)
    else:
        assert results == [None, None]
    assert calls == 1
    assert not root._lifecycle._effects and not child._lifecycle._effects
    if fail:
        with pytest.raises(BaseExceptionGroup) as caught:
            await await_result(root.dispose())
        assert caught.value is parent_error


@pytest.mark.parametrize("inherit_scope", [False, True])
def test_dispose_does_not_close_contexts_that_only_share_visibility(
    inherit_scope: bool,
) -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    owner = root.fork(scope=root.scope.fork())
    owner.set("value", 1)
    child = owner.fork()
    viewer = root.fork(
        scope=owner.scope.fork() if inherit_scope else owner.scope,
    )

    def cleanup() -> None:
        with pytest.raises(RuntimeError, match="being disposed"):
            child.set("value", 2)
        assert viewer.get("value") == 1
        viewer.set("value", 3)

    owner.effect(lambda: cleanup)
    owner.dispose()
    assert viewer.get("value") == 3
    viewer.fork()
    root.dispose()


async def test_cancelled_dispose_waiter_does_not_reopen_descendants() -> None:
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    root.update({"value": 1})
    child = root.fork()
    grandchild = child.fork()
    cleaned: list[str] = []
    child.effect(lambda: lambda: cleaned.append("child"))
    started = asyncio.Event()
    finish = asyncio.Event()

    async def cleanup() -> None:
        started.set()
        await finish.wait()
        raise ValueError("parent cleanup failed")

    root.effect(lambda: cleanup)
    waiter = asyncio.ensure_future(await_result(root.dispose()))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert grandchild.get("value") == 1
    with pytest.raises(RuntimeError, match="being disposed"):
        grandchild.fork()
    finish.set()
    with pytest.raises(BaseExceptionGroup) as caught:
        await await_result(root.dispose())
    assert isinstance(caught.value.exceptions[0], ValueError)
    assert str(caught.value.exceptions[0]) == "parent cleanup failed"
    assert cleaned == ["child"]
    with pytest.raises(RuntimeError, match="disposed"):
        grandchild.get("value")


def test_cleanup_failures_do_not_skip_remaining_cleanup_or_scope_release() -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"value": object()})
    events: list[str] = []
    ctx.effect(lambda: lambda: events.append("first"))

    def fail() -> None:
        events.append("fail")
        raise RuntimeError("cleanup failed")

    ctx.effect(lambda: fail)
    ctx.effect(lambda: lambda: events.append("last"))

    with pytest.raises(BaseExceptionGroup) as caught:
        ctx.dispose()
    assert isinstance(caught.value.exceptions[0], RuntimeError)
    assert str(caught.value.exceptions[0]) == "cleanup failed"
    assert events == ["last", "fail", "first"]
    with pytest.raises(RuntimeError, match="disposed"):
        ctx.get("value")


def test_failed_sync_context_disposal_replays_error_without_repeating_cleanup() -> None:
    calls = 0
    ctx = Context()

    def cleanup() -> None:
        nonlocal calls
        calls += 1
        raise ValueError("cleanup failed")

    ctx.effect(lambda: cleanup)
    with pytest.raises(BaseExceptionGroup) as caught:
        ctx.dispose()
    assert isinstance(caught.value.exceptions[0], ValueError)
    assert str(caught.value.exceptions[0]) == "cleanup failed"
    with pytest.raises(BaseExceptionGroup) as replayed:
        ctx.dispose()
    assert replayed.value is caught.value
    assert calls == 1


def test_final_schema_removal_releases_an_owned_registration_payload() -> None:
    class Payload:
        pass

    ctx = Context()
    remove_declaration = ctx.declare({"temporary": Schema.leaf(mode="register")})
    payload = Payload()
    payload_ref = weakref.ref(payload)
    ctx.register("temporary", payload)
    del payload

    remove_declaration()
    gc.collect()

    assert payload_ref() is None
    with pytest.raises(KeyError, match="temporary"):
        ctx.resolve("temporary")

    remove_redeclaration = ctx.declare({"temporary": Schema.leaf()})
    assert not ctx.exists("temporary")
    remove_redeclaration()
    ctx.dispose()
