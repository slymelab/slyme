from __future__ import annotations

import gc
import weakref
from collections.abc import Hashable

import pytest

from slyme.context import Compose, Context, Schema, Scope


def test_shared_identity_has_one_current_value_and_a_persistent_barrier() -> None:
    root = Context({"value": "root"}, schema=Schema({"value": Schema.leaf()}))
    left = root.isolate("value", identity="shared")
    right = root.isolate("value", identity="shared")
    remove = left.add("value", "original")
    binding = next(iter(root._data.values()))
    right.set("value", "updated")
    remove()
    assert left.get("value") == "updated"
    assert right.get("value") == "updated"
    assert len(binding._values) == 2
    assert binding._blocked == {"shared"}

    right.delete("value")
    assert not left.exists("value")
    assert not right.exists("value")
    assert binding._blocked == {"shared"}
    assert root.get("value") == "root"
    right.add("value", None)
    left.dispose()
    assert right.get("value") is None
    right.dispose()
    assert len(binding._values) == 1
    assert not binding._blocked
    root.dispose()


def test_shared_value_does_not_retain_its_disposed_writers_scope() -> None:
    root = Context(schema=Schema({"value": Schema.leaf()}))
    writer = root.isolate("value", identity="shared")
    reader = root.isolate("value", identity="shared")
    writer.set("value", "retained")
    scope_ref = weakref.ref(writer.scope)
    writer.dispose()
    del writer
    gc.collect()
    assert scope_ref() is None
    assert reader.get("value") == "retained"
    root.dispose()


@pytest.mark.parametrize("operation", ["set", "delete", "remove"])
def test_value_finalizer_can_replace_the_same_path(operation: str) -> None:
    ctx = Context(schema=Schema({"value": Schema.leaf()}))
    events: list[str] = []

    class Payload:
        def __del__(self) -> None:
            events.append("finalized")
            ctx.set("value", "reentrant")

    remove = ctx.add("value", Payload())
    if operation == "set":
        ctx.set("value", "replacement")
    elif operation == "delete":
        ctx.delete("value")
    else:
        remove()
    assert events == ["finalized"]
    assert ctx.get("value") == "reentrant"
    remove()
    assert ctx.get("value") == "reentrant"
    ctx.delete("value")
    assert not ctx.exists("value")
    ctx.dispose()


def test_identity_release_preserves_a_barrier_created_by_a_value_finalizer() -> None:
    root = Context({"value": "root"}, schema=Schema({"value": Schema.leaf()}))
    writer = root.isolate("value", identity="shared")
    replacements: list[Context] = []

    class Payload:
        def __del__(self) -> None:
            child = root.isolate("value", identity="shared")
            child.set("value", "new")
            replacements.append(child)

    writer.set("value", Payload())
    writer.dispose()
    assert len(replacements) == 1
    child = replacements[0]
    assert child.get("value") == "new"
    child.delete("value")
    assert not child.exists("value")
    root.dispose()


def test_context_resolve_uses_the_shared_complete_c3_identity_walk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Context({"value": "root"}, schema=Schema({"value": Schema.leaf()}))
    left = root.isolate("value", identity="shared")
    right = root.isolate("value", identity="shared")
    unbound = Scope(parents=(left.scope, right.scope))
    child = root.fork(scope=unbound.fork())
    child.set("value", "child")
    binding = next(iter(root._data.values()))
    seen: list[Scope] = []
    original = binding._scope_identities.get
    identities_before = dict(binding._scope_identities)

    def record(scope: Scope, default: Hashable = None) -> Hashable:
        seen.append(scope)
        return original(scope, default)

    with monkeypatch.context() as patch:
        patch.setattr(binding._scope_identities, "get", record)
        assert child.get("value") == "child"
        assert tuple(seen) == child.scope.mro
    assert dict(binding._scope_identities) == identities_before
    assert Compose._scoped_identities(
        binding._scope_identities, child.scope, local=False
    ) == [
        binding._identity_for(child.scope, create=False),
        "shared",
        binding._identity_for(root.scope, create=False),
    ]
    root.dispose()
