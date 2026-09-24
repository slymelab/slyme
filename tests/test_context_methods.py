from __future__ import annotations

import asyncio
from inspect import isawaitable

import pytest

from slyme.context import Context, ContextPathError, Identity, Schema, Scope
from slyme.context.store import _MISSING
from slyme.utils.execution import await_result


def test_method_binds_accessing_context_and_preserves_raw_function() -> None:
    root = Context()
    calls = []

    def describe(ctx: Context, /, value: str, *, suffix: str = "") -> str:
        calls.append(ctx)
        return value + suffix

    remove = root.install("describe", describe)
    child = root.fork()
    method = child.describe
    assert not calls
    assert child.get("$.methods.describe") is describe
    assert method("hello", suffix="!") == "hello!"
    assert root.describe("root") == "root"
    assert calls == [child, root]
    assert "describe" not in child.__dict__
    assert remove() is None
    assert remove() is None
    assert not hasattr(root, "describe")
    assert not hasattr(child, "describe")
    assert method("retained") == "retained"
    with pytest.raises(ContextPathError):
        root.resolve("$.methods.describe")
    root.dispose()


async def test_async_method_is_not_started_or_wrapped_on_access() -> None:
    root = Context()
    calls = []

    async def identify(ctx: Context, /) -> Context:
        calls.append(ctx)
        await asyncio.sleep(0)
        return ctx

    root.install("identify", identify)
    child = root.derive()
    result = child.identify()
    assert isawaitable(result)
    assert not calls
    assert await result is child
    assert calls == [child]
    root.dispose()


def test_installed_method_can_install_another_method_on_its_caller() -> None:
    root = Context()

    def provide(ctx: Context, /, *, name: str, func):
        return ctx.install(name, func)

    root.install("provide", provide)
    child = root.derive()
    child.provide(name="identify", func=lambda ctx: ctx)
    assert child.identify() is child
    assert not hasattr(root, "identify")
    child.dispose()
    assert not hasattr(root, "identify")
    assert hasattr(root, "provide")
    root.dispose()


def test_scope_shadowing_restores_inherited_method_on_withdrawal() -> None:
    root = Context()
    root.install("identify", lambda ctx: ("root", ctx))
    child = root.derive()
    sibling = root.derive()
    assert child.identify() == ("root", child)
    remove = child.install("identify", lambda ctx: ("child", ctx))
    assert child.identify() == ("child", child)
    assert sibling.identify() == ("root", sibling)
    remove()
    assert child.identify() == ("root", child)
    assert root.identify() == ("root", root)
    root.dispose()


def test_withdrawing_parent_registration_preserves_child_declaration() -> None:
    root = Context()
    remove = root.install("identify", lambda ctx: "root")
    entry = root.resolve_entry("$.methods.identify")
    child = root.derive()
    child.install("identify", lambda ctx: "child")
    remove()
    assert entry.alive
    assert not hasattr(root, "identify")
    assert child.identify() == "child"
    child.dispose()
    assert not entry.alive
    with pytest.raises(ContextPathError):
        root.resolve("$.methods")
    root.dispose()


def test_method_visibility_does_not_fall_back_to_root_or_other_applications() -> None:
    root = Context()
    other = Context()
    root.install("identify", lambda ctx: ctx)
    detached = root.fork(scope=Scope())
    assert not hasattr(detached, "identify")
    assert not hasattr(other, "identify")
    detached.install("identify", lambda ctx: ("detached", ctx))
    assert detached.identify() == ("detached", detached)
    assert root.identify() is root
    root.dispose()
    other.dispose()


def test_duplicate_install_rolls_back_its_child_and_declaration() -> None:
    root = Context()
    remove = root.install("identify", lambda ctx: ctx)
    child = root.fork()
    entries = root.entries
    with pytest.raises(ContextPathError, match="existing local path"):
        child.install("identify", lambda ctx: None)
    assert not child.children
    assert root.entries == entries
    assert child.identify() is child
    remove()
    with pytest.raises(ContextPathError):
        root.resolve("$.methods.identify")
    root.dispose()


def test_shared_identity_reuses_method_and_rejects_duplicate_registration() -> None:
    root = Context()
    root.install("identify", lambda ctx: "root")
    identity = Identity()
    left = root.derive(bindings={"$.methods.identify": identity})
    right = root.derive(bindings={"$.methods.identify": identity})
    remove = left.install("identify", lambda ctx: ("shared", ctx))
    assert right.identify() == ("shared", right)
    with pytest.raises(ContextPathError, match="existing local path"):
        right.install("identify", lambda ctx: None)
    assert not right.children
    remove()
    assert right.identify() == "root"
    root.dispose()


def test_schema_conflict_during_install_leaves_no_child_or_partial_paths() -> None:
    root = Context()
    root.declare({"$": {"methods": {"identify": Schema.leaf()}}})
    root.set("$.methods.identify", "data")
    entries = root.entries
    with pytest.raises(ValueError, match="Conflicting"):
        root.install("identify", lambda ctx: ctx)
    assert not root.children
    assert root.entries == entries
    assert root.get("$.methods.identify") == "data"
    root.dispose()


@pytest.mark.parametrize(
    "name",
    [
        "get",
        "install",
        "register",
        "facet",
        "scope",
        "parent",
        "root",
        "children",
        "entries",
        "dispose_mode",
    ],
)
def test_native_method_names_do_not_change_context(name: str) -> None:
    root = Context()
    entries = root.entries
    with pytest.raises(ValueError, match="already exists"):
        root.install(name, lambda ctx: ctx)
    assert root.entries == entries
    assert not root.children
    root.dispose()


@pytest.mark.parametrize("name", ["class", "two words", "1name", "$method"])
def test_method_names_need_not_be_python_identifiers(name: str) -> None:
    root = Context()
    root.install(name, lambda ctx: ctx)
    assert getattr(root, name)() is root
    assert root.get(f"$.methods.{name}")(root) is root
    root.dispose()


@pytest.mark.parametrize("name", ["_private", "__await__", "then"])
def test_private_and_protocol_names_are_not_reserved(name: str) -> None:
    root = Context()
    remove = root.install(name, lambda ctx: ctx)
    assert root.get(f"$.methods.{name}")(root) is root
    assert getattr(root, name)() is root
    remove()
    assert not hasattr(root, name)
    root.dispose()


@pytest.mark.parametrize("name, error", [("", TypeError), ("nested.name", ValueError)])
def test_schema_rejects_invalid_path_segments_and_install_rolls_back(
    name, error
) -> None:
    root = Context()
    entries = root.entries
    with pytest.raises(error):
        root.install(name, lambda ctx: ctx)
    assert root.entries == entries
    assert not root.children
    root.dispose()


def test_name_check_does_not_evaluate_native_descriptors() -> None:
    class AppContext(Context):
        @property
        def native(self):
            pytest.fail("Name checking must not evaluate descriptors")

    root = AppContext()
    with pytest.raises(ValueError, match="already exists"):
        root.install("native", lambda ctx: ctx)
    root.dispose()


def test_missing_attributes_and_uninitialized_facet_raise_attribute_error() -> None:
    def create(ctx: Context) -> str:
        assert not hasattr(ctx, "facet")
        assert not hasattr(ctx, "unknown")
        assert not hasattr(ctx, "_unknown")
        assert not hasattr(ctx, "then")
        assert not isawaitable(ctx)
        with pytest.raises(ValueError, match="already exists"):
            ctx.install("facet", lambda current: current)
        ctx.install("identify", lambda current: current)
        assert ctx.identify() is ctx
        return "ready"

    root = Context(facet_factory=create)
    assert root.facet == "ready"
    with pytest.raises(AttributeError, match="unknown"):
        root.unknown()
    root.dispose()


def test_method_body_errors_are_not_converted_to_attribute_errors() -> None:
    root = Context()

    def read(ctx: Context):
        return ctx.get("undeclared")

    root.install("read", read)
    with pytest.raises(ContextPathError):
        root.read()
    root.dispose()


def test_owner_disposal_removes_shared_scope_method_and_lifecycle_errors_propagate() -> (
    None
):
    root = Context()
    owner = root.fork()
    owner.install("identify", lambda ctx: ctx)
    assert root.identify() is root
    owner.dispose()
    assert not owner.children
    assert not hasattr(root, "identify")
    with pytest.raises(RuntimeError, match="disposed"):
        owner.identify()
    with pytest.raises(RuntimeError, match="disposed"):
        owner.install("identify", lambda ctx: ctx)
    root.dispose()


async def test_method_remains_readable_during_cleanup() -> None:
    root = Context()
    root.install("identify", lambda ctx: ctx)
    child = root.fork()
    calls = []

    async def cleanup() -> None:
        await asyncio.sleep(0)
        calls.append(child.identify())
        with pytest.raises(RuntimeError, match="disposed|disposing"):
            child.install("other", lambda ctx: ctx)

    child.effect(lambda: cleanup)
    await await_result(root.dispose())
    assert calls == [child]


def test_batch_owner_withdraws_each_method_before_its_declaration(monkeypatch) -> None:
    root = Context()
    group = root.fork(dispose_mode="batch")
    group.install("identify", lambda ctx: ctx)
    (install_owner,) = group.children
    assert install_owner.scope is group.scope
    assert install_owner.dispose_mode == "sequential"
    entry = root.resolve_entry("$.methods.identify")
    binding = root._store._data[entry]
    original_delete = Schema._delete_entry

    def delete_entry(schema, removed):
        if removed is entry:
            assert binding.get(group.scope) is _MISSING
        return original_delete(schema, removed)

    monkeypatch.setattr(Schema, "_delete_entry", delete_entry)
    group.dispose()
    assert not entry.alive
    assert not root.children
    assert not hasattr(root, "identify")
    root.dispose()
