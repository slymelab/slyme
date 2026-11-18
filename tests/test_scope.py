from __future__ import annotations

from dataclasses import FrozenInstanceError, replace

import pytest

from slyme.context import Scope


def test_scope_identity_is_not_derived_from_its_label_or_parents() -> None:
    root = Scope(label="root")
    same_description = Scope(label="root")

    assert root is not same_description
    assert root != same_description
    assert tuple(root.lineage()) == (root,)
    assert tuple(same_description.lineage()) == (same_description,)


def test_scope_preserves_parent_when_replaced() -> None:
    parent = Scope(label="parent")
    single = Scope(label="single", parent=parent)
    sequence = Scope(parent=parent)
    assert single.parent == sequence.parent == parent
    assert tuple(single.lineage()) == (single, parent)
    assert tuple(sequence.lineage()) == (sequence, parent)
    renamed = replace(single, label="renamed")
    assert renamed.label == "renamed"
    assert renamed.parent == parent
    assert tuple(renamed.lineage()) == (renamed, parent)
    with pytest.raises(FrozenInstanceError):
        single.parent = None


def test_single_parent_forks_preserve_the_lineage() -> None:
    root = Scope(label="root")
    parent = root
    lineage = [root]

    for _ in range(64):
        child = parent.fork()
        lineage.insert(0, child)
        assert child.parent == parent
        assert tuple(child.lineage()) == tuple(lineage)
        assert tuple(parent.lineage()) == tuple(lineage[1:])
        parent = child


def test_scope_fork_does_not_propagate_the_parents_subclass() -> None:
    class ApplicationScope(Scope):
        def __init__(self, application):
            super().__init__(label=application)

    parent = ApplicationScope("application")
    child = parent.fork(label="child")
    assert type(child) is Scope
    assert child.label == "child"
    assert child.parent == parent
    assert tuple(child.lineage()) == (child, parent)


def test_scope_preserves_parents_and_is_immutable() -> None:
    root = Scope(label="root")
    child = Scope(label="child", parent=root)

    assert child.parent == root
    with pytest.raises(FrozenInstanceError):
        child.label = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        child.parent = None  # type: ignore[misc]


def test_scope_find_returns_visible_scopes_or_an_explicit_default() -> None:
    root = Scope(label="root")
    feature = root.fork(label="feature")
    agent = feature.fork(label="agent")

    assert agent.find("agent") is agent
    assert agent.find("feature") is feature
    assert agent.find("root") is root
    with pytest.raises(LookupError, match="No visible Scope"):
        agent.find("missing")
    fallback = Scope(label="fallback")
    assert agent.find("missing", fallback) is fallback
    assert agent.find("missing", default=None) is None
    assert agent.find("feature", fallback) is feature
    assert agent.find("feature", None) is feature


def test_scope_find_uses_nearest_match_and_find_all_returns_each_match() -> None:
    root = Scope(label="shared")
    left = root.fork(label="shared")
    child = left.fork(label="child")

    assert child.find("shared") is left
    assert child.find_all("shared") == (left, root)
    assert child.find_all("missing") == ()


def test_scope_labels_use_equality_without_defining_scope_identity() -> None:
    root = Scope(label={"kind": "shared"})
    child = root.fork(label={"kind": "shared"})

    assert len({root, child}) == 2
    assert child.find({"kind": "shared"}) is child
    assert child.find_all({"kind": "shared"}) == (child, root)
