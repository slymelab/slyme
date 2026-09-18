from __future__ import annotations

import asyncio
import gc
import inspect
import weakref
from dataclasses import FrozenInstanceError, dataclass, fields, is_dataclass, replace
from itertools import permutations
from typing import Any

import pytest

from slyme.context import (
    Context,
    Metadata,
    Ref,
    RefConfig,
    RefContainerConfig,
    RefEntry,
    RefLeafConfig,
    Schema,
)
from slyme.context.schema import SCHEMA_ENGINE, _Declaration


@dataclass(frozen=True)
class _AnnotatedLeaf(RefLeafConfig[str]):
    labels: tuple[str, ...] = ()

    def merge(self, other: RefConfig[str]) -> _AnnotatedLeaf:
        if not isinstance(other, _AnnotatedLeaf):
            raise ValueError("Incompatible annotation config")
        Schema.leaf(self.value_type, mode=self.mode).merge(
            Schema.leaf(other.value_type, mode=other.mode)
        )
        return replace(self, labels=self.labels + other.labels)


@pytest.mark.parametrize("config", [Schema.leaf(int), Schema.container()])
def test_config_merge_returns_a_new_equal_config(config: RefConfig[Any]) -> None:
    merged = config.merge(config)
    assert merged == config
    assert merged is not config


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (Schema.leaf(), Schema.container()),
        (Schema.container(), Schema.leaf()),
        (Schema.leaf(int), Schema.leaf(str)),
        (Schema.leaf(int), Schema.leaf()),
        (Schema.leaf(mode="register"), Schema.leaf()),
    ],
)
def test_config_merge_rejects_incompatible_definitions(
    left: RefConfig[Any], right: RefConfig[Any]
) -> None:
    with pytest.raises(ValueError, match="Conflicting Ref configurations"):
        left.merge(right)


def test_public_entries_expose_root_container_and_leaf_metadata() -> None:
    root_marker, group_marker, leaf_marker = Metadata(), Metadata(), Metadata()
    schema = Schema(
        {
            "": Schema.container(metadata={"app.root": root_marker}),
            "group": {
                "": Schema.container(metadata={"app.group": group_marker}),
                "value": Schema.leaf(int, metadata={"app.leaf": leaf_marker}),
                "empty": {},
            },
        }
    )
    snapshot = schema.entries
    assert isinstance(snapshot, tuple)
    entries = {entry.ref.path: entry for entry in snapshot}
    assert entries.keys() == {"", "group", "group.value", "group.empty"}
    assert all(entry.alive for entry in snapshot)
    assert isinstance(entries[""].config, RefContainerConfig)
    assert isinstance(entries["group.empty"].config, RefContainerConfig)
    assert entries[""].config.metadata == {"app.root": root_marker}
    assert entries["group"].config.metadata == {"app.group": group_marker}
    leaf = schema.resolve_entry("group.value")
    assert leaf is entries["group.value"]
    assert leaf.ref is schema.resolve("group.value")
    assert isinstance(leaf.config, RefLeafConfig)
    assert leaf.config.value_type is int
    assert leaf.config.metadata == {"app.leaf": leaf_marker}


def test_entries_snapshot_keeps_live_entries_not_config_snapshots() -> None:
    schema = Schema()
    first = schema.declare({"value": _AnnotatedLeaf(labels=("first",))})
    snapshot = schema.entries
    entry = schema.resolve_entry("value")
    cached = entry.config
    second = schema.declare({"value": _AnnotatedLeaf(labels=("second",))})
    third = schema.declare({"other": Schema.leaf()})
    assert entry in snapshot
    assert schema.resolve_entry("other") not in snapshot
    assert entry.config == _AnnotatedLeaf(labels=("first", "second"))
    assert cached == _AnnotatedLeaf(labels=("first",))
    second()
    assert entry.alive
    assert entry.config == cached
    third()
    first()
    assert snapshot == (schema.resolve_entry(""), entry)
    assert schema.entries == (schema.resolve_entry(""),)
    assert not entry.alive
    with pytest.raises(LookupError, match="value.*no longer declared"):
        _ = entry.config
    with pytest.raises(KeyError, match="value"):
        schema.resolve_entry("value")
    remove_new = schema.declare({"value": Schema.leaf(str)})
    assert schema.resolve_entry("value") is not entry
    assert not entry.alive
    with pytest.raises(LookupError, match="value.*no longer declared"):
        _ = entry.config
    remove_new()


def test_entries_and_resolve_entry_do_not_read_configs(monkeypatch) -> None:
    schema = Schema({"value": Schema.leaf()})
    original = schema.entries

    def unexpected_config_read(self):
        pytest.fail("Entry lookup must not merge configs.")

    with monkeypatch.context() as patch:
        patch.setattr(RefEntry, "config", property(unexpected_config_read))
        assert schema.entries == original
        for entry in original:
            assert schema.resolve_entry(entry.ref.path) is entry


def test_entry_is_frozen_and_rejects_duplicate_owners_without_mutation() -> None:
    config = Schema.leaf(int)
    entry = RefEntry(Ref[int]("value"))
    assert not entry.alive
    identity_map = {entry: "binding"}
    entry._declare(("plugin", 1), config)
    cached = entry.config
    with pytest.raises(ValueError, match="Duplicate declaration"):
        entry._declare(tuple(["plugin", 1]), Schema.leaf(int))
    assert entry._declarations == {("plugin", 1): config}
    assert entry.config is cached
    with pytest.raises(ValueError, match="Conflicting Ref configurations"):
        entry._declare("conflicting", Schema.leaf(int, mode="register"))
    assert entry._declarations == {("plugin", 1): config}
    assert entry.config is cached
    with pytest.raises(FrozenInstanceError):
        entry.config = Schema.leaf(int)
    with pytest.raises(FrozenInstanceError):
        entry._declarations = {}
    with pytest.raises(FrozenInstanceError):
        entry.ref = Ref("other")
    assert weakref.ref(entry)() is entry
    assert identity_map[entry] == "binding"
    entry._undeclare(("plugin", 1))
    assert not entry.alive
    assert entry._declarations == {}


def test_entry_recomputes_config_from_remaining_declarations() -> None:
    configs = [_AnnotatedLeaf(labels=(label,)) for label in ("a", "b", "c")]
    entry = RefEntry(Ref[str]("value"))
    for owner, config in enumerate(configs):
        entry._declare(owner, config)
    assert entry.config == _AnnotatedLeaf(labels=("a", "b", "c"))
    assert tuple(entry._declarations.values()) == tuple(configs)
    entry._undeclare(1)
    assert entry.config == _AnnotatedLeaf(labels=("a", "c"))
    entry._undeclare(0)
    assert entry.config is configs[2]
    entry._undeclare(2)
    assert not entry.alive
    replacement = _AnnotatedLeaf(labels=("new",))
    entry._declare(0, replacement)
    assert entry.alive
    assert entry.config is replacement
    assert entry._declarations == {0: replacement}


def test_entry_defers_merging_until_config_is_needed(monkeypatch) -> None:
    entry = RefEntry(Ref[str]("value"))
    for owner, label in enumerate(("a", "b", "c", "d")):
        entry._declare(owner, _AnnotatedLeaf(labels=(label,)))
    calls = []
    original = _AnnotatedLeaf.merge

    def merge(self, other):
        calls.append((self.labels, other.labels))
        return original(self, other)

    monkeypatch.setattr(_AnnotatedLeaf, "merge", merge)
    entry._undeclare(0)
    entry._undeclare(2)
    assert entry._config is None
    assert calls == []
    cached = entry.config
    assert cached == _AnnotatedLeaf(labels=("b", "d"))
    assert calls == [(("b",), ("d",))]
    assert entry.config is cached
    assert len(calls) == 1

    entry._undeclare(1)
    assert entry._config is None
    entry._declare("new", _AnnotatedLeaf(labels=("e",)))
    assert entry.config == _AnnotatedLeaf(labels=("d", "e"))
    assert calls == [(("b",), ("d",)), (("d",), ("e",))]
    entry._undeclare(3)
    with pytest.raises(ValueError, match="Duplicate declaration"):
        entry._declare("new", _AnnotatedLeaf(labels=("duplicate",)))
    assert entry._config is None
    entry._undeclare("new")
    assert not entry.alive
    assert entry._config is None
    assert len(calls) == 2


def test_entry_withdrawal_releases_cached_aggregate() -> None:
    entry = RefEntry(Ref[str]("value"))
    entry._declare("first", _AnnotatedLeaf(labels=("first",)))
    entry._declare("second", _AnnotatedLeaf(labels=("second",)))
    aggregate_ref = weakref.ref(entry.config)
    entry._undeclare("second")
    gc.collect()
    assert aggregate_ref() is None
    assert entry._config is None
    entry._undeclare("first")
    assert not entry.alive
    assert entry._config is None


def test_schema_withdrawal_and_binding_cleanup_do_not_read_config(monkeypatch) -> None:
    root = Context()
    schema = root._schema
    declaration: _Declaration = {
        "group": {"value": Schema.leaf(), "owned": Schema.leaf(mode="register")}
    }
    first = schema.declare(declaration)
    second = schema.declare(declaration)
    left = root.fork(scope=root.scope.fork())
    left.update({"group.value": "left"})
    right = root.fork(scope=root.scope.fork())
    right.update({"group.value": "right"})
    remove_owned = left.register("group.owned", "owned")
    child = left.isolate("group.value")
    child.set("group.value", "child")

    def unexpected_config_read(self):
        raise AssertionError(f"Cleanup read config at {self.ref.path!r}")

    with monkeypatch.context() as patch:
        patch.setattr(RefEntry, "config", property(unexpected_config_read))
        first()
        assert schema._entries["group.value"]._config is None
        second()
        second()
        remove_owned()
        left.dispose()
        right.dispose()
        root.dispose()
    assert_indexes(schema, set())
    assert not left._store._data and not right._store._data
    assert not left._store._scope_usage and not right._store._scope_usage
    assert not schema._stores


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_context_owned_cleanup_does_not_read_config(
    monkeypatch, asynchronous: bool
) -> None:
    root = Context()
    declaration: _Declaration = {"group": {"value": Schema.leaf(mode="register")}}
    root.declare(declaration)
    root.register("group.value", "root")
    child = root.fork(scope=root.scope.fork())
    child.declare(declaration)
    child.register("group.value", "child")

    async def cleanup():
        await asyncio.sleep(0)

    if asynchronous:
        child.effect(lambda: cleanup)

    def unexpected_config_read(self):
        raise AssertionError(f"Context cleanup read config at {self.ref.path!r}")

    with monkeypatch.context() as patch:
        patch.setattr(RefEntry, "config", property(unexpected_config_read))
        await root.adispose()
        await root.adispose()
    assert_indexes(root._schema, set())
    assert not root._store._data
    assert not root._store._scope_usage
    assert not root._lifecycle._owned


def test_failed_declaration_rollback_does_not_read_config(monkeypatch) -> None:
    schema = Schema({"stable": Schema.leaf(int)})
    ctx = Context()
    ctx.declare(schema)
    schema = ctx._schema
    ctx.update({"stable": 1})
    original = Schema._merge_declaration
    failure = ValueError("registration failed")

    def unexpected_config_read(self):
        raise AssertionError(f"Rollback read config at {self.ref.path!r}")

    with monkeypatch.context() as patch:

        def fail(self, declaration, declaration_id, entries):
            original(self, declaration, declaration_id, entries)
            patch.setattr(RefEntry, "config", property(unexpected_config_read))
            raise failure

        patch.setattr(Schema, "_merge_declaration", fail)
        with pytest.raises(ValueError) as caught:
            schema.declare(
                {"stable": Schema.leaf(int), "group": {"new": Schema.leaf()}}
            )
        assert caught.value is failure
        assert caught.value.__cause__ is None
    assert_indexes(schema, {"stable"})
    assert ctx.get("stable") == 1
    ctx.dispose()


def test_partial_declaration_commit_removes_only_its_new_paths(monkeypatch) -> None:
    schema = Schema({"stable": Schema.leaf(int)})
    ctx = Context()
    ctx.declare(schema)
    schema = ctx._schema
    ctx.update({"stable": 1})
    owners = {
        path: dict(entry._declarations) for path, entry in schema._entries.items()
    }
    original = Schema._element_at
    failure = RuntimeError("commit interrupted")

    def fail(self, parts, *, create=False):
        if create and parts == ("group", "nested"):
            raise failure
        return original(self, parts, create=create)

    with monkeypatch.context() as patch:
        patch.setattr(Schema, "_element_at", fail)
        with pytest.raises(RuntimeError) as caught:
            schema.declare(
                {
                    "stable": Schema.leaf(int),
                    "group": {
                        "first": Schema.leaf(),
                        "nested": {"last": Schema.leaf()},
                    },
                }
            )
        assert caught.value is failure
        assert caught.value.__cause__ is None
    assert_indexes(schema, {"stable"})
    for path, entry in schema._entries.items():
        assert entry._declarations == owners[path]
    assert ctx.get("stable") == 1
    ctx.dispose()


def test_declaration_merges_each_incoming_config_once(monkeypatch) -> None:
    declaration: _Declaration = {"group": {"value": Schema.leaf(int)}}
    schema = Schema(declaration)
    originals = dict(schema._entries)
    results = []
    leaf_merge = RefLeafConfig.merge
    container_merge = RefContainerConfig.merge

    def merge_leaf(self, other):
        result = leaf_merge(self, other)
        results.append(result)
        return result

    def merge_container(self, other):
        result = container_merge(self, other)
        results.append(result)
        return result

    monkeypatch.setattr(RefLeafConfig, "merge", merge_leaf)
    monkeypatch.setattr(RefContainerConfig, "merge", merge_container)
    remove = schema.declare(declaration)
    assert len(results) == 3
    for path, result in zip(("", "group", "group.value"), results, strict=True):
        assert schema._entries[path] is originals[path]
        assert schema._entries[path]._config is result
    remove()
    assert len(results) == 3
    assert_indexes(schema, {"group", "group.value"})


def test_schema_merges_contributions_without_replacing_runtime_bindings() -> None:
    ctx = Context()
    ctx.declare({"blocked": Schema.leaf(int)})
    schema = ctx._schema
    first = _AnnotatedLeaf(labels=("first",))
    second = _AnnotatedLeaf(labels=("second",))
    remove_first = schema.declare({"value": first})
    entry = schema.resolve_entry("value")
    ctx.update({"value": "runtime value"})
    with pytest.raises(ValueError, match="Conflicting Ref configurations"):
        schema.declare({"value": second, "blocked": Schema.leaf(str)})
    assert entry.config is first
    assert list(entry._declarations.values()) == [first]

    remove_second = schema.declare({"value": second})
    assert schema.resolve_entry("value") is entry
    assert entry.config == _AnnotatedLeaf(labels=("first", "second"))
    assert list(entry._declarations.values()) == [first, second]
    assert ctx.get("value") == "runtime value"
    imported = Schema(schema)
    remove_first()
    assert entry.config is second
    assert ctx.get("value") == "runtime value"
    assert imported.resolve_entry("value").config == _AnnotatedLeaf(
        labels=("first", "second")
    )
    remove_second()
    assert not entry.alive
    assert_indexes(schema, {"blocked"})
    assert not ctx._store._data
    ctx.dispose()


def test_schema_dataclass_initializes_independent_state() -> None:
    assert is_dataclass(Schema)
    parameters = inspect.signature(Schema).parameters
    assert tuple(parameters) == ("declaration",)
    assert parameters["declaration"].default is None
    assert all(not item.init for item in fields(Schema))

    left = Schema()
    right = Schema(None)
    assert left != right
    assert len({left, right}) == 2
    assert left._data is not right._data
    assert left._entries is not right._entries
    assert left._stores is not right._stores
    assert left._data[""] is left._entries[""]
    assert left._entries[""] is not right._entries[""]
    assert weakref.ref(left)() is left

    with pytest.raises(FrozenInstanceError):
        left._data = {}
    with pytest.raises(FrozenInstanceError):
        del left._entries
    remove = left.declare({"value": Schema.leaf()})
    assert left.resolve("value").path == "value"
    with pytest.raises(KeyError):
        right.resolve("value")
    remove()
    assert_indexes(left, set())
    assert_indexes(right, set())


def test_schema_initial_declaration_is_not_retained() -> None:
    class Declaration(dict):
        pass

    declaration = Declaration({"group": {"value": Schema.leaf(int)}})
    source_ref = weakref.ref(declaration)
    schema = Schema(declaration=declaration)
    del declaration
    gc.collect()
    assert source_ref() is None
    assert_indexes(schema, {"group", "group.value"})


def test_schema_normalizes_dicts_without_changing_input() -> None:
    class DeclarationDict(dict):
        pass

    group = DeclarationDict(
        {"value": Schema.leaf(int), "empty": {}, "": Schema.container()}
    )
    declaration: _Declaration = {"left": group, "right": group}
    schema = Schema()
    remove = schema.declare(declaration)

    assert tuple(declaration) == ("left", "right")
    assert tuple(group) == ("value", "empty", "")
    assert group["empty"] == {}
    assert declaration["left"] is declaration["right"] is group
    assert schema._child_names(("left",)) == ("value", "empty")
    assert schema._child_names(("left", "empty")) == ()
    assert_indexes(
        schema,
        {"left", "left.value", "left.empty", "right", "right.value", "right.empty"},
    )
    group["late"] = Schema.leaf()
    assert "left.late" not in schema._entries
    remove()
    assert_indexes(schema, set())


def test_schema_tree_only_traverses_dicts() -> None:
    values = [Schema.leaf()]
    pair = (Schema.container(),)
    tree = {"nested": {"list": values, "tuple": pair}}
    leaves, structure = SCHEMA_ENGINE.flatten(tree)
    assert leaves == [values, pair]
    rebuilt = structure.unflatten(leaves)
    assert rebuilt == tree
    assert rebuilt is not tree
    assert rebuilt["nested"] is not tree["nested"]
    assert rebuilt["nested"]["list"] is values
    assert rebuilt["nested"]["tuple"] is pair


def test_schema_constructor_imports_entries_without_their_owners() -> None:
    source = Schema()
    remove_source = source.declare({"group": {"value": Schema.leaf(int)}})
    target = Schema(source)
    assert_indexes(target, {"group", "group.value"})
    for path, entry in source._entries.items():
        copied = target._entries[path]
        assert copied is not entry
        assert copied.ref == entry.ref
        assert copied.config == entry.config
        assert copied._declarations.keys().isdisjoint(entry._declarations)
    remove_source()
    assert_indexes(source, set())
    assert_indexes(target, {"group", "group.value"})


def assert_indexes(schema: Schema, paths: set[str]) -> None:
    entries: dict[str, RefEntry[Any]] = schema._entries
    tree_entries: dict[str, RefEntry[Any]] = {}

    def collect(tree: dict[str, Any]) -> None:
        for element in tree.values():
            if isinstance(element, RefEntry):
                tree_entries[element.ref.path] = element
            else:
                collect(element)

    collect(schema._element_at(()))
    assert entries == tree_entries
    assert set(entries) == paths | {""}
    for path, entry in entries.items():
        assert schema.resolve_entry(path) is entry
        assert entry.alive


@pytest.mark.parametrize("write_order", tuple(permutations(("a", "a.b", "a.b.c"))))
@pytest.mark.parametrize("delete_order", tuple(permutations(("a", "a.b", "a.b.c"))))
def test_entry_indexes_support_any_parent_child_write_and_delete_order(
    write_order, delete_order
) -> None:
    schema = Schema()
    owner = object()
    entries = {
        "a": RefEntry(Ref("a")),
        "a.b": RefEntry(Ref("a.b")),
        "a.b.c": RefEntry(Ref("a.b.c")),
    }
    for path, entry in entries.items():
        entry._declare(owner, Schema.leaf() if path == "a.b.c" else Schema.container())

    active = set()
    for path in write_order:
        schema._set_entry(entries[path])
        active.add(path)
        assert_indexes(schema, active)
    assert schema._element_at(("a", "b", "c")) is entries["a.b.c"]

    for path in delete_order:
        entries[path]._undeclare(owner)
        schema._delete_entry(entries[path])
        active.remove(path)
        assert_indexes(schema, active)
    assert schema._element_at(()) == {"": schema._entries[""]}


def test_setting_nested_container_does_not_declare_ancestors() -> None:
    schema = Schema()
    owner = object()
    child = RefEntry(Ref("a.b.c"))
    child._declare(owner, Schema.container())
    schema._set_entry(child)
    assert_indexes(schema, {"a.b.c"})
    assert schema._element_at(("a",)) == {"b": {"c": {"": child}}}

    parent = RefEntry(Ref("a.b"))
    parent._declare(owner, Schema.container())
    schema._set_entry(parent)
    assert_indexes(schema, {"a.b", "a.b.c"})
    assert schema._element_at(("a", "b", "c")) == {"": child}
    parent._undeclare(owner)
    schema._delete_entry(parent)
    assert_indexes(schema, {"a.b.c"})
    assert schema._element_at(("a",)) == {"b": {"c": {"": child}}}
    child._undeclare(owner)
    schema._delete_entry(child)
    assert schema._element_at(()) == {"": schema._entries[""]}


def test_declare_import_and_dispose_with_child_first_traversal(monkeypatch) -> None:
    original = SCHEMA_ENGINE.iter_with_key_path

    def child_first(tree):
        return reversed(tuple(original(tree)))

    monkeypatch.setattr(SCHEMA_ENGINE, "iter_with_key_path", child_first)
    ctx = Context()
    schema = ctx._schema
    remove = schema.declare({"group": {"0": Schema.leaf(int), "empty": {}}})
    assert_indexes(schema, {"group", "group.0", "group.empty"})
    ctx.set("group.0", 1)
    imported = Schema(schema)
    duplicate = schema.declare(schema)
    remove()
    assert ctx.get("group.0") == 1

    def unexpected_config_read(self):
        raise AssertionError("Withdrawal must not read config")

    with monkeypatch.context() as patch:
        patch.setattr(RefEntry, "config", property(unexpected_config_read))
        duplicate()
    assert_indexes(schema, set())
    assert schema._element_at(()) == {"": schema._entries[""]}
    assert not ctx._store._data and not ctx._store._scope_usage[ctx.scope].entries
    assert_indexes(imported, {"group", "group.0", "group.empty"})
    ctx.dispose()


def test_child_first_conflict_rolls_back_unconfigured_ancestor_dicts(
    monkeypatch,
) -> None:
    schema = Schema({"blocked": Schema.leaf(int)})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"blocked": 1})
    original = SCHEMA_ENGINE.iter_with_key_path
    entries = dict(schema._entries)
    owners = {path: dict(entry._declarations) for path, entry in entries.items()}

    def child_first(tree):
        return reversed(tuple(original(tree)))

    monkeypatch.setattr(SCHEMA_ENGINE, "iter_with_key_path", child_first)
    with pytest.raises(ValueError, match="Conflicting Ref configurations"):
        schema.declare(
            {
                "blocked": {"child": Schema.leaf()},
                "temporary": {"nested": {"leaf": Schema.leaf()}},
            }
        )
    assert schema._entries == entries
    assert schema._child_names(()) == ("blocked",)
    for path, entry in entries.items():
        assert entry._declarations == owners[path]
    assert ctx.get("blocked") == 1
    ctx.dispose()


def test_schema_indexes_share_entries_through_overlapping_declarations() -> None:
    schema = Schema()
    left = schema.declare({"group": {"left": Schema.leaf(), "empty": {}}})
    right = schema.declare({"group": {"right": Schema.leaf()}})
    duplicate = schema.declare({"group": {"left": Schema.leaf()}})
    group = schema.resolve_entry("group")
    left_entry = schema.resolve_entry("group.left")
    assert_indexes(schema, {"group", "group.left", "group.right", "group.empty"})
    assert len(group._declarations) == 3
    assert len(left_entry._declarations) == 2

    left()
    assert_indexes(schema, {"group", "group.left", "group.right"})
    assert schema.resolve_entry("group.left") is left_entry
    duplicate()
    assert_indexes(schema, {"group", "group.right"})
    assert schema.resolve_entry("group") is group
    right()
    assert_indexes(schema, set())


def test_schema_redeclaration_does_not_retain_old_index_entries() -> None:
    schema = Schema()
    old_entries = []
    for _ in range(100):
        remove = schema.declare({"group": {"value": Schema.leaf()}})
        old = schema.resolve_entry("group.value")
        old_entries.append(weakref.ref(old))
        remove()
        assert_indexes(schema, set())
        replace = schema.declare({"group": Schema.leaf()})
        schema._delete_entry(old)
        remove()
        assert_indexes(schema, {"group"})
        replace()
    del old
    gc.collect()
    assert all(entry() is None for entry in old_entries)


def test_schema_index_and_tree_stay_unchanged_after_conflict() -> None:
    schema = Schema({"group": {"value": Schema.leaf(int)}})
    original = schema.resolve_entry("group.value")
    claims = original._declarations.copy()
    with pytest.raises(ValueError, match="Conflicting"):
        schema.declare({"added": Schema.leaf(), "group": {"value": Schema.leaf(str)}})
    assert_indexes(schema, {"group", "group.value"})
    assert original._declarations == claims


def test_context_uses_flat_schema_lookup_for_declared_paths(monkeypatch) -> None:
    schema = Schema({"group": {"nested": {"value": Schema.leaf()}}})
    ref = schema.resolve("group.nested.value")
    ctx = Context()
    ctx.declare(schema)

    def unexpected_tree_lookup(*args, **kwargs):
        raise AssertionError("Declared leaf lookup must not traverse the Schema tree")

    with monkeypatch.context() as patch:
        patch.setattr(Schema, "_element_at", unexpected_tree_lookup)
        assert schema.resolve("group").path == "group"
        ctx.set(ref, 1)
        assert ctx.get("group.nested.value") == 1
        ctx.set("group.nested.value", 2)
        assert ctx.get(ref) == 2
        ctx.delete(ref)
        assert not ctx.exists(ref)
    ctx.dispose()


def test_schema_keys_and_context_keys_distinguish_declarations_from_visible_values():
    schema = Schema({"group": {"left": Schema.leaf(), "right": Schema.leaf()}})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"group.right": 2})
    assert schema._child_names(("group",)) == ("left", "right")
    assert ctx.get("group").keys() == ("right",)
    ctx.dispose()


def test_schema_root_survives_overlapping_declarations_and_self_import() -> None:
    schema = Schema()
    root = schema.resolve_entry("")
    initial_claims = root._declarations.copy()
    assert root.ref.parts == ()
    assert_indexes(schema, set())

    remove = schema.declare({"": Schema.container(), "group": {"value": Schema.leaf()}})
    duplicate = schema.declare(schema)
    assert schema.resolve_entry("") is root
    remove()
    assert_indexes(schema, {"group", "group.value"})
    duplicate()
    duplicate()
    assert_indexes(schema, set())
    assert schema.resolve_entry("") is root
    assert root._declarations == initial_claims


@pytest.mark.parametrize("source_first", [False, True])
def test_imported_schema_roots_and_claims_are_independent(source_first: bool) -> None:
    source = Schema()
    remove_source = source.declare({"group": {"value": Schema.leaf()}})
    target = Schema()
    remove_target = target.declare(source)
    assert source.resolve_entry("") is not target.resolve_entry("")
    if source_first:
        remove_source()
        assert_indexes(source, set())
        assert_indexes(target, {"group", "group.value"})
        remove_target()
    else:
        remove_target()
        assert_indexes(target, set())
        assert_indexes(source, {"group", "group.value"})
        remove_source()
    assert_indexes(source, set())
    assert_indexes(target, set())


def test_invalid_root_config_and_recursive_input_leave_existing_schema_unchanged() -> (
    None
):
    schema = Schema({"stable": Schema.leaf()})
    root = schema.resolve_entry("")
    claims = root._declarations.copy()
    with pytest.raises(TypeError, match=r"empty key.*Schema\.container"):
        schema.declare({"": Schema.leaf(), "new": Schema.leaf()})

    cyclic: dict[str, Any] = {}
    cyclic["child"] = cyclic
    with pytest.raises(RecursionError):
        schema.declare({"new": Schema.leaf(), "cycle": cyclic})
    assert_indexes(schema, {"stable"})
    assert root._declarations == claims


def test_schema_root_does_not_keep_its_schema_alive() -> None:
    schema = Schema()
    root = schema.resolve_entry("")
    claims = root._declarations.copy()
    remove = schema.declare({})
    schema_ref = weakref.ref(schema)
    del schema
    gc.collect()
    assert schema_ref() is not None
    remove()
    gc.collect()
    assert schema_ref() is None
    assert root._declarations == claims
