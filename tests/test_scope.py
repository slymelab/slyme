from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from slyme.context import Scope


def test_scope_identity_is_not_derived_from_its_label_or_parents() -> None:
    root = Scope("root")
    same_description = Scope("root")

    assert root is not same_description
    assert root != same_description
    assert root.mro == (root,)
    assert same_description.mro == (same_description,)


def test_scope_uses_c3_for_a_diamond() -> None:
    root = Scope("root")
    left = root.fork(label="left")
    right = root.fork(label="right")
    child = Scope("child", parents=(left, right))

    assert child.parents == (left, right)
    assert child.mro == (child, left, right, root)


def test_scope_can_mix_unrelated_visibility_roots() -> None:
    application = Scope("application")
    shared_feature = Scope("shared-feature")
    agent = Scope("agent", parents=(application, shared_feature))

    assert agent.mro == (agent, application, shared_feature)


def test_single_parent_forks_preserve_the_parents_complete_c3_order() -> None:
    root = Scope("root")
    left = root.fork(label="left")
    right = root.fork(label="right")
    parent = Scope(parents=(left, right))
    lineage = [parent, left, right, root]

    for _ in range(64):
        child = parent.fork()
        lineage.insert(0, child)
        assert child.parents == (parent,)
        assert child.mro == tuple(lineage)
        assert parent.mro == tuple(lineage[1:])
        parent = child


def test_scope_fork_is_explicitly_single_parent() -> None:
    root = Scope("root")
    other = Scope("other")

    child = root.fork(label="child")
    assert child.parents == (root,)
    with pytest.raises(TypeError):
        root.fork(other)  # type: ignore[call-arg]


def test_scope_rejects_an_inconsistent_c3_graph() -> None:
    root = Scope("root")
    x = root.fork(label="x")
    y = root.fork(label="y")
    xy = Scope("xy", parents=(x, y))
    yx = Scope("yx", parents=(y, x))

    with pytest.raises(TypeError, match="consistent Scope C3"):
        Scope(parents=(xy, yx))


def test_scope_rejects_duplicate_direct_parents() -> None:
    root = Scope("root")

    with pytest.raises(TypeError, match="duplicate direct parents"):
        Scope(parents=(root, root))


def test_scope_preserves_parents_and_is_immutable() -> None:
    root = Scope("root")
    child = Scope("child", (root,))

    assert child.parents == (root,)
    with pytest.raises(FrozenInstanceError):
        child.label = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        child.parents = ()  # type: ignore[misc]


def test_scope_find_returns_visible_scopes_or_an_explicit_default() -> None:
    root = Scope("root")
    feature = root.fork(label="feature")
    agent = feature.fork(label="agent")

    assert agent.find("agent") is agent
    assert agent.find("feature") is feature
    assert agent.find("root") is root
    with pytest.raises(LookupError, match="No visible Scope"):
        agent.find("missing")
    fallback = Scope("fallback")
    assert agent.find("missing", fallback) is fallback
    assert agent.find("missing", default=None) is None
    assert agent.find("feature", fallback) is feature
    assert agent.find("feature", None) is feature


def test_scope_find_uses_first_c3_match_and_find_all_returns_each_match() -> None:
    root = Scope("shared")
    left = root.fork(label="shared")
    right = root.fork(label="shared")
    child = Scope("child", parents=(left, right))

    assert child.find("shared") is left
    assert child.find_all("shared") == (left, right, root)
    assert child.find_all("missing") == ()


def test_scope_labels_use_equality_without_defining_scope_identity() -> None:
    root = Scope(label={"kind": "shared"})
    child = root.fork(label={"kind": "shared"})

    assert len({root, child}) == 2
    assert child.find({"kind": "shared"}) is child
    assert child.find_all({"kind": "shared"}) == (child, root)
