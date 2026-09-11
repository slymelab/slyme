from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from slyme.context import Scope


def test_scope_identity_is_not_derived_from_its_name_or_parents() -> None:
    root = Scope("root")
    same_description = Scope("root")

    assert root is not same_description
    assert root != same_description
    assert root.mro == (root,)
    assert same_description.mro == (same_description,)


def test_scope_uses_c3_for_a_diamond() -> None:
    root = Scope("root")
    left = root.fork(name="left")
    right = root.fork(name="right")
    child = Scope("child", parents=(left, right))

    assert child.parents == (left, right)
    assert child.mro == (child, left, right, root)


def test_scope_can_mix_unrelated_visibility_roots() -> None:
    application = Scope("application")
    shared_feature = Scope("shared-feature")
    agent = Scope("agent", parents=(application, shared_feature))

    assert agent.mro == (agent, application, shared_feature)


def test_scope_fork_is_explicitly_single_parent() -> None:
    root = Scope("root")
    other = Scope("other")

    child = root.fork(name="child")
    assert child.parents == (root,)
    with pytest.raises(TypeError):
        root.fork(other)  # type: ignore[call-arg]


def test_scope_rejects_an_inconsistent_c3_graph() -> None:
    root = Scope("root")
    x = root.fork(name="x")
    y = root.fork(name="y")
    xy = Scope("xy", parents=(x, y))
    yx = Scope("yx", parents=(y, x))

    with pytest.raises(TypeError, match="consistent Scope C3"):
        Scope(parents=(xy, yx))


def test_scope_validates_direct_parents_and_names() -> None:
    root = Scope("root")

    with pytest.raises(TypeError, match="duplicate direct parents"):
        Scope(parents=(root, root))
    with pytest.raises(TypeError, match="names must be hashable"):
        Scope(name=[])  # type: ignore[arg-type]


def test_scope_normalizes_parent_iterables_and_is_immutable() -> None:
    root = Scope("root")
    child = Scope("child", [root])  # type: ignore[arg-type]

    assert child.parents == (root,)
    with pytest.raises(FrozenInstanceError):
        child.name = "changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        child.parents = ()  # type: ignore[misc]


def test_scope_find_returns_the_unique_visible_named_scope() -> None:
    root = Scope("root")
    feature = root.fork(name="feature")
    agent = feature.fork(name="agent")

    assert agent.find("agent") is agent
    assert agent.find("feature") is feature
    assert agent.find("root") is root
    with pytest.raises(LookupError, match="No visible Scope"):
        agent.find("missing")
    with pytest.raises(TypeError, match="names must be hashable"):
        agent.find([])  # type: ignore[arg-type]


def test_scope_find_rejects_ambiguous_visible_names() -> None:
    left = Scope("shared")
    right = Scope("shared")
    child = Scope("child", parents=(left, right))

    with pytest.raises(LookupError, match="Multiple visible Scopes"):
        child.find("shared")
