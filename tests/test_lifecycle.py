import asyncio
import gc
import weakref
from dataclasses import FrozenInstanceError

import pytest

from slyme.context import (
    Context,
    ContextStore,
    Identity,
    Lifecycle,
    Schema,
    Scope,
    ScopeBinding,
)
from slyme.utils.exception import BaseExceptionGroup
from slyme.utils.execution import await_result


def test_lifecycle_disposal_visits_each_descendant_once() -> None:
    checked: list[Lifecycle] = []

    class TrackedLifecycle(Lifecycle):
        def dispose(self):
            checked.append(self)
            return super().dispose()

    root = TrackedLifecycle()
    left = TrackedLifecycle(parent=root)
    grandchild = TrackedLifecycle(parent=left)
    right = TrackedLifecycle(parent=root)
    root.dispose()
    assert checked == [root, right, left, grandchild]


def test_lifecycle_finalizes_after_owned_cleanup_even_on_failure() -> None:
    events = []
    failure = ValueError("cleanup")
    lifetime = Lifecycle(finalize=lambda: events.append("finalize"))
    lifetime.effect(lambda: lambda: events.append("first"))
    child = Lifecycle(parent=lifetime, finalize=lambda: events.append("child"))

    def fail():
        events.append("failure")
        raise failure

    lifetime.effect(lambda: fail)
    with pytest.raises(BaseExceptionGroup) as caught:
        lifetime.dispose()
    assert caught.value.exceptions == (failure,)
    assert events == ["failure", "child", "first", "finalize"]
    assert not lifetime._owned
    assert lifetime._finalizer is None
    child.dispose()
    with pytest.raises(BaseExceptionGroup) as repeated:
        lifetime.dispose()
    assert repeated.value is caught.value


def test_lifecycle_finalizer_failure_is_retained_without_repeating_cleanup() -> None:
    calls = []
    failure = ValueError("finalize")

    def finalize():
        calls.append("finalize")
        raise failure

    lifetime = Lifecycle(finalize=finalize)
    for _ in range(2):
        with pytest.raises(ValueError) as caught:
            lifetime.dispose()
        assert caught.value is failure
    assert calls == ["finalize"]
    with pytest.raises(RuntimeError, match="disposed"):
        lifetime.assert_readable()


async def test_lifecycle_waiters_share_cleanup_and_finalize_after_it() -> None:
    started = asyncio.Event()
    finish = asyncio.Event()
    events = []
    lifetime = Lifecycle(finalize=lambda: events.append("finalize"))

    async def cleanup():
        started.set()
        await finish.wait()
        events.append("cleanup")

    lifetime.effect(lambda: cleanup)
    first = asyncio.create_task(lifetime.adispose())
    await started.wait()
    lifetime.assert_readable()
    with pytest.raises(RuntimeError, match="disposed"):
        Lifecycle(parent=lifetime)
    with pytest.raises(RuntimeError, match="disposed"):
        lifetime.effect(lambda: pytest.fail("setup must not run"))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    second = asyncio.create_task(lifetime.adispose())
    finish.set()
    await second
    assert events == ["cleanup", "finalize"]
    await lifetime.adispose()


def test_lifecycle_releases_its_finalizer_reference() -> None:
    class Resource:
        def close(self):
            pass

    resource = Resource()
    reference = weakref.ref(resource)
    lifetime = Lifecycle(finalize=resource.close)
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
    assert child._lifecycle.parent is root._lifecycle
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
    initial_owned = tuple(root._lifecycle._owned)
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
    assert tuple(root._lifecycle._owned) == initial_owned
    assert {
        scope for scope, usage in root._store._scope_usages.items() if usage.viewers
    } == {root.scope}
    assert root.get("value") == "root"
    root.dispose()


async def test_closing_context_does_not_freeze_shared_data_or_block_revocation() -> (
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
    task = asyncio.create_task(owner.adispose())
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
