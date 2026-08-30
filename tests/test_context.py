from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from slyme.context import (
    ARG,
    DIFF_MISSING,
    Arg,
    Context,
    ContextConfig,
    R,
    Ref,
    RefFactory,
    to_ref,
)
from slyme.context.core import (
    ContextData,
    ContextDiff,
    ContextPathError,
    diff_context_data,
)
from slyme.context.tree import CONTEXT_ENGINE, CTX_EVAL_ENGINE


def test_ref_validation_identity_and_metadata() -> None:
    with pytest.raises(ValueError, match="Empty ref"):
        Ref("")
    with pytest.raises(ValueError, match="Invalid ref"):
        Ref("a..b")

    ref = Ref("a.b", {"first": 1})
    same_path = Ref("a.b", {"other": 2})
    assert ref.parts == ("a", "b")
    assert ref == same_path
    assert hash(ref) == hash(same_path)
    assert ref != "a.b"
    assert isinstance(ref.metadata, MappingProxyType)
    assert ref.update_metadata({"second": 2}).metadata == {
        "first": 1,
        "second": 2,
    }
    assert ref.at("c").path == "a.b.c"
    assert ref.at("c", metadata={"fresh": True}).metadata == {"fresh": True}
    assert "metadata=" in repr(ref)


def test_ref_factory_is_immutable_and_normalizes() -> None:
    factory = R.application.input
    assert isinstance(factory, RefFactory)
    assert repr(factory) == "R.application.input"
    assert factory().path == "application.input"
    assert factory(metadata={ARG: Arg(required=True)}).metadata[ARG].required
    assert to_ref(factory) == Ref("application.input")
    ref = Ref("already.normal")
    assert to_ref(ref) is ref

    with pytest.raises(AttributeError, match="immutable"):
        factory.value = 1  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match="immutable"):
        del factory.input
    with pytest.raises(ValueError, match="Empty ref"):
        R()


def test_context_crud_views_and_user_dict_leaves() -> None:
    ctx = Context()
    ctx.set(R.user.name, "Ada")
    ctx.update({R.user.age: 37, R.settings: {"theme": "dark"}})

    assert ctx.get(R.user.name) == "Ada"
    assert ctx.get(R.missing, "fallback") == "fallback"
    assert ctx.exists(R.user.age)
    assert not ctx.exists(R.user.unknown)
    assert set(ctx.keys()) == {"user", "settings"}
    assert set(ctx.keys(R.user)) == {"name", "age"}
    assert ctx.to_dict() == {
        "user": {"name": "Ada", "age": 37},
        "settings": {"theme": "dark"},
    }

    user = ctx.get(R.user)
    assert user.to_dict() == {"name": "Ada", "age": 37}
    assert user.get(R.name) == "Ada"
    assert user.exists(R.age)
    assert set(user.keys()) == {"name", "age"}
    assert user.to_context_data() is ctx.to_context_data(R.user)
    assert ctx.collect_leaves() == {
        "user.name": "Ada",
        "user.age": 37,
        "settings": {"theme": "dark"},
    }

    ctx.delete(R.user.age)
    assert not ctx.exists(R.user.age)
    ctx.clear(R.user)
    assert ctx.to_dict(R.user) == {}
    ctx.drop([R.settings, R.not_present])
    assert ctx.to_dict() == {"user": {}}


def test_context_path_errors_do_not_partially_mutate() -> None:
    ctx = Context()
    ctx.set(R.blocked, 1)

    with pytest.raises(ContextPathError, match="blocked"):
        ctx.set(R.blocked.child, 2)
    assert ctx.to_dict() == {"blocked": 1}

    with pytest.raises(ValueError, match="both parent and child"):
        ctx.update({R.parent: 1, R.parent.child: 2})
    assert ctx.to_dict() == {"blocked": 1}

    with pytest.raises(ContextPathError, match="leaf"):
        list(ctx.keys(R.blocked))
    with pytest.raises(ContextPathError, match="container"):
        ctx.to_context_data(R.blocked)
    with pytest.raises(ContextPathError, match="not a container"):
        ctx.clear(R.blocked)
    with pytest.raises(ContextPathError):
        ctx.get(R.absent)


def test_context_mutation_drop_then_update_semantics() -> None:
    ctx = Context()
    ctx.update({R.a.old: 1, R.a.keep: 2, R.other: 3})

    ctx.mutate(updates={R.a.new: 4}, drops=[R.a])
    assert ctx.to_dict() == {"a": {"new": 4}, "other": 3}

    ctx.mutate(updates={R.a: 5}, drops=[R.a, R.a.new])
    assert ctx.to_dict() == {"a": 5, "other": 3}

    before = ctx.to_dict()
    assert ctx.mutate() is None
    assert ctx.to_dict() == before


def test_update_tree_and_structured_extract() -> None:
    ctx = Context()
    refs = {"position": (R.point.x, R.point.y), "label": R.point.label}
    values = {"position": (3, 4), "label": "p"}
    ctx.update_tree(refs, values)

    assert ctx.extract(refs) == values
    assert ctx.extract([R.point.x, {"y": R.point.y}]) == [3, {"y": 4}]


def test_context_diff_strategies_and_flatten() -> None:
    left = Context()
    right = Context()
    shared: list[int] = []
    left.update({R.same: shared, R.changed: [1], R.nested.x: 1, R.removed: 2})
    right.update({R.same: shared, R.changed: [1], R.nested.x: 3, R.added: 4})

    identity_diff = left.diff(right)
    assert identity_diff.added == {"added": 4}
    assert identity_diff.removed == {"removed": 2}
    assert identity_diff.modified["changed"] == ([1], [1])
    assert identity_diff.nested["nested"].modified == {"x": (1, 3)}
    assert identity_diff.flatten() == {
        "added": (DIFF_MISSING, 4),
        "removed": (2, DIFF_MISSING),
        "changed": ([1], [1]),
        "nested.x": (1, 3),
    }
    assert "added" in repr(identity_diff)

    equality_diff = left.diff(right, strategy="eq")
    assert "changed" not in equality_diff.modified
    assert Context().diff(Context(), strategy="eq") == ContextDiff()
    assert not ContextDiff()
    assert repr(ContextDiff()) == "ContextDiff(no changes)"
    with pytest.raises(ValueError, match="Unknown diff strategy"):
        left.diff(right, strategy="invalid")  # type: ignore[arg-type]


def test_diff_context_data_short_circuits_shared_mapping() -> None:
    data = ContextData({"value": 1})
    assert not diff_context_data(data, data)


def test_context_repr_modes_and_invalid_view() -> None:
    ctx = Context()
    ctx.update({R.short: 1, R.long: "abcdefghij"})

    try:
        ContextConfig.set_compact_repr().set_truncated_repr(4)
        compact = repr(ctx)
        assert "Context({'short': 1, 'long': 'abc...})" == compact
        ContextConfig.set_pretty_repr()
        assert "\n" in repr(ctx)
        assert repr(Context()) == "Context()"
        invalid = ctx.get(R.short)
        assert invalid == 1
    finally:
        ContextConfig.set_pretty_repr().set_truncated_repr(100)


@given(
    st.recursive(
        st.integers() | st.text(max_size=20) | st.none(),
        lambda children: (
            st.lists(children, max_size=4)
            | st.tuples(children, children)
            | st.dictionaries(st.text(min_size=1, max_size=6), children, max_size=4)
        ),
        max_leaves=20,
    )
)
def test_context_eval_engine_flatten_round_trip(tree: Any) -> None:
    leaves, definition = CTX_EVAL_ENGINE.flatten(tree)
    assert CTX_EVAL_ENGINE.unflatten(definition, leaves) == tree


def test_context_pytree_engines_preserve_context_identity_contract() -> None:
    ctx = Context()
    ctx.update({R.a.b: 1, R.c: 2})

    leaves, definition = CONTEXT_ENGINE.flatten(ctx)
    rebuilt = CONTEXT_ENGINE.unflatten(definition, leaves)
    assert isinstance(rebuilt, Context)
    assert rebuilt.to_dict() == ctx.to_dict()
