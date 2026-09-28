from __future__ import annotations

import gc
import weakref
from dataclasses import FrozenInstanceError, dataclass
from itertools import permutations

import pytest

from slyme.context import (
    Context,
    Metadata,
    Ref,
    RefContainerConfig,
    RefDeclaration,
    RefEntry,
    RefLeafConfig,
    Schema,
)


@dataclass(frozen=True)
class Description(Metadata):
    text: str


@dataclass(frozen=True)
class EquivalentDescription(Metadata):
    text: str

    def equals(self, other: Metadata) -> bool:
        return type(other) is EquivalentDescription and self.text == other.text


def test_identity_default_is_independent_of_dataclass_equality() -> None:
    first, equivalent = Description("one"), Description("one")
    assert first == equivalent
    assert first.equals(first)
    assert not first.equals(equivalent)
    schema = Schema({"value": Schema.leaf(metadata={"docs": first})})
    cached = schema.resolve_entry("value").config
    duplicate = schema.declare({"value": Schema.leaf(metadata={"docs": first})})
    assert schema.resolve_entry("value").config is cached
    duplicate()
    assert schema.resolve_entry("value").config is cached
    with pytest.raises(ValueError, match="Conflicting metadata.*value.*docs"):
        schema.declare({"value": Schema.leaf(metadata={"docs": equivalent})})
    with pytest.raises(FrozenInstanceError):
        first.text = "other"


@pytest.mark.parametrize("factory", [Schema.leaf, Schema.container])
def test_metadata_mapping_is_a_read_only_snapshot(factory) -> None:
    item = Description("one")
    items = {"docs": item}
    config = factory(metadata=items)
    items["docs"] = Description("two")
    assert config.metadata["docs"] is item
    with pytest.raises(TypeError):
        config.metadata["other"] = item


def test_identity_shortcut_never_calls_equality() -> None:
    class NoComparison(Metadata):
        def equals(self, other: Metadata) -> bool:
            raise AssertionError("Identical markers need no comparison")

        def __eq__(self, other: object) -> bool:
            raise AssertionError("Python equality is not the protocol")

    marker = NoComparison()
    schema = Schema()
    first = schema.declare({"value": Schema.leaf(metadata={"key": marker})})
    second = schema.declare({"value": Schema.leaf(metadata={"key": marker})})
    view = schema.resolve_entry("value").metadata
    first()
    assert schema.resolve_entry("value").metadata is view
    assert view["key"] is marker
    second()


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_equivalent_originals_follow_remaining_declaration_order(order) -> None:
    schema = Schema()
    markers = [EquivalentDescription("one") for _ in range(3)]
    removes = [
        schema.declare({"value": Schema.leaf(metadata={"docs": m})}) for m in markers
    ]
    entry = schema.resolve_entry("value")
    snapshot = entry.metadata
    configs = [d.config for d in entry._declarations.values()]
    active = list(range(3))
    for index in order:
        removes[index]()
        active.remove(index)
        assert snapshot["docs"] is markers[0]
        if active:
            assert entry.metadata["docs"] is markers[active[0]]
            assert entry.config is configs[active[0]]
        else:
            assert not entry.alive
            assert entry._metadata_declarations == {}
            assert snapshot["docs"] is markers[0]
            with pytest.raises(LookupError):
                _ = entry.metadata
            with pytest.raises(LookupError):
                _ = entry.config


def test_only_incoming_distinct_objects_are_compared(monkeypatch) -> None:
    calls = []
    original = EquivalentDescription.equals

    def equals(self, other):
        calls.append((self, other))
        return original(self, other)

    monkeypatch.setattr(EquivalentDescription, "equals", equals)
    a, b, c = [EquivalentDescription("one") for _ in range(3)]
    schema = Schema()
    remove_a = schema.declare({"value": Schema.leaf(metadata={"docs": a})})
    remove_b = schema.declare({"value": Schema.leaf(metadata={"docs": b})})
    remove_c = schema.declare({"value": Schema.leaf(metadata={"docs": c})})
    assert len(calls) == 2
    assert calls[0][0] is a and calls[0][1] is b
    assert calls[1][0] is a and calls[1][1] is c
    entry = schema.resolve_entry("value")
    old = entry.metadata
    remove_a()
    assert entry.metadata["docs"] is b
    assert old["docs"] is a
    cached = entry.metadata
    remove_c()
    assert entry.metadata is cached
    assert len(calls) == 2
    remove_b()
    assert len(calls) == 2


@pytest.mark.parametrize(
    "first, incoming",
    [
        (Schema.leaf(), Schema.container()),
        (Schema.container(), Schema.leaf()),
        (Schema.leaf(), Schema.leaf(mode="register")),
        (Schema.leaf(mode="register"), Schema.leaf()),
    ],
)
def test_structural_conflicts_precede_metadata_comparison(first, incoming) -> None:
    class NoComparison(Metadata):
        def equals(self, other: Metadata) -> bool:
            raise AssertionError("Structural conflicts must fail first")

    def annotate(declaration: RefDeclaration) -> RefDeclaration:
        return RefDeclaration(declaration.config, metadata={"key": NoComparison()})

    entry = RefEntry(Ref("value"))
    entry._declare("first", annotate(first))
    cached = entry.config
    with pytest.raises(ValueError, match="Conflicting Ref configurations"):
        entry._declare("incoming", annotate(incoming))
    assert entry.config is cached
    assert tuple(entry._declarations) == ("first",)


@pytest.mark.parametrize("raises", [False, True])
def test_comparison_failure_rolls_back_without_partial_entry_changes(raises) -> None:
    failure = ValueError("comparison failed")

    class Reject(Metadata):
        def equals(self, other: Metadata) -> bool:
            if raises:
                raise failure
            return False

    original, shared, untouched = Reject(), Metadata(), Metadata()
    schema = Schema(
        {
            "value": Schema.leaf(
                metadata={
                    "shared": shared,
                    "key": original,
                    "untouched": untouched,
                }
            )
        }
    )
    entry = schema.resolve_entry("value")
    snapshot = entry.metadata
    owners = dict(entry._declarations)
    metadata_owners = {
        key: dict(owners) for key, owners in entry._metadata_declarations.items()
    }
    with pytest.raises(ValueError) as caught:
        schema.declare(
            {
                "new": Schema.leaf(),
                "value": Schema.leaf(
                    metadata={
                        "temporary": Metadata(),
                        "shared": shared,
                        "key": Reject(),
                        "unvisited": Metadata(),
                        "untouched": untouched,
                    }
                ),
            }
        )
    if raises:
        assert caught.value is failure
    assert entry.metadata == snapshot
    assert all(entry.metadata[key] is value for key, value in snapshot.items())
    assert entry._declarations == owners
    assert entry._metadata_declarations == metadata_owners
    assert schema.entries == (schema.resolve_entry(""), entry)


def test_later_conflict_withdraws_only_incoming_metadata() -> None:
    original = Description("original")
    schema = Schema(
        {"first": Schema.leaf(metadata={"docs": original}), "last": Schema.leaf()}
    )
    ctx = Context()
    ctx.declare(schema)
    ctx.set("first", "runtime value")
    entry = schema.resolve_entry("first")
    with pytest.raises(ValueError, match="Conflicting Ref configurations"):
        schema.declare(
            {
                "new": {"leaf": Schema.leaf()},
                "first": Schema.leaf(metadata={"temporary": Metadata()}),
                "last": Schema.leaf(mode="register"),
            }
        )
    assert entry.metadata == {"docs": original}
    assert tuple(entry._metadata_declarations) == ("docs",)
    assert ctx.get("first") == "runtime value"
    ctx.dispose()


def test_disjoint_metadata_keys_have_independent_ownership() -> None:
    schema = Schema({"group": {"value": Schema.leaf()}})
    root_marker, leaf_marker = Metadata(), Metadata()
    remove_root = schema.declare({"": Schema.container(metadata={"docs": root_marker})})
    remove_leaf = schema.declare(
        {
            "group": {
                "value": Schema.leaf(
                    metadata={"docs": leaf_marker, "__proto__": root_marker}
                )
            }
        }
    )
    leaf = schema.resolve_entry("group.value")
    cached = leaf.config
    remove_root()
    assert schema.resolve_entry("").metadata == {}
    assert leaf.config is cached
    assert leaf.metadata["docs"] is leaf_marker
    remove_leaf()
    assert leaf.metadata == {}


def test_replacing_representative_releases_old_metadata_and_config() -> None:
    schema = Schema()
    first = EquivalentDescription("one")
    remove_first = schema.declare({"value": Schema.leaf(metadata={"key": first})})
    original_ref = weakref.ref(first)
    entry = schema.resolve_entry("value")
    config_ref = weakref.ref(entry.config)
    assert entry.metadata["key"] is first
    second = EquivalentDescription("one")
    remove_second = schema.declare({"value": Schema.leaf(metadata={"key": second})})
    del first
    remove_first()
    gc.collect()
    assert original_ref() is None
    assert config_ref() is None
    assert entry.metadata["key"] is second
    remove_second()
    assert not entry._declarations and not entry._metadata_declarations


def test_import_snapshots_representatives_without_sharing_ownership() -> None:
    source = Schema()
    a, b = EquivalentDescription("one"), EquivalentDescription("one")
    remove_a = source.declare({"value": Schema.leaf(metadata={"docs": a})})
    remove_b = source.declare({"value": Schema.leaf(metadata={"docs": b})})
    target = Schema(source)
    remove_a()
    assert source.resolve_entry("value").metadata["docs"] is b
    assert target.resolve_entry("value").metadata["docs"] is a
    remove_b()
    assert target.resolve_entry("value").metadata["docs"] is a


def test_empty_root_contributions_keep_original_config() -> None:
    schema = Schema()
    removes = [schema.declare({f"p{i}": Schema.leaf()}) for i in range(100)]
    root = schema.resolve_entry("")
    cached = root.config
    for _ in range(100):
        remove = schema.declare({"temporary": Schema.leaf()})
        assert root.config is cached
        remove()
        assert root.config is cached
    for remove in removes:
        remove()
    assert root.config is cached


def test_entry_can_be_reused_after_its_last_declaration() -> None:
    entry = RefEntry(Ref("value"))
    entry._declare("old", Schema.leaf(metadata={"old": Metadata()}))
    entry._undeclare("old")
    replacement = Schema.container(metadata={"new": Metadata()})
    entry._declare("new", replacement)
    assert entry.config is replacement.config
    assert entry.metadata == replacement.metadata
    assert isinstance(entry.config, RefContainerConfig)


def test_metadata_snapshots_are_lazy_cached_and_only_invalidated_by_visible_changes() -> (
    None
):
    schema = Schema({"value": Schema.leaf()})
    entry = schema.resolve_entry("value")
    assert entry._metadata is None
    empty = entry.metadata
    assert entry.metadata is empty
    marker = Metadata()
    remove = schema.declare({"value": Schema.leaf(metadata={"docs": marker})})
    assert entry._metadata is None
    assert empty == {}
    snapshot = entry.metadata
    assert snapshot["docs"] is marker
    assert entry.metadata is snapshot
    duplicate = schema.declare({"value": Schema.leaf(metadata={"docs": marker})})
    assert entry.metadata is snapshot
    remove()
    assert entry.metadata is snapshot
    duplicate()
    assert entry._metadata is None
    assert entry.metadata == {}
    assert snapshot["docs"] is marker
    with pytest.raises(TypeError):
        snapshot["docs"] = Metadata()


def test_configs_compare_role_and_mode_independently_of_metadata() -> None:
    configs = [RefContainerConfig(), RefLeafConfig(), RefLeafConfig(mode="register")]
    for i, first in enumerate(configs):
        for j, second in enumerate(configs):
            assert first.equals(second) == (i == j)
    assert RefContainerConfig().equals(RefContainerConfig())
    assert RefLeafConfig().equals(RefLeafConfig())
