from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from slyme.context import Compose, Context, Schema
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
