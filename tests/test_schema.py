from __future__ import annotations

import gc
import weakref
from typing import Any

import pytest

from slyme.context import Context, Schema
from slyme.context.schema import _RefEntry


def assert_indexes(schema: Schema, paths: set[str]) -> None:
    entries: dict[str, _RefEntry[Any]] = schema._Schema__entries
    tree_entries: dict[str, _RefEntry[Any]] = {}

    def collect(tree: dict[str, Any]) -> None:
        for node in tree.values():
            if isinstance(node, _RefEntry):
                tree_entries[node.ref.path] = node
            else:
                collect(node)

    collect(schema._node_at(()))
    assert entries == tree_entries
    assert set(entries) == paths
    for path, entry in entries.items():
        assert schema._resolve_entry(path) is entry
        assert entry.declarations


def test_schema_indexes_share_entries_through_overlapping_declarations() -> None:
    schema = Schema()
    left = schema.declare({"group": {"left": Schema.leaf(), "empty": {}}})
    right = schema.declare({"group": {"right": Schema.leaf()}})
    duplicate = schema.declare({"group": {"left": Schema.leaf()}})
    group = schema._resolve_entry("group")
    left_entry = schema._resolve_entry("group.left")
    assert_indexes(schema, {"group", "group.left", "group.right", "group.empty"})
    assert len(group.declarations) == 3
    assert len(left_entry.declarations) == 2

    left()
    assert_indexes(schema, {"group", "group.left", "group.right"})
    assert schema._resolve_entry("group.left") is left_entry
    duplicate()
    assert_indexes(schema, {"group", "group.right"})
    assert schema._resolve_entry("group") is group
    right()
    assert_indexes(schema, set())


def test_schema_redeclaration_does_not_retain_old_index_entries() -> None:
    schema = Schema()
    old_entries = []
    for _ in range(100):
        remove = schema.declare({"group": {"value": Schema.leaf()}})
        old = schema._resolve_entry("group.value")
        old_entries.append(weakref.ref(old))
        remove()
        assert_indexes(schema, set())
        replace = schema.declare({"group": Schema.leaf()})
        schema._remove_entry(old)
        remove()
        assert_indexes(schema, {"group"})
        replace()
    del old
    gc.collect()
    assert all(entry() is None for entry in old_entries)


def test_schema_index_and_tree_stay_unchanged_after_conflict() -> None:
    schema = Schema({"group": {"value": Schema.leaf(int)}})
    original = schema._resolve_entry("group.value")
    claims = set(original.declarations)
    with pytest.raises(ValueError, match="Conflicting"):
        schema.declare({"added": Schema.leaf(), "group": {"value": Schema.leaf(str)}})
    assert_indexes(schema, {"group", "group.value"})
    assert original.declarations == claims


def test_context_uses_flat_schema_lookup_for_declared_paths(monkeypatch) -> None:
    schema = Schema({"group": {"nested": {"value": Schema.leaf()}}})
    ref = schema.resolve("group.nested.value")
    ctx = Context(schema=schema)

    def unexpected_tree_lookup(*args, **kwargs):
        raise AssertionError("Declared leaf lookup must not traverse the Schema tree")

    with monkeypatch.context() as patch:
        patch.setattr(Schema, "_node_at", unexpected_tree_lookup)
        patch.setattr(Schema, "_path_error", unexpected_tree_lookup)
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
    ctx = Context({"group.right": 2}, schema=schema)
    assert schema._child_names(("group",)) == ("left", "right")
    assert ctx.get("group").keys() == ("right",)
    ctx.dispose()
