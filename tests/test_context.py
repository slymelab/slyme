from __future__ import annotations

from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

import slyme.context as context_module
from slyme.context import (
    ARG,
    DIFF_MISSING,
    Arg,
    Context,
    ContextConfig,
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

R = RefFactory(
    {
        "a": {"b": ..., "keep": ..., "new": ..., "old": ...},
        "absent": ...,
        "added": ...,
        "age": ...,
        "application": {"input": ...},
        "blocked": {"child": ...},
        "branch": {"added": ..., "marker": ..., "value": ...},
        "c": ...,
        "changed": ...,
        "long": ...,
        "missing": ...,
        "name": ...,
        "nested": {"x": ...},
        "not_present": ...,
        "other": ...,
        "parent": {"child": ...},
        "point": {"label": ..., "x": ..., "y": ...},
        "removed": ...,
        "same": ...,
        "settings": ...,
        "short": ...,
        "user": {"age": ..., "name": ..., "unknown": ...},
    }
)


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


def test_unbound_ref_declarations_bind_immutably() -> None:
    metadata = {"source": "schema"}
    declaration = Ref(metadata=metadata)
    metadata["late"] = True

    assert declaration.path is None
    assert not declaration.is_bound
    assert declaration.parts == ()
    assert declaration.metadata == {"source": "schema"}
    assert "<unbound>" in repr(declaration)
    with pytest.raises(TypeError, match="not hashable"):
        hash(declaration)
    with pytest.raises(ValueError, match="has no path"):
        to_ref(declaration)
    with pytest.raises(ValueError, match="has no path"):
        declaration.at("child")

    bound = declaration.bind("input.value")
    assert declaration.path is None
    assert bound.bound_path == "input.value"
    assert bound.metadata == {"source": "schema"}
    assert bound.bind("input.value") is bound
    with pytest.raises(ValueError, match="already bound"):
        bound.bind("output.value")


def test_ref_factory_is_immutable_and_normalizes() -> None:
    factory = R.application.input
    assert isinstance(factory, RefFactory)
    assert repr(factory) == "RefFactory(path='application.input')"
    assert factory().path == "application.input"
    assert to_ref(factory) == Ref("application.input")
    ref = Ref("already.normal")
    assert to_ref(ref) is ref

    with pytest.raises(AttributeError, match="immutable"):
        factory.value = 1  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match="immutable"):
        del factory.input
    with pytest.raises(ValueError, match="Empty ref"):
        R()


def test_ref_factory_schema_binds_and_freezes_entries() -> None:
    branch_metadata = {"description": "inputs"}
    input_schema: dict[str, Any] = {
        "": Ref(metadata=branch_metadata),
        "articles": ...,
        "count": Ref(metadata={ARG: Arg(type=int, required=True)}),
    }
    raw_schema: dict[str, Any] = {
        "input": input_schema,
        "args": ...,
    }

    refs = RefFactory(raw_schema)
    input_schema["late"] = ...
    raw_schema["other"] = ...
    branch_metadata["late"] = True

    assert isinstance(refs.input, RefFactory)
    assert isinstance(refs.input.articles, RefFactory)
    assert refs.input().path == "input"
    assert refs.input().metadata == {"description": "inputs"}
    assert refs.input.articles().path == "input.articles"
    assert refs.args().path == "args"
    assert refs.input.articles() is refs.input.articles()
    assert to_ref(refs.input.count).metadata[ARG].required
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        refs.input(metadata={"extra": True})  # type: ignore[call-arg]

    updated_refs = refs.merge(
        {
            "input": {
                "": Ref(metadata={"description": "inputs", "extra": True}),
            }
        },
        conflict="replace",
    )
    assert updated_refs.input().metadata == {
        "description": "inputs",
        "extra": True,
    }
    assert refs.input().metadata == {"description": "inputs"}

    with pytest.raises(AttributeError, match="Did you mean 'articles'"):
        refs.input.artcles
    with pytest.raises(AttributeError, match="late"):
        refs.input.late
    with pytest.raises(AttributeError, match="other"):
        refs.other
    with pytest.raises(AttributeError, match="has no entry"):
        refs.args.child
    with pytest.raises(ValueError, match="Empty ref"):
        refs()


def test_ref_factory_schema_validation() -> None:
    assert not hasattr(context_module, "R")
    with pytest.raises(TypeError, match="missing 1 required positional argument"):
        RefFactory()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="schema must be a mapping"):
        RefFactory(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="schema must be a mapping"):
        RefFactory("bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="root.*empty-key"):
        RefFactory({"": Ref()})
    with pytest.raises(ValueError, match="without dots"):
        RefFactory({"bad.path": ...})
    with pytest.raises(ValueError, match="reserved"):
        RefFactory({"merge": ...})
    with pytest.raises(TypeError, match="mapping, Ref, or Ellipsis"):
        RefFactory({"bad": None})
    with pytest.raises(TypeError, match="empty key"):
        RefFactory({"bad": {"": None}})
    with pytest.raises(ValueError, match="already bound"):
        RefFactory({"actual": Ref("different")})

    cyclic: dict[str, Any] = {}
    cyclic["again"] = cyclic
    with pytest.raises(ValueError, match="Cyclic"):
        RefFactory({"cycle": cyclic})

    refs = RefFactory({"without": ...})
    assert refs.without().path == "without"


def test_ref_factory_schema_merge_is_immutable() -> None:
    base = RefFactory(
        {
            "input": {
                "": Ref(metadata={"description": "inputs"}),
                "a": ...,
            },
            "keep": ...,
        }
    )
    extension = RefFactory(
        {
            "input": {
                "b": ...,
            },
            "output": {
                "result": ...,
            },
        }
    )

    merged = base | extension
    assert merged.input().metadata == {"description": "inputs"}
    assert merged.input.a().path == "input.a"
    assert merged.input.b().path == "input.b"
    assert merged.output.result().path == "output.result"
    with pytest.raises(AttributeError):
        base.input.b
    with pytest.raises(AttributeError):
        extension.input.a

    with pytest.raises(ValueError, match="Ref leaves.*input.a"):
        base | {"input": {"a": ...}}
    enriched = base.merge(
        {"input": {"a": Ref(metadata={"source": "extension"})}},
        conflict="replace",
    )
    assert enriched.input.a().metadata == {"source": "extension"}

    leaf = RefFactory({"entry": ...})
    container = RefFactory({"entry": {"child": ...}})
    empty_container = RefFactory({"entry": {}})
    assert empty_container.entry().path == "entry"
    with pytest.raises(ValueError, match="leaf.*container"):
        leaf | container
    with pytest.raises(ValueError, match="leaf.*container"):
        leaf | empty_container
    with pytest.raises(ValueError, match="leaf.*container"):
        leaf.merge(container, conflict="replace")
    with pytest.raises(ValueError, match="container.*leaf"):
        container.merge(leaf, conflict="replace")
    extended_container = empty_container | container
    assert extended_container.entry.child().path == "entry.child"

    implicit = RefFactory({"group": {"left": ...}})
    explicit = RefFactory(
        {
            "group": {
                "": Ref(metadata={"owner": "right"}),
                "right": ...,
            }
        }
    )
    declared = implicit | explicit
    assert declared.group().metadata == {"owner": "right"}
    assert declared.group.left().path == "group.left"
    assert declared.group.right().path == "group.right"

    other_explicit = RefFactory({"group": {"": Ref(metadata={"owner": "replacement"})}})
    with pytest.raises(ValueError, match="container Ref declarations.*group"):
        explicit | other_explicit
    replaced_declaration = explicit.merge(other_explicit, conflict="replace")
    assert replaced_declaration.group().metadata == {"owner": "replacement"}
    assert replaced_declaration.group.right().path == "group.right"

    explicit_default = RefFactory({"group": {"": Ref(), "child": ...}})
    with pytest.raises(ValueError, match="container Ref declarations.*group"):
        explicit_default | explicit

    conflicting = RefFactory({"input": {"": Ref(metadata={"description": "other"})}})
    with pytest.raises(ValueError, match="input"):
        base | conflicting
    replaced = base.merge(conflicting, conflict="replace")
    assert replaced.input().metadata == {"description": "other"}
    assert replaced.input.a().path == "input.a"
    with pytest.raises(ValueError, match="Unknown schema conflict"):
        base.merge(extension, conflict="invalid")  # type: ignore[arg-type]


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


def test_context_requires_explicit_path_drop_to_change_structure_role() -> None:
    ctx = Context()
    ctx.set(R.a.b, 1)

    with pytest.raises(ContextPathError, match="container.*leaf"):
        ctx.set(R.a, 2)
    assert ctx.to_dict() == {"a": {"b": 1}}

    ctx.clear(R.a)
    assert ctx.to_dict() == {"a": {}}
    with pytest.raises(ContextPathError, match="container.*leaf"):
        ctx.set(R.a, 2)

    ctx.set(R.a.b, 3)
    ctx.delete(R.a.b)
    assert ctx.to_dict() == {"a": {}}
    with pytest.raises(ContextPathError, match="container.*leaf"):
        ctx.set(R.a, 4)

    ctx.delete(R.a)
    ctx.set(R.a, 5)
    with pytest.raises(ContextPathError, match="blocked"):
        ctx.set(R.a.b, 6)

    ctx.mutate(updates={R.a.b: 7}, drops=[R.a])
    assert ctx.to_dict() == {"a": {"b": 7}}
    with pytest.raises(ContextPathError, match="container.*leaf"):
        ctx.mutate(updates={R.a: 8}, drops=[R.a.b])
    assert ctx.to_dict() == {"a": {"b": 7}}

    ctx.mutate(updates={R.a: 9}, drops=[R.a])
    assert ctx.to_dict() == {"a": 9}


def test_context_mutation_validates_before_inplace_apply() -> None:
    ctx = Context()
    ctx.update({R.a.b: 1, R.blocked: 2, R.other: 3})
    root = ctx.to_context_data()
    branch = ctx.to_context_data(R.a)

    with pytest.raises(ContextPathError, match="blocked"):
        ctx.update({R.a.new: 4, R.blocked.child: 5})
    assert ctx.to_dict() == {"a": {"b": 1}, "blocked": 2, "other": 3}

    ctx.set(R.a.b, 6)
    ctx.set(R.a.new, 7)
    assert ctx.to_context_data() is root
    assert ctx.to_context_data(R.a) is branch
    assert branch == {"b": 6, "new": 7}


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


def test_context_clone_copies_only_context_data_structure() -> None:
    shared_mapping = {"items": [1, 2]}
    shared_marker = object()
    ctx = Context()
    ctx.update(
        {
            R.branch.value: shared_mapping,
            R.branch.marker: shared_marker,
            R.other: [3, 4],
        }
    )

    cloned = ctx.clone()

    assert cloned is not ctx
    assert cloned.to_context_data() is not ctx.to_context_data()
    assert cloned.to_context_data(R.branch) is not ctx.to_context_data(R.branch)
    assert cloned.get(R.branch.value) is shared_mapping
    assert cloned.get(R.branch.marker) is shared_marker
    assert cloned.get(R.other) is ctx.get(R.other)

    cloned.set(R.branch.added, "clone-only")
    cloned.delete(R.branch.marker)
    assert not ctx.exists(R.branch.added)
    assert ctx.exists(R.branch.marker)

    shared_mapping["items"].append(3)
    assert cloned.get(R.branch.value)["items"] == [1, 2, 3]
    assert ctx.get(R.branch.value)["items"] == [1, 2, 3]

    branch_clone = ctx.get(R.branch).clone()
    assert branch_clone.to_dict() == {
        "value": shared_mapping,
        "marker": shared_marker,
    }
    assert branch_clone.to_context_data() is not ctx.to_context_data(R.branch)
