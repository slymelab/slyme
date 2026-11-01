from __future__ import annotations

import weakref

import pytest

from slyme.context import Context, ContextPathError, Ref, Schema
from slyme.context.store import ContextStore
from slyme.utils.exception import BaseExceptionGroup


@pytest.fixture
def ctx():
    root = Context()
    root.declare(
        {
            "a": Schema.leaf(mode="register"),
            "b": Schema.leaf(mode="register"),
            "value": Schema.leaf(),
            "group": {},
        }
    )
    try:
        yield root
    finally:
        root.dispose()


def test_resolve_entries_preserves_order_duplicates_and_live_declarations(ctx):
    for resolver in (ctx, Schema({"a": Schema.leaf(), "group": {}})):
        first = resolver.resolve_entry("a")
        result = resolver.resolve_entries(iter(["a", Ref("a"), "group"]))
        assert result == (first, first, resolver.resolve_entry("group"))
        assert resolver.resolve_entries([]) == ()
        with pytest.raises(ContextPathError, match="container, not a leaf"):
            resolver.resolve_entries(["a", "group"], role="leaf")
        with pytest.raises(ContextPathError, match="not declared"):
            resolver.resolve_entries(["a", "missing"])
    remove = ctx.declare({"temporary": Schema.leaf()})
    old = ctx.resolve_entries(["temporary"])[0]
    remove()
    assert not old.alive
    ctx.declare({"temporary": Schema.leaf()})
    assert ctx.resolve_entries(["temporary"])[0] is not old


def test_registration_batch_is_owned_and_early_disposer_is_exact(ctx):
    child = ctx.fork()
    remove = child.register_many({"a": 1, Ref("b"): None})
    assert ctx.get("a") == 1 and ctx.get("b") is None
    remove()
    replacement = ctx.register_many({"a": 2, "b": 3})
    remove()
    child.dispose()
    assert ctx.get("a") == 2 and ctx.get("b") == 3
    replacement()
    owner = ctx.fork()
    owner.register_many({"a": 4, "b": 5})
    owner.dispose()
    assert not ctx.exists("a") and not ctx.exists("b")


def test_registration_batch_shadows_inherited_values(ctx):
    ctx.register_many({"a": "parent", "b": "parent"})
    child = ctx.derive()
    remove = child.register_many({"a": "child", "b": "child"})
    assert ctx.get("a") == "parent" and child.get("a") == "child"
    remove()
    assert child.get("a") == child.get("b") == "parent"


@pytest.mark.parametrize("target", ["missing", "group", "value", "b", Ref("a")])
def test_registration_batch_failure_withdraws_only_its_own_installations(ctx, target):
    ctx.register("b", "existing")
    effects = tuple(ctx._lifecycle._effects)
    with pytest.raises(ContextPathError):
        ctx.register_many({"a": "new", target: "conflicting"})
    assert not ctx.exists("a")
    assert ctx.get("b") == "existing"
    assert tuple(ctx._lifecycle._effects) == effects
    # Failed setup leaves no hidden registration blocking the next attempt.
    ctx.register_many({"a": "retry"})()


def test_batch_disposer_releases_payloads_even_when_retained(ctx):
    class Value:
        pass

    value = Value()
    reference = weakref.ref(value)
    remove = ctx.register_many({"a": value})
    del value
    assert reference() is not None
    remove()
    assert reference() is None
    remove()


def test_batch_cleanup_is_reverse_order_all_settled_and_repeatable(ctx, monkeypatch):
    register = ContextStore.register
    events = []

    def failing_cleanup(self, scope, entry, value):
        dispose = register(self, scope, entry, value)

        def cleanup():
            events.append(entry.ref.path)
            dispose()
            raise RuntimeError(entry.ref.path)

        return cleanup

    monkeypatch.setattr(ContextStore, "register", failing_cleanup)
    remove = ctx.register_many({"a": 1, "b": 2})
    with pytest.raises(BaseExceptionGroup) as first:
        remove()
    assert events == ["b", "a"]
    assert [str(error) for error in first.value.exceptions] == ["b", "a"]
    assert not ctx.exists("a") and not ctx.exists("b")
    with pytest.raises(BaseExceptionGroup) as repeated:
        remove()
    assert repeated.value is first.value
    assert events == ["b", "a"]


def test_cleanup_rejects_new_batches_but_allows_entry_reads(ctx):
    observed = []

    def cleanup():
        observed.extend(ctx.resolve_entries(["a"]))
        for operation in (
            lambda: ctx.set_many({}),
            lambda: ctx.delete_many([]),
            lambda: ctx.register_many({}),
        ):
            with pytest.raises(RuntimeError, match="being disposed"):
                operation()

    ctx.effect(lambda: cleanup)
    ctx.dispose()
    assert len(observed) == 1
    with pytest.raises(RuntimeError, match="disposed"):
        ctx.resolve_entries([])
