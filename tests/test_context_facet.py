from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import Awaitable
from dataclasses import FrozenInstanceError

import pytest

from slyme.context import Compose, Context, Identity, Schema, ScopeBinding
from slyme.context.default import DATA_TREE_REF
from slyme.utils.execution import await_result
from tests.compose_helpers import ValueLayer, collect_values


class Plugin:
    def __init__(self, ctx: Context[Plugin]) -> None:
        self.ctx = ctx
        self.active_ctx: Context | None = None

    def unload(self) -> None | Awaitable[None]:
        if self.active_ctx is not None:
            return self.active_ctx.dispose()

    def dispose(self) -> None:
        pytest.fail("Context must not automatically dispose its facet")


@pytest.mark.parametrize("creation", ["root", "fork", "derive"])
def test_factory_receives_initialized_context_once(creation) -> None:
    parent = Context()
    calls = []

    def create(ctx: Context[Plugin]) -> Plugin:
        calls.append(ctx)
        assert ctx.get(DATA_TREE_REF)
        if creation == "root":
            assert ctx.parent is None
            assert ctx.root is ctx
        else:
            assert ctx.parent is parent
            assert ctx in parent.children
            assert ctx.root is parent
        return Plugin(ctx)

    construct = Context if creation == "root" else getattr(parent, creation)
    ctx = construct(facet_factory=create)
    plugin = ctx.facet
    assert calls == [ctx]
    assert isinstance(plugin, Plugin)
    assert plugin.ctx is ctx
    assert ctx.__dict__["facet"] is plugin
    with pytest.raises(FrozenInstanceError):
        ctx.facet = object()
    ctx.dispose()
    ctx.dispose()
    assert ctx.facet is plugin
    assert calls == [ctx]
    parent.dispose()


def test_facet_is_not_inherited_or_shared_with_scope() -> None:
    root = Context(facet_factory=Plugin)
    child = root.fork()
    derived = root.derive()
    other = root.fork(facet_factory=lambda ctx: {"context": ctx})
    assert child.scope is other.scope is root.scope
    assert child.facet is derived.facet is None
    assert other.facet == {"context": other}
    assert root.facet.ctx is root
    root.dispose()
    assert child.facet is None
    empty = Context[None]()
    assert empty.facet is None
    empty.dispose()


@pytest.mark.parametrize("value", [None, False, 0, "", [], {}])
def test_factory_preserves_its_returned_value(value) -> None:
    ctx = Context(facet_factory=lambda ctx: value)
    assert ctx.facet is value
    ctx.dispose()


def test_derive_installs_all_bindings_before_creating_facet() -> None:
    root = Context()
    root.declare({name: Schema.leaf() for name in ("shared", "hidden", "inherited")})
    root.update({"shared": "root", "hidden": "root", "inherited": "root"})
    values = Compose(factory=ValueLayer, query=collect_values)
    root.effect(lambda: values.register(root.scope, "root"))
    shared = Identity()
    composed = Identity(blocked=True)
    peer = root.derive(bindings={"shared": shared, values: composed})
    peer.set("shared", "peer")
    peer.effect(lambda: values.register(peer.scope, "peer"))

    def create(ctx: Context) -> dict:
        return {
            "shared": ctx.get("shared"),
            "hidden": ctx.get("hidden", None),
            "inherited": ctx.get("inherited"),
            "values": values.resolve(ctx.scope),
        }

    child = root.derive(
        bindings={
            "shared": shared,
            "hidden": ScopeBinding(blocked=True),
            values: composed,
        },
        facet_factory=create,
    )
    assert child.facet == {
        "shared": "peer",
        "hidden": None,
        "inherited": "root",
        "values": ("peer",),
    }
    root.dispose()


@pytest.mark.parametrize("creation", ["root", "fork", "derive"])
def test_factory_failure_disposes_context_and_synchronous_effects(creation) -> None:
    parent = Context()
    baseline = tuple(parent._lifecycle._effects)
    contexts, cleanup = [], []
    failure = ValueError("facet construction failed")

    def fail(ctx: Context) -> Plugin:
        contexts.append(ctx)
        ctx.effect(lambda: lambda: cleanup.append(ctx))
        raise failure

    construct = Context if creation == "root" else getattr(parent, creation)
    with pytest.raises(ValueError) as caught:
        construct(facet_factory=fail)
    assert caught.value is failure
    assert cleanup == contexts
    assert len(contexts) == 1
    ctx = contexts[0]
    assert not ctx._lifecycle._effects
    assert not parent.children
    assert tuple(parent._lifecycle._effects) == baseline
    assert not ctx._store._scope_usages[ctx.scope].viewers.difference({parent})
    with pytest.raises(RuntimeError, match="disposed"):
        ctx.get(DATA_TREE_REF)
    ctx.dispose()
    assert cleanup == contexts
    parent.dispose()


async def test_factory_can_register_async_cleanup_without_special_checks() -> None:
    events = []

    async def cleanup() -> None:
        await asyncio.sleep(0)
        events.append("cleanup")

    def create(ctx: Context[Plugin]) -> Plugin:
        ctx.effect(lambda: cleanup)
        return Plugin(ctx)

    ctx = Context(facet_factory=create)
    assert not events
    await await_result(ctx.dispose())
    assert events == ["cleanup"]
    assert ctx.facet.ctx is ctx


def test_plugin_can_release_an_activation_without_losing_its_instance() -> None:
    root = Context()
    instance = root.fork(facet_factory=Plugin)
    plugin = instance.facet
    plugin.active_ctx = instance.fork()
    cleanup = []
    plugin.active_ctx.effect(lambda: lambda: cleanup.append("first"))
    plugin.unload()
    assert cleanup == ["first"]
    assert instance in root.children
    assert not instance.children
    assert instance.facet is plugin
    plugin.active_ctx = instance.fork()
    plugin.active_ctx.effect(lambda: lambda: cleanup.append("second"))
    root.dispose()
    assert cleanup == ["first", "second"]
    assert instance.facet is plugin


def test_factories_can_explicitly_share_a_facet() -> None:
    root = Context()
    shared = object()
    first = root.fork(facet_factory=lambda ctx: shared)
    second = root.derive(facet_factory=lambda ctx: shared)
    first.dispose()
    assert first.facet is second.facet is shared
    root.dispose()


def test_factory_is_released_and_facet_follows_context_references() -> None:
    class Factory:
        def __call__(self, ctx: Context[Plugin]) -> Plugin:
            return Plugin(ctx)

    root = Context()
    factory = Factory()
    factory_ref = weakref.ref(factory)
    child = root.derive(facet_factory=factory)
    facet_ref = weakref.ref(child.facet)
    context_ref = weakref.ref(child)
    del factory
    gc.collect()
    assert factory_ref() is None
    child.dispose()
    gc.collect()
    assert facet_ref() is child.facet
    del child
    gc.collect()
    assert context_ref() is None
    assert facet_ref() is None
    root.dispose()
