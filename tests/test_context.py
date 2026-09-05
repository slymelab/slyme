from __future__ import annotations

import gc
import weakref
from types import MappingProxyType
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

import slyme.context as context_module
from slyme.context import (
    ARG,
    Arg,
    Context,
    ContextConfig,
    Ref,
    Schema,
    to_ref,
)
from slyme.context.core import ContextPathError
from slyme.context.tree import CTX_EVAL_ENGINE

R = Schema(
    {
        "a": {
            "b": {"": ..., "c": ..., "d": ...},
            "first": ...,
            "from_nearest": ...,
            "from_oldest": ...,
            "keep": ...,
            "left": ...,
            "new": ...,
            "old": ...,
            "regular": ...,
            "root": ...,
            "second": ...,
            "temporary": ...,
        },
        "absent": ...,
        "added": ...,
        "age": ...,
        "application": {"input": ...},
        "blocked": {"child": ...},
        "branch": {"added": ..., "marker": ..., "value": ...},
        "c": ...,
        "changed": ...,
        "group": {
            "child": ...,
            "initial": ...,
            "later": ...,
            "local": ...,
            "parent": ...,
        },
        "long": ...,
        "missing": ...,
        "name": ...,
        "nested": {"x": ...},
        "not_present": ...,
        "other": ...,
        "payload": ...,
        "parent": {"child": ...},
        "point": {"label": ..., "x": ..., "y": ...},
        "removed": ...,
        "same": ...,
        "runtime": {"value": ...},
        "service": ...,
        "settings": {"": ..., "theme": ...},
        "short": ...,
        "user": {"age": ..., "name": ..., "unknown": ...},
        "value": ...,
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


def test_schema_views_are_immutable_and_normalize() -> None:
    view = R.application.input
    assert isinstance(view, Schema)
    assert repr(view) == "Schema(path='application.input')"
    assert view().path == "application.input"
    assert to_ref(view) == Ref("application.input")
    ref = Ref("already.normal")
    assert to_ref(ref) is ref

    with pytest.raises(AttributeError, match="immutable"):
        view.value = 1  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match="immutable"):
        del view.input
    with pytest.raises(ValueError, match="Empty ref"):
        R()


def test_schema_binds_and_freezes_declarations() -> None:
    branch_metadata = {"description": "inputs"}
    input_declarations: dict[str, Any] = {
        "": Ref(metadata=branch_metadata),
        "articles": ...,
        "count": Ref(metadata={ARG: Arg(type=int, required=True)}),
    }
    raw_declarations: dict[str, Any] = {
        "input": input_declarations,
        "args": ...,
    }

    schema = Schema(raw_declarations)
    input_declarations["late"] = ...
    raw_declarations["other"] = ...
    branch_metadata["late"] = True

    assert isinstance(schema.input, Schema)
    assert isinstance(schema.input.articles, Schema)
    assert schema.input().path == "input"
    assert schema.input().metadata == {"description": "inputs"}
    assert schema.input.articles().path == "input.articles"
    assert schema.args().path == "args"
    assert schema.input.articles() is schema.input.articles()
    assert to_ref(schema.input.count).metadata[ARG].required
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        schema.input(metadata={"extra": True})  # type: ignore[call-arg]

    updated_schema = schema.merge(
        {
            "input": {
                "": Ref(metadata={"description": "inputs", "extra": True}),
            }
        },
        conflict="replace",
    )
    assert updated_schema.input().metadata == {
        "description": "inputs",
        "extra": True,
    }
    assert schema.input().metadata == {"description": "inputs"}

    with pytest.raises(AttributeError, match="Did you mean 'articles'"):
        schema.input.artcles
    with pytest.raises(AttributeError, match="late"):
        schema.input.late
    with pytest.raises(AttributeError, match="other"):
        schema.other
    with pytest.raises(AttributeError, match="has no entry"):
        schema.args.child
    with pytest.raises(ValueError, match="Empty ref"):
        schema()


def test_schema_declaration_validation() -> None:
    assert not hasattr(context_module, "R")
    assert not hasattr(context_module, "Refs")
    with pytest.raises(TypeError, match="missing 1 required positional argument"):
        Schema()  # type: ignore[call-arg]
    with pytest.raises(TypeError, match="declarations must be a mapping"):
        Schema(None)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="declarations must be a mapping"):
        Schema("bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="root.*empty-key"):
        Schema({"": Ref()})
    with pytest.raises(ValueError, match="without dots"):
        Schema({"bad.path": ...})
    for reserved in ("from_refs", "merge"):
        with pytest.raises(ValueError, match="reserved"):
            Schema({reserved: ...})
    with pytest.raises(TypeError, match="mapping, Ref, or Ellipsis"):
        Schema({"bad": None})
    with pytest.raises(TypeError, match="empty key"):
        Schema({"bad": {"": None}})
    with pytest.raises(ValueError, match="already bound"):
        Schema({"actual": Ref("different")})

    cyclic: dict[str, Any] = {}
    cyclic["again"] = cyclic
    with pytest.raises(ValueError, match="Cyclic"):
        Schema({"cycle": cyclic})

    schema = Schema({"without": ...})
    assert schema.without().path == "without"


def test_schema_merge_is_immutable() -> None:
    base = Schema(
        {
            "input": {
                "": Ref(metadata={"description": "inputs"}),
                "a": ...,
            },
            "keep": ...,
        }
    )
    extension = Schema(
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

    with pytest.raises(ValueError, match="Ref declarations.*input.a"):
        base | {"input": {"a": ...}}
    enriched = base.merge(
        {"input": {"a": Ref(metadata={"source": "extension"})}},
        conflict="replace",
    )
    assert enriched.input.a().metadata == {"source": "extension"}

    leaf = Schema({"entry": ...})
    container = Schema({"entry": {"child": ...}})
    empty_container = Schema({"entry": {}})
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

    implicit = Schema({"group": {"left": ...}})
    explicit = Schema(
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

    other_explicit = Schema({"group": {"": Ref(metadata={"owner": "replacement"})}})
    with pytest.raises(ValueError, match="container Ref declarations.*group"):
        explicit | other_explicit
    replaced_declaration = explicit.merge(other_explicit, conflict="replace")
    assert replaced_declaration.group().metadata == {"owner": "replacement"}
    assert replaced_declaration.group.right().path == "group.right"

    explicit_default = Schema({"group": {"": Ref(), "child": ...}})
    with pytest.raises(ValueError, match="container Ref declarations.*group"):
        explicit_default | explicit

    conflicting = Schema({"input": {"": Ref(metadata={"description": "other"})}})
    with pytest.raises(ValueError, match="input"):
        base | conflicting
    replaced = base.merge(conflicting, conflict="replace")
    assert replaced.input().metadata == {"description": "other"}
    assert replaced.input.a().path == "input.a"
    with pytest.raises(ValueError, match="Unknown Schema conflict"):
        base.merge(extension, conflict="invalid")  # type: ignore[arg-type]


def test_schema_can_be_built_from_bound_refs() -> None:
    schema = Schema.from_refs(
        [
            Ref("input", metadata={"kind": "container"}),
            Ref("input.value", metadata={"kind": "leaf"}),
            Ref("output.value"),
        ]
    )

    assert schema.input().metadata == {"kind": "container"}
    assert schema.input.value().metadata == {"kind": "leaf"}
    assert schema.output.value().path == "output.value"


def test_context_schema_is_shared_and_declared_monotonically() -> None:
    core = Schema({"plugin": {"base": ...}})
    extension = Schema({"plugin": {"extra": ...}})
    root = Context(schema=core)
    child = root.fork()
    plugin_view = child.schema.plugin

    child.declare(extension)

    assert child.schema is root.schema
    assert plugin_view.extra().path == "plugin.extra"
    child.set(extension.plugin.extra, 1)
    assert child.get(root.schema.plugin.extra) == 1

    root.declare(extension)
    with pytest.raises(ValueError, match="plugin.extra"):
        root.declare(Schema({"plugin": {"extra": ...}}))


def test_context_rejects_undeclared_refs_and_unrelated_parents() -> None:
    root = Context(schema=R)
    unknown = Ref("unknown.path")

    with pytest.raises(ContextPathError, match="not declared"):
        root.get(unknown, None)
    with pytest.raises(ContextPathError, match="not declared"):
        root.exists(unknown)
    with pytest.raises(ContextPathError, match="not declared"):
        root.set(unknown, 1)
    with pytest.raises(ContextPathError, match="not declared"):
        root.delete(unknown)

    unrelated = Context(schema=R)
    assert unrelated.root is unrelated
    assert unrelated.schema is not root.schema
    with pytest.raises(TypeError, match="same application root"):
        root.fork(unrelated)
    with pytest.raises(TypeError, match="inherits its schema"):
        Context(parents=(root,), schema=R)


def test_context_crud_views_and_user_dict_leaves() -> None:
    settings = {"theme": "dark"}
    ctx = Context(
        {
            R.user.name: "Ada",
            R.user.age: 37,
            R.settings: settings,
        },
        schema=R,
    )

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
    assert ctx.flatten() == {
        R.user.name(): "Ada",
        R.user.age(): 37,
        R.settings(): settings,
    }
    assert user.flatten() == {Ref("name"): "Ada", Ref("age"): 37}

    ctx.delete(R.user.age)
    assert not ctx.exists(R.user.age)
    ctx.delete(R.user.name)
    assert not ctx.exists(R.user)
    ctx.drop([R.settings, R.not_present])
    assert ctx.to_dict() == {}


def test_context_constructor_requires_a_ref_mapping() -> None:
    with pytest.raises(TypeError, match="must be a mapping"):
        Context([])  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="Ref or Schema"):
        Context({"path": 1})  # type: ignore[dict-item]
    with pytest.raises(ContextPathError, match="not declared"):
        Context({R.value: 1})


def test_context_constructor_accepts_data_and_keyword_only_parents() -> None:
    root = Context({R.a.b: 1}, schema=R)
    base = root.fork()
    mixin = root.fork()
    mixin.set(R.other, 2)
    child = Context({R.c: 3}, parents=(base, mixin))

    assert child.parents == (base, mixin)
    assert root.root is root
    assert root.mro == (root,)
    assert child.root is root
    assert child.mro == (child, base, mixin, root)
    assert child.to_dict() == {"c": 3, "a": {"b": 1}, "other": 2}
    assert child.to_dict(local=True) == {"c": 3}

    with pytest.raises(TypeError, match="positional"):
        Context(None, (root,))  # type: ignore[call-arg]


def test_context_path_errors_do_not_partially_mutate() -> None:
    ctx = Context(schema=R)
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
        ctx.to_dict(R.blocked)
    with pytest.raises(ContextPathError):
        ctx.get(R.absent)


def test_context_mutation_drop_then_update_semantics() -> None:
    ctx = Context(schema=R)
    ctx.update({R.a.old: 1, R.a.keep: 2, R.other: 3})

    ctx.mutate(updates={R.a.new: 4}, drops=[R.a])
    assert ctx.to_dict() == {"a": {"new": 4}, "other": 3}

    ctx.mutate(updates={R.a: 5}, drops=[R.a, R.a.new])
    assert ctx.to_dict() == {"a": 5, "other": 3}

    before = ctx.to_dict()
    assert ctx.mutate() is None
    assert ctx.to_dict() == before


def test_context_requires_explicit_path_drop_to_change_structure_role() -> None:
    ctx = Context(schema=R)
    ctx.set(R.a.b, 1)

    with pytest.raises(ContextPathError, match="container.*leaf"):
        ctx.set(R.a, 2)
    assert ctx.to_dict() == {"a": {"b": 1}}

    ctx.delete(R.a.b)
    assert not ctx.exists(R.a)
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
    ctx = Context(schema=R)
    ctx.update({R.a.b: 1, R.blocked: 2, R.other: 3})
    with pytest.raises(ContextPathError, match="blocked"):
        ctx.update({R.a.new: 4, R.blocked.child: 5})
    assert ctx.to_dict() == {"a": {"b": 1}, "blocked": 2, "other": 3}

    ctx.set(R.a.b, 6)
    ctx.set(R.a.new, 7)
    assert ctx.to_dict(R.a) == {"b": 6, "new": 7}


def test_update_tree_and_structured_extract() -> None:
    ctx = Context(schema=R)
    refs = {"position": (R.point.x, R.point.y), "label": R.point.label}
    values = {"position": (3, 4), "label": "p"}
    ctx.update_tree(refs, values)

    assert ctx.extract(refs) == values
    assert ctx.extract([R.point.x, {"y": R.point.y}]) == [3, {"y": 4}]


def test_context_repr_modes_and_invalid_view() -> None:
    ctx = Context(schema=R)
    ctx.update({R.short: 1, R.long: "abcdefghij"})

    try:
        ContextConfig.set_compact_repr().set_truncated_repr(4)
        compact = repr(ctx)
        assert "Context({'short': 1, 'long': 'abc...})" == compact
        ContextConfig.set_pretty_repr()
        assert "\n" in repr(ctx)
        assert repr(Context(schema=R)) == "Context()"
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


def test_context_is_an_opaque_pytree_leaf() -> None:
    ctx = Context(schema=R)
    ctx.update({R.a.b: 1, R.c: 2})

    leaves, definition = CTX_EVAL_ENGINE.flatten(ctx)
    rebuilt = CTX_EVAL_ENGINE.unflatten(definition, leaves)
    assert rebuilt is ctx


def test_flatten_reconstructs_context_without_copying_leaf_values() -> None:
    shared_mapping = {"items": [1, 2]}
    shared_marker = object()
    ctx = Context(
        {
            R.branch.value: shared_mapping,
            R.branch.marker: shared_marker,
            R.other: shared_mapping,
        },
        schema=R,
    )

    flattened = ctx.flatten()
    rebuilt = Context(flattened, schema=ctx.schema)

    assert flattened == {
        R.branch.value(): shared_mapping,
        R.branch.marker(): shared_marker,
        R.other(): shared_mapping,
    }
    assert rebuilt.get(R.branch.value) is shared_mapping
    assert rebuilt.get(R.branch.marker) is shared_marker
    assert rebuilt.get(R.other) is shared_mapping

    rebuilt.set(R.branch.added, "rebuilt-only")
    rebuilt.delete(R.branch.marker)
    assert not ctx.exists(R.branch.added)
    assert ctx.exists(R.branch.marker)

    shared_mapping["items"].append(3)
    assert rebuilt.get(R.branch.value)["items"] == [1, 2, 3]
    assert ctx.get(R.branch.value)["items"] == [1, 2, 3]

    assert ctx.get(R.branch).flatten() == {
        Ref("value"): shared_mapping,
        Ref("marker"): shared_marker,
    }


def test_to_dict_is_a_nested_projection_while_flatten_preserves_leaf_paths() -> None:
    mapping_leaf = Context({R.settings: {"theme": "dark"}}, schema=R)
    structured = Context({Ref("settings.theme"): "dark"}, schema=R)

    assert mapping_leaf.to_dict() == structured.to_dict()
    assert mapping_leaf.flatten() == {R.settings(): {"theme": "dark"}}
    assert structured.flatten() == {Ref("settings.theme"): "dark"}


def test_context_fork_has_live_parent_lookup_and_local_writes() -> None:
    value = Ref("runtime.value")
    root = Context(schema=R)
    child = root.fork()
    sibling = root.fork()

    root.set(value, 1)
    assert child.get(value) == 1
    assert not child.exists(value, local=True)

    child.set(value, 2)
    assert child.get(value) == 2
    assert child.get(value, local=True) == 2
    assert root.get(value) == 1
    assert sibling.get(value) == 1

    root.set(value, 3)
    assert child.get(value) == 2
    assert sibling.get(value) == 3
    child.delete(value)
    assert child.get(value) == 3
    assert "data" not in vars(child)
    assert not hasattr(child, "parent_contexts")


def test_context_read_operations_can_select_local_or_effective_data() -> None:
    root = Context(schema=R)
    root.set(Ref("group.parent"), 1)
    child = root.fork()
    child.set(Ref("group.child"), 2)

    assert tuple(child.keys(Ref("group"))) == ("child", "parent")
    assert tuple(child.keys(Ref("group"), local=True)) == ("child",)
    assert child.to_dict() == {"group": {"child": 2, "parent": 1}}
    assert child.to_dict(local=True) == {"group": {"child": 2}}
    assert child.flatten() == {
        Ref("group.child"): 2,
        Ref("group.parent"): 1,
    }
    assert child.flatten(local=True) == {Ref("group.child"): 2}
    assert child.extract([Ref("group.child")], local=True) == [2]


def test_context_views_follow_later_parent_and_child_changes() -> None:
    root = Context(schema=R)
    root.set(Ref("group.initial"), 1)
    child = root.fork()
    view = child.get(Ref("group"))

    root.set(Ref("group.later"), 2)
    child.set(Ref("group.local"), 3)
    assert view.to_dict() == {"local": 3, "initial": 1, "later": 2}

    child.mutate(updates={Ref("group"): 4}, drops=[Ref("group")])
    with pytest.raises(ContextPathError):
        view.to_dict()


def test_context_uses_c3_for_multiple_parents() -> None:
    value = Ref("value")
    root = Context(schema=R)
    left = root.fork()
    right = root.fork()
    child = left.fork(right)

    root.set(value, "root")
    right.set(value, "right")
    left.set(value, "left")

    assert child.mro == (child, left, right, root)
    assert child.get(value) == "left"

    x = root.fork()
    y = root.fork()
    xy = x.fork(y)
    yx = y.fork(x)
    with pytest.raises(TypeError, match="C3"):
        xy.fork(yx)
    with pytest.raises(TypeError, match="duplicate"):
        root.fork(root)


def test_context_structural_lookup_obeys_leaf_barriers() -> None:
    root = Context(schema=R)
    root.set(Ref("a.b.c"), 1)

    leaf_child = root.fork()
    leaf_child.set(Ref("a.b"), 2)
    assert leaf_child.get(Ref("a.b")) == 2
    assert not leaf_child.exists(Ref("a.b.c"))

    merged_child = root.fork()
    merged_child.set(Ref("a.b.d"), 3)
    assert merged_child.to_dict(Ref("a.b")) == {"d": 3, "c": 1}

    leaf_root = Context(schema=R)
    leaf_root.set(Ref("a"), 4)
    reopened = leaf_root.fork()
    reopened.set(Ref("a.b.c"), 5)
    assert reopened.to_dict(Ref("a")) == {"b": {"c": 5}}

    oldest = Context(schema=R)
    oldest.set(Ref("a.from_oldest"), True)
    blocker = oldest.fork()
    blocker.set(Ref("a"), "blocked")
    nearest = blocker.fork()
    nearest.set(Ref("a.from_nearest"), True)
    assert nearest.to_dict(Ref("a")) == {"from_nearest": True}


def test_c3_branch_merge_stops_at_an_intermediate_leaf() -> None:
    root = Context(schema=R)
    root.set(Ref("a.root"), 1)
    left = root.fork()
    left.set(Ref("a.left"), 2)
    right = root.fork()
    right.set(Ref("a"), "barrier")

    child = left.fork(right)

    assert child.mro == (child, left, right, root)
    assert child.to_dict(Ref("a")) == {"left": 2}


def test_context_add_is_local_immutable_and_exactly_reversible() -> None:
    value = Ref("service")
    root = Context(schema=R)
    remove_root = root.add(value, "root")

    with pytest.raises(ContextPathError, match="existing local"):
        root.add(value, "other")
    with pytest.raises(ContextPathError, match="Cannot replace added"):
        root.set(value, "other")

    child = root.fork()
    remove_child = child.add(value, "child")
    assert child.get(value) == "child"
    remove_child()
    remove_child()
    assert child.get(value) == "root"

    remove_root()
    assert not root.exists(value)


def test_context_add_disposer_restores_an_inherited_leaf_barrier() -> None:
    root = Context(schema=R)
    root.set(Ref("a"), "root-leaf")
    child = root.fork()

    remove = child.add(Ref("a.b.c"), "temporary")
    assert child.get(Ref("a.b.c")) == "temporary"
    remove()

    assert child.get(Ref("a")) == "root-leaf"
    assert not child.exists(Ref("a.b"), local=True)


def test_context_add_prunes_shared_temporary_branches_in_any_order() -> None:
    root = Context(schema=R)
    root.set(Ref("a"), "root-leaf")
    child = root.fork()

    remove_first = child.add(Ref("a.first"), 1)
    remove_second = child.add(Ref("a.second"), 2)
    remove_first()
    assert child.to_dict(Ref("a")) == {"second": 2}
    remove_second()

    assert child.get(Ref("a")) == "root-leaf"


def test_deleting_an_added_leaf_prunes_its_temporary_branches() -> None:
    root = Context(schema=R)
    root.set(Ref("a"), "root-leaf")
    child = root.fork()

    stale_disposer = child.add(Ref("a.old"), 1)
    child.delete(Ref("a.old"))
    assert child.get(Ref("a")) == "root-leaf"

    remove_new = child.add(Ref("a.new"), 2)
    remove_new()
    assert child.get(Ref("a")) == "root-leaf"
    stale_disposer()


def test_context_add_disposer_does_not_retain_removed_payload() -> None:
    class Payload:
        pass

    ctx = Context(schema=R)
    payload = Payload()
    payload_ref = weakref.ref(payload)
    dispose = ctx.add(Ref("payload"), payload)

    dispose()
    del payload
    gc.collect()

    assert payload_ref() is None
    dispose()


def test_context_removes_a_container_after_its_last_local_leaf() -> None:
    root = Context(schema=R)
    root.set(Ref("a"), "root-leaf")
    child = root.fork()

    remove = child.add(Ref("a.temporary"), 1)
    child.set(Ref("a.regular"), 2)
    child.delete(Ref("a.regular"))
    remove()

    assert child.get(Ref("a")) == "root-leaf"
    assert not child.exists(Ref("a"), local=True)


def test_context_keeps_user_mappings_as_atomic_leaves() -> None:
    settings = {"theme": "dark"}
    ctx = Context({Ref("settings"): settings}, schema=R)

    assert ctx.get(Ref("settings")) is settings
    assert not ctx.exists(Ref("settings.theme"))
    assert ctx.flatten() == {Ref("settings"): settings}


def test_flatten_can_materialize_an_effective_context() -> None:
    value = Ref("value")
    root = Context(schema=R)
    root.set(value, 1)
    child = root.fork()

    snapshot = Context(child.flatten(), schema=child.schema)
    assert snapshot.parents == ()
    assert snapshot.get(value) == 1

    root.set(value, 2)
    assert child.get(value) == 2
    assert snapshot.get(value) == 1


def test_flatten_does_not_copy_add_lifecycle_guards() -> None:
    value = Ref("value")
    ctx = Context(schema=R)
    dispose = ctx.add(value, 1)

    snapshot = Context(ctx.flatten(), schema=ctx.schema)
    snapshot.set(value, 2)

    assert snapshot.get(value) == 2
    assert ctx.get(value) == 1
    dispose()
