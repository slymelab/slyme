import asyncio
import gc
import weakref
from dataclasses import FrozenInstanceError

import pytest

from slyme.context import (
    Context,
    ContextStore,
    Identity,
    Schema,
    Scope,
    ScopeBinding,
)
from slyme.context.lifecycle import Lifecycle, _Effect, _LifecycleState
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result


def test_lifecycle_disposal_visits_each_descendant_once(monkeypatch) -> None:
    checked: list[Context] = []

    class TrackedContext(Context):
        def dispose(self):
            checked.append(self)
            return super().dispose()

    root = TrackedContext()
    left = root.fork()
    grandchild = left.fork()
    right = root.fork()
    states = {ctx: [] for ctx in (root, left, grandchild, right)}
    set_state = Lifecycle._set_state

    def track_state(lifecycle, state):
        states[lifecycle.ctx].append(state)
        set_state(lifecycle, state)

    monkeypatch.setattr(Lifecycle, "_set_state", track_state)
    root.dispose()
    assert checked == [root, right, left, grandchild]
    assert all(
        transitions
        == [
            _LifecycleState.DISPOSE_PENDING,
            _LifecycleState.DISPOSING,
            _LifecycleState.DISPOSED,
        ]
        for transitions in states.values()
    )


def test_context_children_snapshot_tracks_ownership_not_scope_ancestry() -> None:
    root = Context()
    first = root.fork()
    second = root.fork(scope=Scope())
    grandchild = first.fork(scope=second.scope)
    snapshot = root.children
    assert snapshot == (first, second)
    assert first.children == (grandchild,)
    assert second.children == ()
    assert grandchild._lifecycle.ctx.parent is first
    assert set(root._children.values()).issubset(root._lifecycle._effects)
    with pytest.raises(FrozenInstanceError):
        first.parent = second
    first.dispose()
    assert snapshot == (first, second)
    assert root.children == (second,)
    assert first.children == ()
    assert first.parent is root
    second._lifecycle.assert_active()
    root.dispose()
    assert root.children == ()


def test_early_child_disposal_releases_parent_references() -> None:
    root = Context()
    initial_effects = tuple(root._lifecycle._effects)
    child = root.fork()
    reference = weakref.ref(child)
    child.dispose()
    assert not root.children
    assert tuple(root._lifecycle._effects) == initial_effects
    del child
    gc.collect()
    assert reference() is None
    root.dispose()


@pytest.mark.parametrize("fails", [False, True])
@pytest.mark.parametrize("dispose_mode", ["sequential", "batch"])
async def test_parent_keeps_child_until_its_cancelled_waiter_cleanup_finishes(
    fails, dispose_mode
):
    root = Context(dispose_mode=dispose_mode)
    child = root.fork()
    started, finish = asyncio.Event(), asyncio.Event()
    failure = ValueError("child cleanup")

    async def cleanup():
        started.set()
        await finish.wait()
        if fails:
            raise failure

    child.effect(lambda: cleanup)
    early = asyncio.create_task(await_result(child.dispose()))
    await started.wait()
    early.cancel()
    with pytest.raises(asyncio.CancelledError):
        await early
    assert child._lifecycle._state is _LifecycleState.DISPOSING
    disposing = asyncio.create_task(await_result(root.dispose()))
    assert child._lifecycle._state is _LifecycleState.DISPOSING
    await asyncio.sleep(0)
    assert root.children == (child,)
    assert root._children[child] in root._lifecycle._effects
    assert not disposing.done()
    finish.set()
    if fails:
        with pytest.raises(BaseExceptionGroup) as caught:
            await disposing
        assert caught.value.exceptions[0].exceptions == (failure,)
    else:
        await disposing
    assert not root.children
    assert not root._lifecycle._effects
    assert not root._schema._stores


def test_lifecycle_finalizes_after_owned_cleanup_even_on_failure() -> None:
    events = []
    failure = ValueError("cleanup")

    class TrackedContext(Context):
        def _finalize(self):
            super()._finalize()
            events.append("finalize" if self.parent is None else "child")

    ctx = TrackedContext()
    lifetime = ctx._lifecycle
    lifetime.effect(lambda: lambda: events.append("first"))
    child = ctx.fork()

    def fail():
        events.append("failure")
        raise failure

    lifetime.effect(lambda: fail)
    with pytest.raises(BaseExceptionGroup) as caught:
        lifetime.dispose()
    assert caught.value.exceptions == (failure,)
    assert events == ["failure", "child", "first", "finalize"]
    assert not lifetime._effects
    assert not ctx.children
    child.dispose()
    with pytest.raises(BaseExceptionGroup) as repeated:
        lifetime.dispose()
    assert repeated.value is caught.value


def test_lifecycle_finalize_failure_is_retained_without_repeating_cleanup() -> None:
    calls = []
    failure = ValueError("finalize")

    class FailingContext(Context):
        def _finalize(self):
            super()._finalize()
            calls.append("finalize")
            raise failure

    lifetime = FailingContext()._lifecycle
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            lifetime.dispose()
        assert caught.value is failure
        with pytest.raises(ValueError) as finalized:
            lifetime.ctx._finalize()
        assert finalized.value is failure
    assert calls == ["finalize"]
    with pytest.raises(RuntimeError, match="disposed"):
        lifetime.assert_readable()


def test_child_and_parent_share_once_only_finalization(monkeypatch) -> None:
    effects, viewers = [], []
    finalize = _Effect.finalize
    release_scope = ContextStore.release_scope

    def record_finalize(effect):
        effects.append(effect)
        finalize(effect)

    def record_release(store, viewer, scope):
        viewers.append(viewer)
        release_scope(store, viewer, scope)

    monkeypatch.setattr(_Effect, "finalize", record_finalize)
    monkeypatch.setattr(ContextStore, "release_scope", record_release)
    root = Context()
    child = root.fork()
    child_effect = root._children[child]
    root.dispose()
    child_effect.finalize()
    child._finalize()
    root._finalize()
    assert effects.count(child_effect) == 1
    assert viewers == [child, root]
    assert not root.children
    assert not root._lifecycle._effects


async def test_failed_setup_and_disposal_share_once_only_finalization(
    monkeypatch,
) -> None:
    calls = []
    finalize = _Effect.finalize

    def record(effect):
        calls.append(effect)
        finalize(effect)

    monkeypatch.setattr(_Effect, "finalize", record)
    ctx = Context()
    failure = ValueError("setup failed")

    async def setup():
        await asyncio.sleep(0)
        raise failure

    registration = ctx.effect(setup)
    effect = next(reversed(ctx._lifecycle._effects))
    with pytest.raises(BaseExceptionGroup) as caught:
        await await_result(ctx.dispose())
    assert caught.value.exceptions == (failure,)
    with pytest.raises(ValueError) as setup_error:
        await await_result(registration)
    assert setup_error.value is failure
    effect.finalize()
    assert calls.count(effect) == 1
    assert not ctx._lifecycle._effects


def test_effect_finalization_failure_is_retained_without_retry(monkeypatch) -> None:
    calls = []
    finalize = _Effect.finalize
    failure = ValueError("finalize")
    ctx = Context()

    def fail(effect):
        calls.append(effect)
        finalize(effect)
        raise failure

    monkeypatch.setattr(_Effect, "finalize", fail)
    cleanup_calls = []
    dispose = ctx.effect(lambda: lambda: cleanup_calls.append("cleanup"))
    effect = next(reversed(ctx._lifecycle._effects))
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            dispose()
        assert caught.value is failure
        with pytest.raises(ValueError) as finalized:
            effect.finalize()
        assert finalized.value is failure
    assert calls == [effect]
    assert cleanup_calls == ["cleanup"]
    ctx.dispose()


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_completed_setup_does_not_retain_its_callback(asynchronous) -> None:
    cleanup_calls = []

    def cleanup():
        cleanup_calls.append("cleanup")

    class Setup:
        def __call__(self):
            if asynchronous:
                return self.finish()
            return cleanup

        async def finish(self):
            await asyncio.sleep(0)
            return cleanup

    ctx = Context()
    setup = Setup()
    reference = weakref.ref(setup)
    registration = ctx.effect(setup)
    dispose = await await_result(registration)
    del setup
    gc.collect()
    assert reference() is None
    assert not cleanup_calls
    await await_result(dispose())
    await await_result(ctx.dispose())
    assert cleanup_calls == ["cleanup"]


def test_scope_viewer_requires_one_release_per_acquisition() -> None:
    store = ContextStore(Schema())
    scope = Scope()
    viewer = object()
    for _ in range(2):
        store.acquire_scope(viewer, scope)
        store.release_scope(viewer, scope)
        with pytest.raises(KeyError):
            store.release_scope(viewer, scope)
    store.dispose()


async def test_lifecycle_waiters_share_cleanup_and_finalize_after_it() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    events = []

    class TrackedContext(Context):
        def _finalize(self):
            super()._finalize()
            events.append("finalize")

    ctx = TrackedContext()
    lifetime = ctx._lifecycle

    async def cleanup():
        started.set()
        await finish.wait()
        events.append("cleanup")

    lifetime.effect(lambda: cleanup)
    first = asyncio.create_task(await_result(lifetime.dispose()))
    await started.wait()
    lifetime.assert_readable()
    with pytest.raises(RuntimeError, match="disposed"):
        ctx.fork()
    with pytest.raises(RuntimeError, match="disposed"):
        lifetime.effect(lambda: pytest.fail("setup must not run"))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(await_result(lifetime.dispose()))
    finish.set()
    await second
    assert events == ["cleanup", "finalize"]
    await await_result(lifetime.dispose())


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_disposers_execute_once_per_lifetime_and_per_effect(asynchronous) -> None:
    events = []

    class TrackedContext(Context):
        def _finalize(self):
            super()._finalize()
            events.append(
                "left:finalize" if self._lifecycle is left else "right:finalize"
            )

    left = TrackedContext()._lifecycle
    right = TrackedContext()._lifecycle

    async def pending(name):
        await asyncio.sleep(0)
        events.append(name)

    def cleanup(name):
        if asynchronous:
            return pending(name)
        events.append(name)

    first = left.effect(lambda: lambda: cleanup("left:first"))
    left.effect(lambda: lambda: cleanup("left:second"))
    right.effect(lambda: lambda: cleanup("right"))
    await await_result(first())
    await await_result(first())
    await await_result(right.dispose())
    await await_result(left.dispose())
    await await_result(right.dispose())
    await await_result(left.dispose())
    assert events == [
        "left:first",
        "right",
        "right:finalize",
        "left:second",
        "left:finalize",
    ]


@pytest.mark.parametrize(
    "rewait_before_cleanup_finishes",
    [True, False],
    ids=["before-finish", "after-finish"],
)
@pytest.mark.parametrize("dispose_mode", ["sequential", "batch"])
async def test_cancelled_dispose_waiter_can_reobserve_late_cleanup_failure(
    rewait_before_cleanup_finishes: bool,
    dispose_mode,
) -> None:
    started, release, finalized = asyncio.Event(), asyncio.Event(), asyncio.Event()

    class TrackedContext(Context):
        def _finalize(self):
            super()._finalize()
            finalized.set()

    lifetime = TrackedContext(dispose_mode=dispose_mode)._lifecycle

    async def cleanup() -> None:
        started.set()
        await release.wait()
        raise ValueError("late cleanup failure")

    lifetime.effect(lambda: cleanup)
    completion = lifetime.dispose()
    first_waiter = asyncio.create_task(await_result(completion))
    await started.wait()
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    if rewait_before_cleanup_finishes:
        repeated = asyncio.create_task(await_result(lifetime.dispose()))
        await asyncio.sleep(0)
        assert not repeated.done()
        release.set()
    else:
        release.set()
        await finalized.wait()
        repeated = asyncio.create_task(await_result(lifetime.dispose()))

    with pytest.raises(BaseExceptionGroup) as caught:
        await repeated
    assert isinstance(caught.value.exceptions[0], ValueError)
    assert str(caught.value.exceptions[0]) == "late cleanup failure"
    assert lifetime.dispose() is completion
    with pytest.raises(BaseExceptionGroup) as replayed:
        await await_result(lifetime.dispose())
    assert replayed.value is caught.value


@pytest.mark.parametrize("phase", ["setup", "dispose"])
@pytest.mark.parametrize("observed", [False, True])
async def test_background_failure_reaches_asyncio_diagnostics_unless_observed(
    phase,
    observed,
) -> None:
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()
    reports = []
    started, release, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    finalized = asyncio.Event()

    class TrackedContext(Context):
        def _finalize(self):
            super()._finalize()
            finalized.set()

    lifetime = TrackedContext()._lifecycle

    async def fail():
        try:
            started.set()
            await release.wait()
            raise ValueError(f"unobserved {phase} failure")
        finally:
            finished.set()

    if phase == "setup":
        completion = lifetime.effect(fail)
    else:
        lifetime.effect(lambda: fail)
        completion = lifetime.dispose()
    loop.set_exception_handler(lambda _, report: reports.append(report))
    try:
        waiter = asyncio.create_task(await_result(completion))
        await started.wait()
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await finished.wait()
        if phase == "setup":
            lifetime.dispose()
        await finalized.wait()
        if observed:
            expected = ValueError if phase == "setup" else BaseExceptionGroup
            with pytest.raises(expected):
                await completion
        del waiter, completion, lifetime
        gc.collect()

        if observed:
            assert not reports
            return
        assert len(reports) == 1
        assert reports[0]["message"] == "Task exception was never retrieved"
        error = reports[0]["exception"]
        if phase == "dispose":
            assert isinstance(error, BaseExceptionGroup)
            (error,) = error.exceptions
        assert isinstance(error, ValueError)
        assert str(error) == f"unobserved {phase} failure"
    finally:
        loop.set_exception_handler(previous_handler)


def test_lifecycle_releases_effect_resource_references() -> None:
    class Resource:
        def close(self):
            pass

    resource = Resource()
    reference = weakref.ref(resource)
    lifetime = Context()._lifecycle
    lifetime.effect(lambda resource=resource: resource.close)
    del resource
    assert reference() is not None
    lifetime.dispose()
    gc.collect()
    assert reference() is None


def test_context_shares_store_and_schema_but_not_lifecycle() -> None:
    schema = Schema({"group": {"value": Schema.leaf(), "unset": Schema.leaf()}})
    root = Context()
    root.declare(schema)
    child = root.fork()
    other = Context()
    other.declare(schema)
    assert root._schema is child._schema
    assert other._schema is not root._schema
    schema = root._schema
    assert root._store is child._store
    assert other._store is not root._store
    assert root._lifecycle is not child._lifecycle
    assert child._lifecycle.ctx is child
    assert root._lifecycle.ctx is root
    assert child.parent is root
    assert root.children == (child,)
    with pytest.raises(FrozenInstanceError):
        child._schema = Schema()
    with pytest.raises(FrozenInstanceError):
        child._store = other._store
    assert schema._stores == {root._store}
    assert child.resolve("group.value") is schema.resolve("group.value")
    assert child.resolve_entry("group.value") is schema.resolve_entry("group.value")
    assert child.entries == schema.entries
    assert [entry.ref.path for entry in child.entries] == [
        "",
        "$",
        "$.tree",
        "$.tree.data",
        "$.tree.node",
        "$.eval",
        "$.eval.handlers",
        "group",
        "group.value",
        "group.unset",
    ]
    assert child.to_dict() == {"$": child.get("$").to_dict()}
    root.dispose()
    with pytest.raises(RuntimeError, match="disposed"):
        child.resolve("group.value")
    with pytest.raises(RuntimeError, match="disposed"):
        child.resolve_entry("group.value")
    with pytest.raises(RuntimeError, match="disposed"):
        _ = child.entries
    assert other.resolve("group.value").path == "group.value"
    other.dispose()
    assert not schema._stores


def test_failed_binding_leaves_child_disposal_to_its_owner() -> None:
    original_identity = Identity("original")
    replacement_identity = Identity("replacement")
    root = Context()
    root.declare(Schema({"value": Schema.leaf()}))
    initial_effects = tuple(root._lifecycle._effects)
    root.update({"value": "root"})
    child = root.derive(
        bindings={"value": ScopeBinding(original_identity, blocked=False)}
    )
    with pytest.raises(ValueError, match="immutable"):
        child._store.bind(
            child.scope,
            child.resolve_entry("value", role="leaf"),
            ScopeBinding(replacement_identity, blocked=False),
        )
    assert child.get("value") == "root"
    assert root._store._scope_usages[child.scope].viewers == {child}
    child.dispose()
    assert tuple(root._lifecycle._effects) == initial_effects
    assert {
        scope for scope, usage in root._store._scope_usages.items() if usage.viewers
    } == {root.scope}
    assert root.get("value") == "root"
    root.dispose()


async def test_disposing_context_does_not_freeze_shared_data_or_block_revocation() -> (
    None
):
    root = Context()
    root.declare(Schema({"group": {"value": Schema.leaf()}}))
    owner = root.fork()
    viewer = root.fork()
    owner.declare({"temporary": Schema.leaf(mode="register")})
    revoke = owner.register("temporary", 1)
    owner.set("group.value", "old")
    view = owner.get("group")
    started = asyncio.Event()
    finish = asyncio.Event()

    async def cleanup():
        started.set()
        await finish.wait()
        assert view.get("value") == "new"

    owner.effect(lambda: cleanup)
    task = asyncio.create_task(await_result(owner.dispose()))
    await started.wait()
    with pytest.raises(RuntimeError, match="disposed"):
        owner.set("group.value", "forbidden")
    with pytest.raises(RuntimeError, match="disposed"):
        owner.declare({"forbidden": Schema.leaf()})
    viewer.set("group.value", "new")
    revoke()
    assert not viewer.exists("temporary")
    assert owner.get("group.value") == "new"
    finish.set()
    await task
    with pytest.raises(KeyError):
        viewer.resolve("temporary")
    with pytest.raises(RuntimeError, match="disposed"):
        view.get("value")
    assert viewer.get("group.value") == "new"
    root.dispose()


def test_schema_withdrawal_clears_store_binding_data() -> None:
    schema = Schema()
    withdraw = schema.declare({"value": Schema.leaf()})
    store = ContextStore(schema)
    scope = Scope()
    viewer = object()
    store.acquire_scope(viewer, scope)
    store.set(scope, schema.resolve_entry("value"), 1)
    assert store.get(scope, schema.resolve_entry("value")) == 1
    withdraw()
    assert not store._data
    store.release_scope(viewer, scope)
    store.dispose()
    assert not schema._stores


def test_disposed_store_detaches_without_changing_other_schema_consumers() -> None:
    schema = Schema()
    withdraw = schema.declare({"value": Schema.leaf()})
    left, right = ContextStore(schema), ContextStore(schema)
    scope = Scope()
    left_viewer, right_viewer = object(), object()
    left.acquire_scope(left_viewer, scope)
    right.acquire_scope(right_viewer, scope)
    left.set(scope, schema.resolve_entry("value"), "left")
    right.set(scope, schema.resolve_entry("value"), "right")
    left.release_scope(left_viewer, scope)
    left.dispose()
    left.dispose()
    assert schema._stores == {right}
    assert right.get(scope, schema.resolve_entry("value")) == "right"
    withdraw()
    assert not right._data
    right.release_scope(right_viewer, scope)
    right.dispose()
    assert not schema._stores


async def test_context_disposal_releases_store_after_failed_async_cleanup() -> None:
    schema = Schema()
    root = Context()
    root.declare(schema)
    schema = root._schema
    store = root._store
    events = []

    async def cleanup():
        assert store in schema._stores
        events.append("cleanup")
        raise ValueError("failure")

    root.effect(lambda: cleanup)
    with pytest.raises(BaseExceptionGroup):
        await await_result(root.dispose())
    assert events == ["cleanup"]
    assert not schema._stores
    assert all(not usage.viewers for usage in store._scope_usages.values())
