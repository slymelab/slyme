from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from slyme.context import Compose, Context, Schema, Scope
from slyme.context.core import ContextPathError
from slyme.utils.awaitable import resolve


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
        await resolve(root.dispose())
    else:
        root.dispose()
    assert events == ["early-effect", "early-child", "last", "child", "first"]
    assert not root._owned


def test_sync_disposal_visits_each_descendant_once() -> None:
    checked: list[Context] = []

    class TrackedContext(Context):
        def dispose(self):
            checked.append(self)
            return super().dispose()

    root = TrackedContext()
    left = root.fork()
    grandchild = left.fork()
    right = root.fork()
    root.dispose()
    assert checked == [root, right, left, grandchild]


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

    with pytest.raises(ValueError) as caught:
        root.dispose()
    assert caught.value is failure
    assert events == ["child", "middle"]
    assert not root._owned


async def test_early_async_disposal_stays_owned_until_cleanup_finishes() -> None:
    root = Context()
    child = root.fork()
    started = asyncio.Event()
    finish = asyncio.Event()

    async def cleanup() -> None:
        started.set()
        await finish.wait()

    release = child.effect(lambda: cleanup)
    effect = next(iter(child._owned))
    early = asyncio.create_task(resolve(release()))
    await started.wait()
    assert effect in child._owned
    disposing = asyncio.create_task(resolve(root.dispose()))
    await asyncio.sleep(0)
    assert child in root._owned
    assert not disposing.done()
    finish.set()
    await asyncio.gather(early, disposing)
    assert not child._owned and not root._owned


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
            "hooks": Schema.leaf(replaceable=False),
            "stable": Schema.leaf(),
        }
    )
    ctx = Context(schema=schema)
    hooks = Compose[str, tuple[str, ...]].collect()
    ctx.add("hooks", hooks)
    remove_plugin = ctx.declare({"plugin": {"value": Schema.leaf()}})
    ctx.set("plugin.value", 1)
    ctx.effect(lambda: hooks.add(ctx.scope, "first", metadata={"owner": "test"}))
    ctx.effect(lambda: hooks.add(ctx.scope, "zeroth", position="prepend"))

    assert hooks.resolve(ctx.scope) == ("zeroth", "first")
    assert hooks.entries(ctx.scope)[1]["metadata"] == {"owner": "test"}
    with pytest.raises(ContextPathError, match="non-replaceable"):
        ctx.set("hooks", Compose.collect())

    remove_plugin()
    remove_plugin()
    with pytest.raises(KeyError, match="plugin"):
        schema.resolve("plugin.value")
    ctx.dispose()
    assert hooks.resolve(ctx.scope) == ()


def test_compose_effect_keeps_its_target_when_the_context_leaf_is_replaced() -> None:
    schema = Schema({"hooks": Schema.leaf()})
    ctx = Context(schema=schema)
    hooks = Compose[str, tuple[str, ...]].collect()
    ctx.add("hooks", hooks)
    other = ctx.scope.fork()

    dispose = ctx.effect(lambda: ctx.get("hooks").add(other, "other"))
    assert hooks.resolve(ctx.scope) == ()
    assert hooks.resolve(other) == ("other",)
    replacement = Compose[str, tuple[str, ...]].collect()
    ctx.set("hooks", replacement)
    ctx.effect(lambda: ctx.get("hooks").add(other, "replacement"))
    dispose()
    assert hooks.resolve(other) == ()
    assert replacement.resolve(other) == ("replacement",)
    ctx.dispose()
    assert replacement.resolve(other) == ()


def test_scope_data_survives_until_its_last_context_viewer_is_disposed() -> None:
    class Payload:
        pass

    schema = Schema({"value": Schema.leaf()})
    root = Context(schema=schema)
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
    viewers = root._scope_viewers
    assert viewers is not None

    root_viewers = viewers[root.scope]
    child_viewers = viewers[child_scope]
    assert root_viewers == {root, child, sibling}
    assert child_viewers == {child}
    child.dispose()
    assert not child_viewers
    assert child_scope not in viewers
    assert viewers[root.scope] is root_viewers
    sibling.dispose()
    assert root_viewers == {root}
    root.dispose()
    assert not root_viewers
    assert viewers == {}


def test_scope_viewer_registration_rejects_unbalanced_operations() -> None:
    ctx = Context()

    with pytest.raises(RuntimeError, match="already registered"):
        ctx._acquire_scope()
    ctx._release_scope()
    with pytest.raises(RuntimeError, match="not registered"):
        ctx._release_scope()
    ctx._acquire_scope()
    ctx.dispose()


def test_reused_scope_does_not_restore_data_or_repeat_old_context_disposal() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    scope = root.scope.fork()
    first = root.fork(scope=scope)
    viewers = root._scope_viewers
    assert viewers is not None
    old = viewers[scope]
    first.set("value", "old")
    first.dispose()
    assert not old
    assert scope not in viewers

    second = root.fork(scope=scope)
    assert viewers[scope] is not old
    assert not second.exists("value")
    second.set("value", "new")
    first.dispose()
    assert viewers[scope] == {second}
    assert second.get("value") == "new"
    root.dispose()
    assert not viewers


def test_context_registers_and_releases_every_scope_in_its_mro() -> None:
    root = Context()
    parent_scope = root.scope.fork()
    owner = root.fork(scope=parent_scope)
    viewers = root._scope_viewers
    assert viewers is not None
    before = {scope: set(contexts) for scope, contexts in viewers.items()}
    child_scope = parent_scope.fork()

    child = root.fork(scope=child_scope)
    assert all(child in viewers[scope] for scope in child_scope.mro)
    child.dispose()
    assert viewers == before
    assert tuple(root._owned) == (owner,)
    root.dispose()


def test_context_disposal_cleans_scope_indexes_and_binding_data() -> None:
    class Payload:
        pass

    root = Context(schema=Schema({"value": Schema.leaf()}))
    child = root.fork(scope=root.scope.fork())
    payload = Payload()
    payload_ref = weakref.ref(payload)
    child.set("value", payload)
    del payload
    binding = next(iter(root._data.values()))
    identity_scopes = next(iter(binding._identity_scopes.values()))
    viewers = root._scope_viewers
    assert viewers is not None
    scope_viewers = viewers[child.scope]

    child.dispose()
    child.dispose()
    assert not scope_viewers
    assert not identity_scopes
    assert child.scope not in viewers
    assert viewers[root.scope] == {root}
    assert not binding._identity_scopes
    assert not binding._buckets
    gc.collect()
    assert payload_ref() is None
    root.dispose()
    assert not root._data


def test_binding_scope_release_is_idempotent() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    child = root.isolate("value", identity="shared")
    child.set("value", "old")
    binding = next(iter(root._data.values()))
    binding.release_scope(child.scope)
    binding.release_scope(child.scope)
    assert not binding._identity_scopes
    assert not binding._buckets
    child.set("value", "new")
    assert binding._identity_scopes["shared"] == {child.scope}
    assert child.get("value") == "new"
    root.dispose()


def test_scope_release_notifies_each_binding_for_each_expired_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context(schema=Schema({"first": Schema.leaf(), "second": Schema.leaf()}))
    parent_scope = root.scope.fork()
    child_scope = parent_scope.fork()
    child = root.fork(scope=child_scope)
    child.set("first", "child")
    child.set("second", "child")
    bindings = tuple(root._data.values())
    calls = []
    original = type(bindings[0]).release_scope

    def record(self, scope):
        calls.append((self, scope))
        original(self, scope)

    with monkeypatch.context() as patch:
        patch.setattr(type(bindings[0]), "release_scope", record)
        child.dispose()
    assert calls == [
        (binding, scope)
        for scope in (child_scope, parent_scope)
        for binding in bindings
    ]
    root.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("effect_failure", [False, True])
async def test_scope_cleanup_failure_finishes_other_bindings_and_scopes(
    monkeypatch: pytest.MonkeyPatch,
    asynchronous: bool,
    effect_failure: bool,
) -> None:
    root = Context(schema=Schema({"first": Schema.leaf(), "second": Schema.leaf()}))
    parent_scope = root.scope.fork()
    parent = root.fork(scope=parent_scope)
    parent.update({"first": "parent", "second": "parent"})
    child = root.fork(scope=parent_scope.fork())
    child.update({"first": "child", "second": "child"})
    parent.dispose()
    bindings = tuple(root._data.values())
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
    expected = primary if effect_failure else failures[0]
    with monkeypatch.context() as patch:
        patch.setattr(type(bindings[0]), "release_scope", fail)
        for _ in range(2):
            with pytest.raises(ValueError) as raised:
                if asynchronous:
                    await resolve(child.dispose())
                else:
                    child.dispose()
            assert raised.value is expected

    assert calls == [
        (binding, scope)
        for scope in (child.scope, parent_scope)
        for binding in bindings
    ]
    assert not root._owned
    assert root._scope_viewers is not None
    assert set(root._scope_viewers) == {root.scope}
    for binding in bindings:
        assert not binding._identity_scopes
        assert not binding._buckets
    with pytest.raises(RuntimeError, match="disposed"):
        child.get("first")
    root.dispose()
    assert not root._data


def test_failed_binding_restore_rolls_back_new_scope_viewers(monkeypatch) -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    identity = object()
    previous = root.isolate("value", identity=identity)
    scope = previous.scope
    previous.dispose()
    writer = root.isolate("value", identity=identity)
    writer.set("value", "live")
    binding = next(iter(root._data.values()))
    viewers = root._scope_viewers
    assert viewers is not None
    before = {scope: set(contexts) for scope, contexts in viewers.items()}
    original = type(binding).acquire_scopes

    def fail(self, scopes):
        original(self, scopes)
        raise ValueError("restore failed")

    with monkeypatch.context() as patch:
        patch.setattr(type(binding), "acquire_scopes", fail)
        with pytest.raises(ValueError, match="restore failed"):
            root.fork(scope=scope)
    assert viewers == before
    assert binding._identity_scopes[identity] == {writer.scope}
    assert writer.get("value") == "live"
    assert tuple(root._owned) == (writer,)
    root.dispose()


def test_scope_reacquired_during_value_finalization_keeps_remaining_bindings() -> None:
    root = Context(schema=Schema({"first": Schema.leaf(), "second": Schema.leaf()}))
    scope = root.scope.fork()
    writer = root.fork(scope=scope)
    readers = []

    class Payload:
        def __del__(self):
            readers.append(root.fork(scope=scope))

    writer.set("first", Payload())
    writer.set("second", "retained")
    writer.dispose()
    assert len(readers) == 1
    reader = readers[0]
    assert reader.get("second") == "retained"
    assert not reader.exists("first")
    root.dispose()


def test_value_finalizer_can_reuse_the_released_scope_and_identity() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    writer = root.isolate("value", identity="shared")
    scope = writer.scope
    readers = []

    class Payload:
        def __del__(self):
            reader = root.fork(scope=scope)
            reader.set("value", "new")
            readers.append(reader)

    writer.set("value", Payload())
    binding = next(iter(root._data.values()))
    old_scopes = binding._identity_scopes["shared"]
    writer.dispose()
    assert len(readers) == 1
    assert not old_scopes
    assert binding._identity_scopes["shared"] is not old_scopes
    assert binding._identity_scopes["shared"] == {scope}
    writer.dispose()
    assert readers[0].get("value") == "new"
    root.dispose()
    assert not binding._identity_scopes
    assert not binding._buckets


def test_failed_scope_acquisition_preserves_error_when_rollback_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    root.set("value", "live")
    binding = next(iter(root._data.values()))
    child_scope = root.scope.fork()
    acquisition_error = ValueError("restore failed")
    cleanup_error = ValueError("cleanup failed")
    original = type(binding).release_scope

    def fail_restore(self, scopes):
        raise acquisition_error

    def fail_release(self, scope):
        original(self, scope)
        if scope is child_scope:
            raise cleanup_error

    with monkeypatch.context() as patch:
        patch.setattr(type(binding), "acquire_scopes", fail_restore)
        patch.setattr(type(binding), "release_scope", fail_release)
        with pytest.raises(ValueError) as raised:
            root.fork(scope=child_scope)
        assert raised.value is acquisition_error
        assert raised.value.__cause__ is cleanup_error
    assert root._scope_viewers is not None
    assert set(root._scope_viewers) == {root.scope}
    assert not root._owned
    assert root.get("value") == "live"
    root.dispose()


def test_bound_identity_data_survives_until_its_last_viewer_is_disposed() -> None:
    class Payload:
        pass

    schema = Schema({"value": Schema.leaf()})
    root = Context(schema=schema)
    identity = object()
    writer = root.isolate("value", identity=identity)
    reader = root.isolate("value", identity=identity)
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


def test_bound_identity_keeps_add_owned_by_its_context() -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context(schema=schema)
    identity = object()
    owner = root.isolate("value", identity=identity)
    viewer = root.isolate("value", identity=identity)
    owner.add("value", "temporary")

    assert viewer.get("value") == "temporary"
    owner.dispose()
    assert not viewer.exists("value")
    viewer.dispose()
    root.dispose()


def test_identity_index_tracks_isolated_scopes_after_value_deletion() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    identity = object()
    writer = root.isolate("value", identity=identity)
    viewer = root.isolate("value", identity=identity)
    binding = next(iter(root._data.values()))

    scopes = binding._identity_scopes[identity]
    assert scopes == {writer.scope, viewer.scope}

    writer.set("value", "first")
    writer.delete("value")
    assert not writer.exists("value")
    assert not viewer.exists("value")
    assert binding._identity_scopes[identity] is scopes
    writer.set("value", "second")
    writer.dispose()
    assert scopes == {viewer.scope}
    assert viewer.get("value") == "second"

    viewer.dispose()
    assert not scopes
    assert not binding._identity_scopes
    assert not binding._buckets
    assert binding._scope_identities[writer.scope] is identity
    root.dispose()


def test_identity_reuse_starts_new_ownership_without_reviving_old_data() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    identity = object()
    first = root.isolate("value", identity=identity)
    first.set("value", "old")
    binding = next(iter(root._data.values()))
    old = binding._identity_scopes[identity]
    first.dispose()
    assert not old

    second = root.isolate("value", identity=identity)
    assert binding._identity_scopes[identity] is not old
    assert not second.exists("value")
    second.set("value", "new")
    first.dispose()
    assert second.get("value") == "new"
    root.dispose()
    assert not binding._identity_scopes


def test_schema_disposal_releases_binding_with_its_scope_set_still_referenced() -> None:
    class Payload:
        pass

    root = Context()
    remove_schema = root.declare({"value": Schema.leaf()})
    payload = Payload()
    payload_ref = weakref.ref(payload)
    root.add("value", payload)
    binding = next(iter(root._data.values()))
    binding_ref = weakref.ref(binding)
    scopes = next(iter(binding._identity_scopes.values()))
    del binding, payload

    remove_schema()
    gc.collect()
    assert not root._data
    assert binding_ref() is None
    assert payload_ref() is None
    assert scopes == {root.scope}
    root.dispose()


@pytest.mark.parametrize("descendant", [False, True])
@pytest.mark.parametrize("reader_first", [False, True])
def test_reused_scope_protects_identity_without_reading_or_binding_again(
    descendant: bool,
    reader_first: bool,
) -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    identity = object()
    previous = root.isolate("value", identity=identity)
    saved_scope = previous.scope
    previous.set("value", "old")
    previous.dispose()
    binding = next(iter(root._data.values()))
    assert not binding._identity_scopes
    assert not binding._buckets

    reader_scope = saved_scope.fork() if descendant else saved_scope
    if reader_first:
        reader = root.fork(scope=reader_scope)
    writer = root.isolate("value", identity=identity)
    writer.set("value", "new")
    if not reader_first:
        reader = root.fork(scope=reader_scope)

    writer.dispose()
    assert binding._identity_scopes[identity] == {saved_scope}
    assert reader.get("value") == "new"
    reader.dispose()
    assert not binding._identity_scopes
    assert not binding._buckets
    root.dispose()


def test_identity_index_keeps_both_observed_parents_of_a_diamond_scope() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    identity = object()
    left = root.isolate("value", identity=identity)
    right = root.isolate("value", identity=identity)
    reader = root.fork(scope=Scope(parents=(left.scope, right.scope)))
    left.set("value", "shared")
    binding = next(iter(root._data.values()))

    left.dispose()
    right.dispose()
    assert binding._identity_scopes[identity] == {left.scope, right.scope}
    assert reader.get("value") == "shared"
    reader.dispose()
    assert not binding._identity_scopes
    assert not binding._buckets
    root.dispose()


def test_scope_release_does_not_scan_other_identity_bindings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    sessions = [root.fork(scope=root.scope.fork()) for _ in range(1000)]
    for number, session in enumerate(sessions):
        session.set("value", number)
    binding = next(iter(root._data.values()))
    scope_refs = [weakref.ref(session.scope) for session in sessions]
    assert len(binding._identity_scopes) == 1000

    def reject_scan():
        pytest.fail("Scope release must not scan every Scope-to-identity binding.")

    monkeypatch.setattr(binding._scope_identities, "items", reject_scan)
    sessions[0].dispose()
    assert len(binding._identity_scopes) == 999
    assert sessions[-1].get("value") == 999
    for session in sessions[1:]:
        session.dispose()
    assert not binding._identity_scopes
    assert not binding._buckets

    del session
    sessions.clear()
    gc.collect()
    assert all(scope_ref() is None for scope_ref in scope_refs)
    assert not binding._scope_identities
    root.dispose()


def test_isolated_contexts_can_share_one_private_identity() -> None:
    schema = Schema({"service": Schema.leaf()})
    root = Context({"service": "root"}, schema=schema)
    identity = object()
    left = root.isolate("service", identity=identity)
    right = root.isolate("service", identity=identity)

    assert not left.exists("service")
    assert not right.exists("service")
    left.set("service", "isolated")
    assert right.get("service") == "isolated"

    left.dispose()
    assert right.get("service") == "isolated"
    right.dispose()

    later = root.isolate("service", identity=identity)
    assert not later.exists("service")
    later.dispose()
    root.dispose()


def test_shared_scope_distinguishes_set_and_add_ownership() -> None:
    schema = Schema(
        {
            "set_value": Schema.leaf(),
            "added_value": Schema.leaf(),
            "replaced_value": Schema.leaf(),
        }
    )
    root = Context(schema=schema)
    shared_scope = root.scope.fork()
    owner = root.fork(scope=shared_scope)
    viewer = root.fork(scope=shared_scope)

    owner.set("set_value", "scope-owned")
    owner.add("added_value", "context-owned")
    owner.add("replaced_value", "old")
    viewer.set("replaced_value", "new")

    owner.dispose()
    assert viewer.get("set_value") == "scope-owned"
    assert not viewer.exists("added_value")
    assert viewer.get("replaced_value") == "new"

    viewer.dispose()
    late_viewer = root.fork(scope=shared_scope)
    assert not late_viewer.exists("set_value")
    assert not late_viewer.exists("added_value")
    assert not late_viewer.exists("replaced_value")
    late_viewer.dispose()
    root.dispose()


def test_descendant_scope_keeps_ancestor_data_visible() -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context(schema=schema)
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
    left = Context(schema=schema)
    right = Context(schema=schema, scope=left.scope)
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
    ctx = Context({"value": 1}, schema=schema)
    ctx.dispose()

    for operation in (
        lambda: ctx.get("value"),
        lambda: ctx.extract({}),
        lambda: ctx.set("value", 2),
        lambda: ctx.fork(),
        lambda: ctx.effect(lambda: lambda: None),
    ):
        with pytest.raises(RuntimeError, match="disposed"):
            operation()


def test_cleanup_can_read_context_but_cannot_start_new_mutation() -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context({"value": 1}, schema=schema)
    observed: list[int] = []

    def cleanup() -> None:
        observed.append(ctx.get("value"))
        with pytest.raises(RuntimeError, match="being disposed"):
            ctx.set("value", 2)

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
    assert ctx.dispose() is pending

    asyncio.run(resolve(pending))
    assert events == ["async", "sync"]


async def test_async_dispose_is_shared_and_runs_cleanup_once() -> None:
    calls = 0
    ctx = Context()

    async def cleanup() -> None:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1

    ctx.effect(lambda: cleanup)
    await asyncio.gather(resolve(ctx.dispose()), resolve(ctx.dispose()))
    await resolve(ctx.dispose())
    assert calls == 1


@pytest.mark.parametrize(
    "rewait_before_cleanup_finishes",
    [True, False],
    ids=["before-finish", "after-finish"],
)
async def test_cancelled_dispose_waiter_can_reobserve_late_cleanup_failure(
    rewait_before_cleanup_finishes: bool,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    ctx = Context()

    async def cleanup() -> None:
        started.set()
        await release.wait()
        raise ValueError("late cleanup failure")

    ctx.effect(lambda: cleanup)
    first_waiter = asyncio.create_task(resolve(ctx.dispose()))
    await started.wait()
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    if rewait_before_cleanup_finishes:
        repeated = asyncio.create_task(resolve(ctx.dispose()))
        await asyncio.sleep(0)
        assert not repeated.done()
        release.set()
    else:
        release.set()
        assert ctx._dispose_pending is not None
        disposal_task = ctx._dispose_pending._task
        assert disposal_task is not None
        await asyncio.wait({disposal_task})
        repeated = asyncio.create_task(resolve(ctx.dispose()))

    with pytest.raises(ValueError, match="late cleanup failure"):
        await repeated
    with pytest.raises(ValueError, match="late cleanup failure"):
        await resolve(ctx.dispose())


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
    task = asyncio.create_task(resolve(pending))
    await started.wait()
    assert ctx.dispose() is pending
    release.set()
    await task


async def test_async_cleanup_cannot_reenter_owner_disposal() -> None:
    ctx = Context()

    async def cleanup() -> None:
        await resolve(ctx.dispose())

    ctx.effect(lambda: cleanup)
    with pytest.raises(RuntimeError, match="cannot be re-entered"):
        await resolve(ctx.dispose())


async def test_early_async_effect_disposal_cannot_dispose_its_owner() -> None:
    ctx = Context()
    dispose_effect = None

    async def cleanup() -> None:
        assert dispose_effect is not None
        await resolve(ctx.dispose())

    dispose_effect = ctx.effect(lambda: cleanup)
    with pytest.raises(RuntimeError, match="cannot be re-entered"):
        await dispose_effect()
    await resolve(ctx.dispose())


@pytest.mark.parametrize("dispose_ancestor", [False, True])
async def test_async_early_cleanup_blocks_owner_and_ancestor_disposal(
    dispose_ancestor: bool,
) -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context({"value": 1}, schema=schema)
    child = root.fork()
    target = root if dispose_ancestor else child
    events: list[object] = []

    async def cleanup() -> None:
        events.append("start")
        with pytest.raises(RuntimeError, match="setup or cleanup"):
            target.dispose()
        with pytest.raises(RuntimeError, match="setup or cleanup"):
            await resolve(target.dispose())
        events.append(child.get("value"))

    dispose_effect = child.effect(lambda: cleanup)
    await dispose_effect()
    assert events == ["start", 1]
    root.dispose()


@pytest.mark.parametrize("dispose_ancestor", [False, True])
def test_sync_effect_setup_blocks_owner_and_ancestor_disposal(
    dispose_ancestor: bool,
) -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context({"value": 1}, schema=schema)
    child = root.fork()
    target = root if dispose_ancestor else child
    events: list[str] = []

    def setup():
        events.append("acquire")
        with pytest.raises(RuntimeError, match="setup or cleanup"):
            target.dispose()
        assert child.get("value") == 1
        return lambda: events.append("release")

    dispose_effect = child.effect(setup)
    dispose_effect()
    assert events == ["acquire", "release"]
    root.dispose()


@pytest.mark.parametrize("dispose_ancestor", [False, True])
async def test_async_effect_setup_blocks_owner_and_ancestor_disposal(
    dispose_ancestor: bool,
) -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context({"value": 1}, schema=schema)
    child = root.fork()
    target = root if dispose_ancestor else child
    events: list[str] = []

    async def cleanup() -> None:
        events.append("release")

    def setup():
        events.append("acquire")
        with pytest.raises(RuntimeError, match="setup or cleanup"):
            target.dispose()
        assert child.get("value") == 1
        return cleanup

    dispose_effect = child.effect(setup)
    await dispose_effect()
    assert events == ["acquire", "release"]
    root.dispose()


@pytest.mark.parametrize("dispose_ancestor", [False, True])
def test_sync_early_cleanup_blocks_owner_and_ancestor_disposal(
    dispose_ancestor: bool,
) -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context({"value": 1}, schema=schema)
    child = root.fork()
    target = root if dispose_ancestor else child
    dispose_effect = None
    events: list[object] = []

    def cleanup() -> None:
        assert dispose_effect is not None
        events.append("start")
        with pytest.raises(RuntimeError, match="setup or cleanup"):
            target.dispose()
        with pytest.raises(RuntimeError, match="cannot be re-entered"):
            dispose_effect()
        events.append(child.get("value"))

    dispose_effect = child.effect(lambda: cleanup)
    dispose_effect()
    assert events == ["start", 1]
    root.dispose()


async def test_async_effect_disposal_cannot_await_itself() -> None:
    ctx = Context()
    dispose_effect = None

    async def cleanup() -> None:
        assert dispose_effect is not None
        await dispose_effect()

    dispose_effect = ctx.effect(lambda: cleanup)
    with pytest.raises(RuntimeError, match="cannot await its own"):
        await resolve(ctx.dispose())


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
    waiter = asyncio.create_task(resolve(ctx.dispose()))
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not finished.is_set()

    release.set()
    await resolve(ctx.dispose())
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

    with pytest.raises(ValueError, match="cleanup failed"):
        await resolve(ctx.dispose())
    with pytest.raises(ValueError, match="cleanup failed"):
        await resolve(ctx.dispose())
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

    with pytest.raises(asyncio.CancelledError):
        await resolve(ctx.dispose())
    with pytest.raises(asyncio.CancelledError):
        await resolve(ctx.dispose())
    assert events == ["last", "cancel", "first"]


def test_parent_disposal_blocks_new_effects_in_active_children() -> None:
    root = Context()
    child = root.fork()

    async def async_cleanup() -> None:
        pass

    root.effect(lambda: lambda: child.effect(lambda: async_cleanup))

    with pytest.raises(RuntimeError, match="ancestor Context is being disposed"):
        root.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        child.fork()


def test_cleanup_failures_do_not_skip_remaining_cleanup_or_scope_release() -> None:
    schema = Schema({"value": Schema.leaf()})
    ctx = Context({"value": object()}, schema=schema)
    events: list[str] = []
    ctx.effect(lambda: lambda: events.append("first"))

    def fail() -> None:
        events.append("fail")
        raise RuntimeError("cleanup failed")

    ctx.effect(lambda: fail)
    ctx.effect(lambda: lambda: events.append("last"))

    with pytest.raises(RuntimeError, match="cleanup failed"):
        ctx.dispose()
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
    with pytest.raises(ValueError, match="cleanup failed"):
        ctx.dispose()
    with pytest.raises(ValueError, match="cleanup failed"):
        ctx.dispose()
    assert calls == 1


def test_final_schema_removal_releases_an_owned_add_payload() -> None:
    class Payload:
        pass

    ctx = Context()
    remove_declaration = ctx.declare({"temporary": Schema.leaf()})
    payload = Payload()
    payload_ref = weakref.ref(payload)
    ctx.add("temporary", payload)
    del payload

    remove_declaration()
    gc.collect()

    assert payload_ref() is None
    with pytest.raises(KeyError, match="temporary"):
        ctx.schema.resolve("temporary")

    remove_redeclaration = ctx.declare({"temporary": Schema.leaf()})
    assert not ctx.exists("temporary")
    remove_redeclaration()
    ctx.dispose()
