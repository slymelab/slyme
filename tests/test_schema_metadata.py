from __future__ import annotations

from dataclasses import FrozenInstanceError, dataclass

import pytest

from slyme.context import Context, Metadata, Schema


@dataclass(frozen=True)
class Labels(Metadata):
    values: tuple[str, ...]

    def merge(self, other: Metadata) -> Labels:
        if not isinstance(other, Labels):
            raise ValueError("Incompatible labels")
        return Labels(self.values + other.values)


@dataclass(frozen=True)
class Description(Metadata):
    text: str


def test_default_metadata_merge_uses_identity_not_dataclass_equality() -> None:
    first = Description("one")
    equivalent = Description("one")
    assert first == equivalent
    assert first.merge(first) is first
    with pytest.raises(ValueError, match="Conflicting metadata"):
        first.merge(equivalent)
    with pytest.raises(FrozenInstanceError):
        first.text = "other"
    marker = Metadata()
    assert marker.merge(marker) is marker


@pytest.mark.parametrize("factory", [Schema.leaf, Schema.container])
def test_metadata_mapping_is_a_read_only_snapshot(factory) -> None:
    item = Description("one")
    items = {"docs.description": item}
    config = factory(metadata=items)
    items["docs.description"] = Description("two")
    assert config.metadata["docs.description"] is item
    with pytest.raises(TypeError):
        config.metadata["other"] = item


@pytest.mark.parametrize("factory", [Schema.leaf, Schema.container])
def test_merge_unions_keys_and_combines_matching_items_without_mutation(
    factory,
) -> None:
    description = Description("one")
    label = Labels(("a",))
    left = factory(metadata={"docs.description": description, "app.labels": label})
    right = factory(metadata={"app.labels": label, "__proto__": description})
    merged = left.merge(right)
    assert merged.metadata == {
        "docs.description": description,
        "app.labels": Labels(("a", "a")),
        "__proto__": description,
    }
    assert merged.metadata["docs.description"] is description
    assert left.metadata["app.labels"] is label
    assert right.metadata["app.labels"] is label
    assert "__proto__" not in left.metadata


def test_config_merge_does_not_compare_metadata_values() -> None:
    class NoEquality(Metadata):
        def __eq__(self, other: object) -> bool:
            raise AssertionError("Metadata equality must not be inferred")

    item = NoEquality()
    result = Schema.leaf(metadata={"key": item}).merge(
        Schema.leaf(metadata={"key": item})
    )
    assert result.metadata["key"] is item


def test_behavior_conflict_precedes_metadata_merge() -> None:
    class NoMerge(Metadata):
        def merge(self, other: Metadata) -> Metadata:
            raise AssertionError("Incompatible behavior must fail first")

    item = NoMerge()
    config = Schema.leaf(int, metadata={"key": item})
    for incompatible in (
        Schema.leaf(str, metadata={"key": item}),
        Schema.leaf(int, mode="register", metadata={"key": item}),
        Schema.container(metadata={"key": item}),
    ):
        with pytest.raises(ValueError, match="Conflicting Ref configurations"):
            config.merge(incompatible)


def test_metadata_failure_preserves_schema_and_original_exception() -> None:
    error = ValueError("application conflict")

    class Reject(Metadata):
        def merge(self, other: Metadata) -> Metadata:
            raise error

    schema = Schema({"value": Schema.leaf(metadata={"key": Reject()})})
    entry = schema._entries["value"]
    original = entry.config
    owners = dict(entry._declarations)
    root_owners = dict(schema._entries[""]._declarations)
    with pytest.raises(ValueError) as caught:
        schema.declare(
            {"new": Schema.leaf(), "value": Schema.leaf(metadata={"key": Metadata()})}
        )
    assert caught.value is error
    assert entry.config is original
    assert entry._declarations == owners
    assert schema._entries[""]._declarations == root_owners
    with pytest.raises(KeyError):
        schema.resolve("new")


def test_late_conflict_rolls_back_merged_metadata_and_new_containers() -> None:
    config = Schema.leaf(str, metadata={"labels": Labels(("original",))})
    schema = Schema({"first": config, "last": Schema.leaf(int)})
    ctx = Context()
    ctx.declare(schema)
    ctx.update({"first": "runtime value"})
    entries = dict(schema._entries)
    owners = {path: dict(entry._declarations) for path, entry in entries.items()}

    with pytest.raises(ValueError, match="Conflicting Ref configurations"):
        schema.declare(
            {
                "new": {"leaf": Schema.leaf()},
                "first": Schema.leaf(str, metadata={"labels": Labels(("temporary",))}),
                "last": Schema.leaf(str),
            }
        )

    assert schema._entries == entries
    assert schema._child_names(()) == ("first", "last")
    for path, entry in entries.items():
        assert entry._declarations == owners[path]
    assert entries["first"]._config is None
    assert entries["first"].config is config
    assert ctx.get("first") == "runtime value"
    ctx.dispose()


def test_withdrawal_rebuilds_metadata_in_remaining_declaration_order() -> None:
    schema = Schema({"value": Schema.leaf()})
    releases = [
        schema.declare({"value": Schema.leaf(metadata={"labels": Labels((label,))})})
        for label in ("a", "b", "c")
    ]
    entry = schema._entries["value"]
    assert entry.config.metadata["labels"] == Labels(("a", "b", "c"))
    releases[1]()
    assert entry._config is None
    assert entry.config.metadata["labels"] == Labels(("a", "c"))
    releases[0]()
    assert entry.config.metadata["labels"] == Labels(("c",))
    releases[2]()
    assert entry.config.metadata == {}


def test_root_and_container_metadata_coexist_with_implicit_declarations() -> None:
    schema = Schema({"group": {"value": Schema.leaf()}})
    item = Description("container docs")
    remove = schema.declare(
        {
            "": Schema.container(metadata={"docs": item}),
            "group": {"": Schema.container(metadata={"docs": item})},
        }
    )
    for path in ("", "group"):
        assert schema._entries[path].config.metadata["docs"] is item
    remove()
    for path in ("", "group"):
        assert schema._entries[path].config.metadata == {}


def test_schema_import_snapshots_merged_metadata_without_sharing_owners() -> None:
    source = Schema({"value": Schema.leaf(metadata={"labels": Labels(("a",))})})
    remove = source.declare({"value": Schema.leaf(metadata={"labels": Labels(("b",))})})
    imported = Schema(source)
    remove()
    assert source._entries["value"].config.metadata["labels"] == Labels(("a",))
    assert imported._entries["value"].config.metadata["labels"] == Labels(("a", "b"))


def test_withdrawal_never_merges_metadata(monkeypatch) -> None:
    schema = Schema()
    removes = [
        schema.declare({"value": Schema.leaf(metadata={"labels": Labels((str(i),))})})
        for i in range(3)
    ]

    def fail(self, other):
        raise AssertionError("Withdrawal must not merge metadata")

    monkeypatch.setattr(Labels, "merge", fail)
    for remove in removes:
        remove()
    assert set(schema._entries) == {""}
