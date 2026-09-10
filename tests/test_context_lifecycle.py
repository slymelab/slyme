from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from slyme.context import Compose, Context, Schema, Scope
from slyme.context.core import ContextPathError


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


def test_context_owns_add_declare_and_contribute() -> None:
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
    ctx.contribute("hooks", "first", metadata={"owner": "test"})
    ctx.contribute("hooks", "zeroth", position="prepend")

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


def test_context_contribute_can_target_an_explicit_scope() -> None:
    schema = Schema({"hooks": Schema.leaf()})
    ctx = Context(schema=schema)
    hooks = Compose[str, tuple[str, ...]].collect()
    ctx.add("hooks", hooks)
    other = ctx.scope.fork()

    dispose = ctx.contribute("hooks", "other", scope=other)
    assert hooks.resolve(ctx.scope) == ()
    assert hooks.resolve(other) == ("other",)
    dispose()
    assert hooks.resolve(other) == ()

    ctx.set("hooks", object())
    with pytest.raises(TypeError, match="does not hold Compose"):
        ctx.contribute("hooks", "invalid")
    ctx.dispose()


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


def test_scope_viewers_record_the_exact_live_contexts() -> None:
    root = Context()
    child_scope = root.scope.fork()
    child = root.fork(scope=child_scope)
    sibling = root.fork()
    viewers = root._scope_viewers
    assert viewers is not None

    assert viewers[root.scope] == {root, child, sibling}
    assert viewers[child_scope] == {child}
    child.dispose()
    assert child_scope not in viewers
    assert viewers[root.scope] == {root, sibling}
    sibling.dispose()
    root.dispose()
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


def test_bound_identity_data_survives_until_its_last_viewer_is_disposed() -> None:
    class Payload:
        pass

    schema = Schema({"value": Schema.leaf()})
    root = Context(schema=schema)
    left_scope = root.scope.fork()
    right_scope = root.scope.fork()
    writer = root.fork(scope=left_scope)
    reader = root.fork(scope=right_scope)
    identity = object()
    writer.bind("value", identity=identity)
    reader.bind("value", identity=identity)
    payload = Payload()
    payload_ref = weakref.ref(payload)
    writer.set("value", payload)

    writer.dispose()
    assert reader.get("value") is payload
    del payload
    reader.dispose()
    gc.collect()

    assert payload_ref() is None
    late_reader = root.fork(scope=right_scope)
    late_reader.bind("value", identity=identity)
    assert not late_reader.exists("value")
    late_reader.dispose()
    root.dispose()


def test_bound_identity_keeps_add_owned_by_its_context() -> None:
    schema = Schema({"value": Schema.leaf()})
    root = Context(schema=schema)
    owner = root.fork(scope=root.scope.fork())
    viewer = root.fork(scope=root.scope.fork())
    identity = object()
    owner.bind("value", identity=identity)
    viewer.bind("value", identity=identity)
    owner.add("value", "temporary")

    assert viewer.get("value") == "temporary"
    owner.dispose()
    assert not viewer.exists("value")
    viewer.dispose()
    root.dispose()


def test_identity_index_tracks_observed_scopes_even_without_values() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    writer = root.fork(scope=root.scope.fork())
    viewer = root.fork(scope=root.scope.fork())
    identity = object()
    writer.bind("value", identity=identity)
    viewer.bind("value", identity=identity)
    viewer.bind("value", identity=identity)
    binding = next(iter(root._data.values()))

    assert binding._identity_scopes == {identity: {writer.scope, viewer.scope}}
    with pytest.raises(ValueError, match="cannot be rebound"):
        writer.bind("value", identity=object())
    assert binding._identity_scopes == {identity: {writer.scope, viewer.scope}}

    writer.set("value", "first")
    writer.delete("value")
    assert not binding._buckets
    assert binding._identity_scopes == {identity: {writer.scope, viewer.scope}}
    writer.set("value", "second")
    writer.dispose()
    assert binding._identity_scopes == {identity: {viewer.scope}}
    assert viewer.get("value") == "second"

    viewer.dispose()
    assert not binding._identity_scopes
    assert not binding._buckets
    assert binding._scope_identities[writer.scope] is identity
    root.dispose()


@pytest.mark.parametrize("descendant", [False, True])
@pytest.mark.parametrize("reader_first", [False, True])
def test_reused_scope_protects_identity_without_reading_or_binding_again(
    descendant: bool,
    reader_first: bool,
) -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    saved_scope = root.scope.fork()
    previous = root.fork(scope=saved_scope)
    identity = object()
    previous.bind("value", identity=identity)
    previous.set("value", "old")
    previous.dispose()
    binding = next(iter(root._data.values()))
    assert not binding._identity_scopes
    assert not binding._buckets

    reader_scope = saved_scope.fork() if descendant else saved_scope
    if reader_first:
        reader = root.fork(scope=reader_scope)
    writer = root.fork(scope=root.scope.fork())
    writer.bind("value", identity=identity)
    writer.set("value", "new")
    if not reader_first:
        reader = root.fork(scope=reader_scope)

    writer.dispose()
    assert binding._identity_scopes == {identity: {saved_scope}}
    assert reader.get("value") == "new"
    reader.dispose()
    assert not binding._identity_scopes
    assert not binding._buckets
    root.dispose()


def test_identity_index_keeps_both_observed_parents_of_a_diamond_scope() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    left = root.fork(scope=root.scope.fork())
    right = root.fork(scope=root.scope.fork())
    reader = root.fork(scope=Scope(parents=(left.scope, right.scope)))
    identity = object()
    left.bind("value", identity=identity)
    right.bind("value", identity=identity)
    left.set("value", "shared")
    binding = next(iter(root._data.values()))

    left.dispose()
    right.dispose()
    assert binding._identity_scopes == {identity: {left.scope, right.scope}}
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


def test_sync_dispose_preflights_async_cleanup_without_partial_teardown() -> None:
    events: list[str] = []
    ctx = Context()
    ctx.effect(lambda: lambda: events.append("sync"))
    child = ctx.fork()

    async def cleanup() -> None:
        events.append("async")

    child.async_effect(lambda: cleanup)
    with pytest.raises(RuntimeError, match="async_dispose"):
        ctx.dispose()
    assert events == []

    asyncio.run(ctx.async_dispose())
    assert events == ["async", "sync"]


async def test_async_dispose_is_shared_and_runs_cleanup_once() -> None:
    calls = 0
    ctx = Context()

    async def cleanup() -> None:
        nonlocal calls
        await asyncio.sleep(0)
        calls += 1

    ctx.async_effect(lambda: cleanup)
    await asyncio.gather(ctx.async_dispose(), ctx.async_dispose())
    await ctx.async_dispose()
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

    ctx.async_effect(lambda: cleanup)
    first_waiter = asyncio.create_task(ctx.async_dispose())
    await started.wait()
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    if rewait_before_cleanup_finishes:
        repeated = asyncio.create_task(ctx.async_dispose())
        await asyncio.sleep(0)
        assert not repeated.done()
        release.set()
    else:
        release.set()
        disposal_task = ctx._dispose_task
        assert disposal_task is not None
        await asyncio.wait({disposal_task})
        repeated = asyncio.create_task(ctx.async_dispose())

    with pytest.raises(ValueError, match="late cleanup failure"):
        await repeated
    with pytest.raises(ValueError, match="late cleanup failure"):
        await ctx.async_dispose()


async def test_async_dispose_marks_context_before_scheduling_cleanup() -> None:
    started = asyncio.Event()
    release = asyncio.Event()

    class PausingContext(Context):
        async def _run_async_dispose(self) -> None:
            started.set()
            await release.wait()
            await super()._run_async_dispose()

    ctx = PausingContext()
    task = asyncio.create_task(ctx.async_dispose())
    await started.wait()

    with pytest.raises(RuntimeError, match="already in progress"):
        ctx.dispose()

    release.set()
    await task


async def test_async_cleanup_cannot_reenter_owner_disposal() -> None:
    ctx = Context()

    async def cleanup() -> None:
        await ctx.async_dispose()

    ctx.async_effect(lambda: cleanup)
    with pytest.raises(RuntimeError, match="cannot be re-entered"):
        await ctx.async_dispose()


async def test_early_async_effect_disposal_cannot_dispose_its_owner() -> None:
    ctx = Context()
    dispose_effect = None

    async def cleanup() -> None:
        assert dispose_effect is not None
        await ctx.async_dispose()

    dispose_effect = ctx.async_effect(lambda: cleanup)
    with pytest.raises(RuntimeError, match="cannot be re-entered"):
        await dispose_effect()
    await ctx.async_dispose()


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
            await target.async_dispose()
        events.append(child.get("value"))

    dispose_effect = child.async_effect(lambda: cleanup)
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

    dispose_effect = child.async_effect(setup)
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

    dispose_effect = ctx.async_effect(lambda: cleanup)
    with pytest.raises(RuntimeError, match="cannot await itself"):
        await ctx.async_dispose()


async def test_cancelling_a_dispose_waiter_does_not_cancel_cleanup() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()
    ctx = Context()

    async def cleanup() -> None:
        started.set()
        await release.wait()
        finished.set()

    ctx.async_effect(lambda: cleanup)
    waiter = asyncio.create_task(ctx.async_dispose())
    await started.wait()
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert not finished.is_set()

    release.set()
    await ctx.async_dispose()
    assert finished.is_set()


async def test_async_cleanup_failure_does_not_skip_remaining_cleanup() -> None:
    events: list[str] = []
    ctx = Context()

    async def cleanup(name: str) -> None:
        events.append(name)

    async def fail() -> None:
        events.append("fail")
        raise ValueError("cleanup failed")

    ctx.async_effect(lambda: lambda: cleanup("first"))
    ctx.async_effect(lambda: fail)
    ctx.async_effect(lambda: lambda: cleanup("last"))

    with pytest.raises(ValueError, match="cleanup failed"):
        await ctx.async_dispose()
    with pytest.raises(ValueError, match="cleanup failed"):
        await ctx.async_dispose()
    assert events == ["last", "fail", "first"]


async def test_cancelled_async_cleanup_does_not_skip_remaining_cleanup() -> None:
    events: list[str] = []
    ctx = Context()

    async def cleanup(name: str) -> None:
        events.append(name)

    async def cancel() -> None:
        events.append("cancel")
        raise asyncio.CancelledError

    ctx.async_effect(lambda: lambda: cleanup("first"))
    ctx.async_effect(lambda: cancel)
    ctx.async_effect(lambda: lambda: cleanup("last"))

    with pytest.raises(asyncio.CancelledError):
        await ctx.async_dispose()
    with pytest.raises(asyncio.CancelledError):
        await ctx.async_dispose()
    assert events == ["last", "cancel", "first"]


def test_parent_disposal_blocks_new_effects_in_active_children() -> None:
    root = Context()
    child = root.fork()

    async def async_cleanup() -> None:
        pass

    root.effect(lambda: lambda: child.async_effect(lambda: async_cleanup))

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
