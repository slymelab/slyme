from __future__ import annotations

import gc
import inspect
import weakref
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

import slyme.context as context_module
from slyme.context import (
    Context,
    Ref,
    Schema,
    Scope,
)
from slyme.context.core import ContextPathError
from slyme.context.tree import CTX_EVAL_ENGINE

R = Schema(
    {
        "a": {
            "b": {"c": Schema.leaf(), "d": Schema.leaf()},
            "first": Schema.leaf(),
            "from_nearest": Schema.leaf(),
            "from_oldest": Schema.leaf(),
            "keep": Schema.leaf(),
            "left": Schema.leaf(),
            "new": Schema.leaf(),
            "old": Schema.leaf(),
            "regular": Schema.leaf(),
            "root": Schema.leaf(),
            "second": Schema.leaf(),
            "temporary": Schema.leaf(),
        },
        "absent": Schema.leaf(),
        "added": Schema.leaf(),
        "age": Schema.leaf(),
        "application": {"input": Schema.leaf()},
        "blocked": {"child": Schema.leaf()},
        "branch": {
            "added": Schema.leaf(),
            "marker": Schema.leaf(),
            "value": Schema.leaf(),
        },
        "c": Schema.leaf(),
        "changed": Schema.leaf(),
        "group": {
            "child": Schema.leaf(),
            "initial": Schema.leaf(),
            "later": Schema.leaf(),
            "local": Schema.leaf(),
            "parent": Schema.leaf(),
        },
        "long": Schema.leaf(),
        "missing": Schema.leaf(),
        "name": Schema.leaf(),
        "nested": {"x": Schema.leaf()},
        "not_present": Schema.leaf(),
        "other": Schema.leaf(),
        "payload": Schema.leaf(),
        "parent": {"child": Schema.leaf()},
        "point": {"label": Schema.leaf(), "x": Schema.leaf(), "y": Schema.leaf()},
        "removed": Schema.leaf(),
        "same": Schema.leaf(),
        "runtime": {"value": Schema.leaf()},
        "service": Schema.leaf(replaceable=False),
        "settings": Schema.leaf(),
        "short": Schema.leaf(),
        "user": {"age": Schema.leaf(), "name": Schema.leaf(), "unknown": Schema.leaf()},
        "value": Schema.leaf(),
    }
)


def test_ref_is_a_directly_constructible_path_handle() -> None:
    schema = Schema({"input": {"value": Schema.leaf(str)}})

    value = schema.resolve("input.value")
    equivalent = Schema({"input": {"value": Schema.leaf(int)}}).resolve("input.value")

    assert value.path == "input.value"
    assert value.parts == ("input", "value")
    assert value == equivalent
    assert hash(value) == hash(equivalent)
    manual = Ref("input.value")
    assert manual == value

    with pytest.raises(ValueError, match="cannot be empty"):
        Ref("")
    with pytest.raises(ValueError, match="Invalid Ref path"):
        Ref("input..value")


def test_schema_resolves_leaf_and_container_refs() -> None:
    schema = Schema(
        {
            "input": {
                "": Schema.container(),
                "articles": Schema.leaf(list),
                "count": Schema.leaf(int),
            },
            "args": Schema.leaf(),
        }
    )

    assert schema.resolve("input").path == "input"
    assert schema.resolve("input.articles").path == "input.articles"
    assert schema.resolve("input.count").path == "input.count"
    assert schema.resolve("args").path == "args"
    with pytest.raises(KeyError, match="Did you mean 'articles'"):
        schema.resolve("input.artcles")
    with pytest.raises(KeyError, match="has no entry 'child'"):
        schema.resolve("args.child")
    with pytest.raises(ValueError, match="cannot be empty"):
        schema.resolve("")


def test_schema_snapshots_input_and_has_no_attribute_path_api() -> None:
    declarations: dict[str, Any] = {"input": {"value": Schema.leaf()}}
    schema = Schema(declarations)
    declarations["late"] = Schema.leaf()

    assert schema.resolve("input.value").path == "input.value"
    with pytest.raises(KeyError, match="late"):
        schema.resolve("late")
    with pytest.raises(AttributeError):
        schema.input  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match="read-only"):
        schema.input = 1  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match="read-only"):
        del schema.input


def test_schema_declaration_validation() -> None:
    assert not hasattr(context_module, "R")
    assert not hasattr(context_module, "Refs")
    assert isinstance(Schema(), Schema)
    assert isinstance(Schema(None), Schema)
    with pytest.raises(TypeError, match="declarations must be a mapping"):
        Schema("bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="root.*empty-key"):
        Schema({"": Schema.container()})
    with pytest.raises(ValueError, match="without dots"):
        Schema({"bad.path": Schema.leaf()})
    with pytest.raises(TypeError, match=r"mapping or Schema\.leaf"):
        Schema({"bad": None})
    with pytest.raises(TypeError, match=r"mapping or Schema\.leaf"):
        Schema({"bad": ...})
    with pytest.raises(TypeError, match=r"empty key.*Schema\.container"):
        Schema({"bad": {"": None}})
    with pytest.raises(TypeError, match=r"empty key.*Schema\.container"):
        Schema({"bad": {"": Schema.leaf()}})
    with pytest.raises(TypeError, match=r"Schema\.container.*empty key"):
        Schema({"bad": Schema.container()})
    with pytest.raises(TypeError, match="replaceable must be bool"):
        Schema.leaf(replaceable=1)  # type: ignore[arg-type]

    resolved = Schema({"source": Schema.leaf()}).resolve("source")
    with pytest.raises(TypeError, match=r"mapping or Schema\.leaf"):
        Schema({"copied": resolved})

    cyclic: dict[str, Any] = {}
    cyclic["again"] = cyclic
    with pytest.raises(ValueError, match="Cyclic"):
        Schema({"cycle": cyclic})

    names = Schema(
        {
            "declare": Schema.leaf(),
            "from_refs": Schema.leaf(),
            "class": Schema.leaf(),
            "_private": Schema.leaf(),
            "hyphen-name": Schema.leaf(),
        }
    )
    for path in ("declare", "from_refs", "class", "_private", "hyphen-name"):
        assert names.resolve(path).path == path

    with pytest.raises(TypeError, match="mapping or Schema"):
        names.declare(None)  # type: ignore[arg-type]


def test_schema_declare_is_reversible_independent_and_atomic() -> None:
    base = Schema(
        {
            "input": {
                "": Schema.container(),
                "a": Schema.leaf(int),
            },
            "keep": Schema.leaf(),
        }
    )
    extension = Schema(
        {
            "input": {"b": Schema.leaf()},
            "output": {"result": Schema.leaf()},
        }
    )

    remove_extension = base.declare(extension)
    assert base.resolve("input").path == "input"
    assert base.resolve("input.a").path == "input.a"
    assert base.resolve("input.b").path == "input.b"
    assert base.resolve("output.result").path == "output.result"
    assert extension.resolve("input.b").path == "input.b"
    remove_duplicate = base.declare(extension)

    remove_extension()
    assert base.resolve("input.b").path == "input.b"
    remove_duplicate()
    remove_duplicate()
    with pytest.raises(KeyError, match="input"):
        base.resolve("input.b")
    with pytest.raises(KeyError, match="output"):
        base.resolve("output.result")
    assert base.resolve("input.a").path == "input.a"

    with pytest.raises(ValueError, match="Ref configuration.*input.a"):
        base.declare(
            {
                "partial": Schema.leaf(),
                "input": {"a": Schema.leaf(str)},
            }
        )
    assert base.resolve("input.a").path == "input.a"
    with pytest.raises(KeyError, match="partial"):
        base.resolve("partial")

    leaf = Schema({"entry": Schema.leaf()})
    container = Schema({"entry": {"child": Schema.leaf()}})
    empty_container = Schema({"entry": {}})
    assert empty_container.resolve("entry").path == "entry"
    with pytest.raises(ValueError, match="leaf.*container"):
        leaf.declare(container)
    with pytest.raises(ValueError, match="leaf.*container"):
        leaf.declare(empty_container)
    with pytest.raises(ValueError, match="container.*leaf"):
        container.declare(leaf)
    remove_child = empty_container.declare(container)
    assert empty_container.resolve("entry.child").path == "entry.child"
    remove_child()
    assert empty_container.resolve("entry").path == "entry"
    with pytest.raises(KeyError, match="child"):
        empty_container.resolve("entry.child")

    implicit = Schema({"group": {"left": Schema.leaf()}})
    explicit = Schema(
        {
            "group": {
                "": Schema.container(),
                "right": Schema.leaf(),
            }
        }
    )
    remove_explicit = implicit.declare(explicit)
    assert implicit.resolve("group").path == "group"
    assert implicit.resolve("group.left").path == "group.left"
    assert implicit.resolve("group.right").path == "group.right"
    remove_explicit()
    with pytest.raises(KeyError, match="right"):
        implicit.resolve("group.right")

    with pytest.raises(ValueError, match="Ref configuration.*fixed"):
        Schema({"fixed": Schema.leaf(replaceable=False)}).declare(
            {"fixed": Schema.leaf()}
        )


def test_schema_declarations_keep_shared_ancestors_until_the_last_owner() -> None:
    schema = Schema()
    remove_left = schema.declare({"group": {"left": Schema.leaf()}})
    remove_right = schema.declare({"group": {"right": Schema.leaf()}})

    remove_left()
    with pytest.raises(KeyError, match="left"):
        schema.resolve("group.left")
    assert schema.resolve("group.right").path == "group.right"
    assert schema.resolve("group").path == "group"

    remove_right()
    with pytest.raises(KeyError, match="group"):
        schema.resolve("group")


def test_schema_path_can_be_redeclared_with_a_new_structure_after_disposal() -> None:
    schema = Schema()
    remove_leaf = schema.declare({"entry": Schema.leaf(int)})
    remove_leaf()

    remove_container = schema.declare({"entry": {"child": Schema.leaf(str)}})
    assert schema.resolve("entry.child").path == "entry.child"
    remove_container()
    with pytest.raises(KeyError, match="entry"):
        schema.resolve("entry")


def test_schema_disposal_prevents_old_context_values_from_reappearing() -> None:
    schema = Schema()
    remove_old = schema.declare({"plugin": {"value": Schema.leaf(replaceable=False)}})
    left = Context(schema=schema)
    right = Context(schema=schema)
    old_ref = schema.resolve("plugin.value")
    left.set(old_ref, "left-old")
    right.set(old_ref, "right-old")

    remove_old()
    with pytest.raises(ContextPathError, match="plugin.value"):
        left.get(old_ref)

    remove_new = schema.declare({"plugin": {"value": Schema.leaf()}})
    assert not left.exists(old_ref)
    assert not right.exists(old_ref)
    left.set(old_ref, "left-new")
    assert left.get(old_ref) == "left-new"
    assert not right.exists(old_ref)
    remove_new()


def test_context_view_tracks_the_live_schema_path() -> None:
    schema = Schema()
    remove_old = schema.declare({"plugin": {"value": Schema.leaf()}})
    ctx = Context(schema=schema)
    ctx.set("plugin.value", "old")
    view = ctx.get("plugin")

    remove_old()
    with pytest.raises(ContextPathError, match="plugin"):
        view.get("value")
    with pytest.raises(ContextPathError, match="plugin"):
        view.flatten()

    remove_new = schema.declare({"plugin": {"value": Schema.leaf()}})
    assert view.get("value", "missing") == "missing"
    ctx.set("plugin.value", "new")
    assert view.to_dict() == {"value": "new"}
    remove_new()


def test_schema_can_be_built_before_or_through_context() -> None:
    core = Schema({"plugin": {"base": Schema.leaf()}})
    extension = Schema({"plugin": {"extra": Schema.leaf()}})
    plugin_ref = extension.resolve("plugin.extra")
    root = Context(schema=core)
    child = root.fork()

    remove_core_extension = core.declare(extension)

    assert root.schema is core
    assert child.schema is root.schema
    child.set(plugin_ref, 1)
    assert child.get(root.schema.resolve("plugin.extra")) == 1

    remove_root_extension = root.declare(extension)
    remove_context_only = root.declare({"context_only": Schema.leaf()})
    root.set("context_only", 2)
    assert child.get("context_only") == 2

    remove_context_only()
    with pytest.raises(ContextPathError, match="context_only"):
        child.get("context_only")
    remove_root_extension()
    remove_core_extension()
    with pytest.raises(ContextPathError, match="plugin"):
        child.get("plugin.extra")

    context_first = Context()
    remove_late = context_first.declare({"late": {"value": Schema.leaf()}})
    context_first.set("late.value", 3)
    assert context_first.get("late.value") == 3
    remove_late()
    with pytest.raises(ContextPathError, match="late"):
        context_first.get("late.value")


def test_context_rejects_undeclared_keys_and_invalid_parent_arguments() -> None:
    root = Context(schema=R)

    for operation in (
        lambda: root.get("unknown.path", None),
        lambda: root.exists("unknown.path"),
        lambda: root.set("unknown.path", 1),
        lambda: root.delete("unknown.path"),
    ):
        with pytest.raises(ContextPathError, match="unknown"):
            operation()

    foreign = Schema({"value": Schema.leaf(str)}).resolve("value")
    root.set("value", 1)
    assert root.get(foreign) == 1
    assert root.flatten() == {root.schema.resolve("value"): 1}

    unrelated = Context(schema=R)
    assert unrelated.root is unrelated
    assert unrelated.schema is root.schema
    assert not unrelated.exists("value")
    with pytest.raises(TypeError, match="parent must be a Context"):
        Context(parent=object())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="inherits its schema"):
        Context(parent=root, schema=R)


def test_context_operations_respect_schema_leaf_and_container_roles() -> None:
    schema = Schema(
        {
            "leaf": Schema.leaf(),
            "container": {"child": Schema.leaf()},
            "custom_container": {
                "": Schema.container(),
                "child": Schema.leaf(),
            },
        }
    )
    ctx = Context(schema=schema)

    with pytest.raises(ContextPathError, match="container.*not a leaf"):
        ctx.set("container", 1)
    with pytest.raises(ContextPathError, match="container.*not a leaf"):
        ctx.add("container", 1)
    with pytest.raises(ContextPathError, match="custom_container.*not a leaf"):
        ctx.set("custom_container", 1)
    with pytest.raises(ContextPathError, match="leaf.*not a container"):
        tuple(ctx.keys("leaf"))
    with pytest.raises(ContextPathError, match="leaf.*not a container"):
        ctx.to_dict("leaf")

    with pytest.raises(ContextPathError, match="container.*not a leaf"):
        ctx.update({"leaf": 1, "container": 2})
    assert not ctx.exists("leaf")

    ctx.set("leaf", {"child": 1})
    ctx.set("container.child", 2)
    ctx.set("custom_container.child", 3)

    assert ctx.get("leaf") == {"child": 1}
    assert ctx.to_dict("container") == {"child": 2}
    assert ctx.to_dict("custom_container") == {"child": 3}


def test_context_crud_views_and_user_dict_leaves() -> None:
    settings = {"theme": "dark"}
    ctx = Context(
        {
            R.resolve("user.name"): "Ada",
            R.resolve("user.age"): 37,
            R.resolve("settings"): settings,
        },
        schema=R,
    )

    assert ctx.get(R.resolve("user.name")) == "Ada"
    assert ctx.get(R.resolve("missing"), "fallback") == "fallback"
    assert ctx.exists(R.resolve("user.age"))
    assert not ctx.exists(R.resolve("user.unknown"))
    assert set(ctx.keys()) == {"user", "settings"}
    assert set(ctx.keys(R.resolve("user"))) == {"name", "age"}
    assert ctx.to_dict() == {
        "user": {"name": "Ada", "age": 37},
        "settings": {"theme": "dark"},
    }

    user = ctx.get(R.resolve("user"))
    assert user.to_dict() == {"name": "Ada", "age": 37}
    assert user.get("name") == "Ada"
    assert user.exists("age")
    assert user.get(R.resolve("user.name")) == "Ada"
    with pytest.raises(ContextPathError, match="outside"):
        user.get(R.resolve("name"))
    assert set(user.keys()) == {"name", "age"}
    assert ctx.flatten() == {
        R.resolve("user.name"): "Ada",
        R.resolve("user.age"): 37,
        R.resolve("settings"): settings,
    }
    assert user.flatten() == {
        R.resolve("user.name"): "Ada",
        R.resolve("user.age"): 37,
    }

    ctx.delete(R.resolve("user.age"))
    assert not ctx.exists(R.resolve("user.age"))
    ctx.delete(R.resolve("user.name"))
    assert not ctx.exists(R.resolve("user"))
    ctx.drop([R.resolve("settings"), R.resolve("not_present")])
    assert ctx.to_dict() == {}


def test_context_constructor_requires_a_declared_path_mapping() -> None:
    with pytest.raises(TypeError, match="must be a mapping"):
        Context([])  # type: ignore[arg-type]
    with pytest.raises(ContextPathError, match="path"):
        Context({"path": 1})
    with pytest.raises(ContextPathError, match="value"):
        Context({R.resolve("value"): 1})


def test_context_constructor_accepts_data_and_a_keyword_only_parent() -> None:
    root = Context({R.resolve("a.b.c"): 1}, schema=R)
    child_scope = root.scope.fork()
    child = Context({R.resolve("c"): 3}, parent=root, scope=child_scope)

    assert child.parent is root
    assert child.scope is child_scope
    assert root.root is root
    assert child.root is root
    assert child.to_dict() == {"c": 3, "a": {"b": {"c": 1}}}
    assert child.to_dict(local=True) == {"c": 3}

    with pytest.raises(TypeError, match="positional"):
        Context(None, (root,))  # type: ignore[call-arg]


def test_context_path_errors_do_not_partially_mutate() -> None:
    ctx = Context(schema=R)
    ctx.set(R.resolve("blocked.child"), 1)

    with pytest.raises(ContextPathError, match="blocked.*not a leaf"):
        ctx.set(R.resolve("blocked"), 2)
    assert ctx.to_dict() == {"blocked": {"child": 1}}

    with pytest.raises(ContextPathError, match="parent.*not a leaf"):
        ctx.update({R.resolve("other"): 2, R.resolve("parent"): 1})
    assert ctx.to_dict() == {"blocked": {"child": 1}}

    ctx.set(R.resolve("short"), 3)
    with pytest.raises(ContextPathError, match="short.*not a container"):
        list(ctx.keys(R.resolve("short")))
    with pytest.raises(ContextPathError, match="short.*not a container"):
        ctx.to_dict(R.resolve("short"))
    with pytest.raises(ContextPathError):
        ctx.get(R.resolve("absent"))


def test_context_mutation_drop_then_update_semantics() -> None:
    ctx = Context(schema=R)
    ctx.update(
        {
            R.resolve("a.old"): 1,
            R.resolve("a.keep"): 2,
            R.resolve("other"): 3,
        }
    )

    ctx.mutate(updates={R.resolve("a.new"): 4}, drops=[R.resolve("a")])
    assert ctx.to_dict() == {"a": {"new": 4}, "other": 3}

    ctx.mutate(
        updates={R.resolve("other"): 5},
        drops=[R.resolve("a"), R.resolve("a.new")],
    )
    assert ctx.to_dict() == {"other": 5}

    before = ctx.to_dict()
    assert ctx.mutate() is None
    assert ctx.to_dict() == before


def test_context_deletion_does_not_change_schema_structure_roles() -> None:
    ctx = Context(schema=R)
    ctx.set(R.resolve("a.b.c"), 1)

    with pytest.raises(ContextPathError, match="a.*not a leaf"):
        ctx.set(R.resolve("a"), 2)
    assert ctx.to_dict() == {"a": {"b": {"c": 1}}}

    ctx.delete(R.resolve("a"))
    assert not ctx.exists(R.resolve("a"))
    with pytest.raises(ContextPathError, match="a.*not a leaf"):
        ctx.set(R.resolve("a"), 5)
    ctx.set(R.resolve("a.b.d"), 6)
    assert ctx.to_dict() == {"a": {"b": {"d": 6}}}

    ctx.set(R.resolve("settings"), {"theme": "dark"})
    ctx.delete(R.resolve("settings"))
    with pytest.raises(ContextPathError, match="settings.*not a container"):
        ctx.to_dict(R.resolve("settings"))


def test_context_mutation_validates_before_inplace_apply() -> None:
    ctx = Context(schema=R)
    ctx.update(
        {
            R.resolve("a.b.c"): 1,
            R.resolve("blocked.child"): 2,
            R.resolve("other"): 3,
        }
    )
    with pytest.raises(ContextPathError, match="blocked.*not a leaf"):
        ctx.update({R.resolve("a.new"): 4, R.resolve("blocked"): 5})
    assert ctx.to_dict() == {
        "a": {"b": {"c": 1}},
        "blocked": {"child": 2},
        "other": 3,
    }

    ctx.set(R.resolve("a.b.c"), 6)
    ctx.set(R.resolve("a.new"), 7)
    assert ctx.to_dict(R.resolve("a")) == {"b": {"c": 6}, "new": 7}


def test_update_tree_and_structured_extract() -> None:
    ctx = Context(schema=R)
    refs = {
        "position": (R.resolve("point.x"), R.resolve("point.y")),
        "label": R.resolve("point.label"),
    }
    values = {"position": (3, 4), "label": "p"}
    ctx.update_tree(refs, values)

    assert ctx.extract(refs) == values
    assert ctx.extract([R.resolve("point.x"), {"y": R.resolve("point.y")}]) == [
        3,
        {"y": 4},
    ]


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
    ctx.update({R.resolve("a.b.c"): 1, R.resolve("c"): 2})

    leaves, definition = CTX_EVAL_ENGINE.flatten(ctx)
    rebuilt = CTX_EVAL_ENGINE.unflatten(definition, leaves)
    assert rebuilt is ctx


def test_flatten_reconstructs_context_without_copying_leaf_values() -> None:
    shared_mapping = {"items": [1, 2]}
    shared_marker = object()
    ctx = Context(
        {
            R.resolve("branch.value"): shared_mapping,
            R.resolve("branch.marker"): shared_marker,
            R.resolve("other"): shared_mapping,
        },
        schema=R,
    )

    flattened = ctx.flatten()
    rebuilt = Context(flattened, schema=ctx.schema)

    assert flattened == {
        R.resolve("branch.value"): shared_mapping,
        R.resolve("branch.marker"): shared_marker,
        R.resolve("other"): shared_mapping,
    }
    assert rebuilt.get(R.resolve("branch.value")) is shared_mapping
    assert rebuilt.get(R.resolve("branch.marker")) is shared_marker
    assert rebuilt.get(R.resolve("other")) is shared_mapping

    rebuilt.set(R.resolve("branch.added"), "rebuilt-only")
    rebuilt.delete(R.resolve("branch.marker"))
    assert not ctx.exists(R.resolve("branch.added"))
    assert ctx.exists(R.resolve("branch.marker"))

    shared_mapping["items"].append(3)
    assert rebuilt.get(R.resolve("branch.value"))["items"] == [1, 2, 3]
    assert ctx.get(R.resolve("branch.value"))["items"] == [1, 2, 3]

    assert ctx.get(R.resolve("branch")).flatten() == {
        R.resolve("branch.value"): shared_mapping,
        R.resolve("branch.marker"): shared_marker,
    }


def test_to_dict_is_a_nested_projection_while_flatten_preserves_leaf_paths() -> None:
    leaf_schema = Schema({"settings": Schema.leaf()})
    tree_schema = Schema({"settings": {"theme": Schema.leaf()}})
    mapping_leaf = Context(
        {leaf_schema.resolve("settings"): {"theme": "dark"}},
        schema=leaf_schema,
    )
    structured = Context(
        {tree_schema.resolve("settings.theme"): "dark"},
        schema=tree_schema,
    )

    assert mapping_leaf.to_dict() == structured.to_dict()
    assert mapping_leaf.flatten() == {
        leaf_schema.resolve("settings"): {"theme": "dark"}
    }
    assert structured.flatten() == {tree_schema.resolve("settings.theme"): "dark"}


def test_context_fork_shares_its_scope_by_default() -> None:
    value = R.resolve("runtime.value")
    root = Context(schema=R)
    child = root.fork()
    sibling = root.fork()

    assert child.parent is root
    assert sibling.parent is root
    assert child.scope is root.scope
    assert sibling.scope is root.scope

    root.set(value, 1)
    assert child.get(value, local=True) == 1

    child.set(value, 2)
    assert root.get(value) == 2
    assert sibling.get(value) == 2
    sibling.delete(value)
    assert not root.exists(value)
    assert not child.exists(value)


def test_context_explicit_child_scope_has_live_inheritance_and_local_writes() -> None:
    value = R.resolve("runtime.value")
    root = Context(schema=R)
    child = root.fork(scope=root.scope.fork())
    sibling = root.fork(scope=root.scope.fork())

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


def test_context_lifecycle_parent_and_scope_visibility_are_orthogonal() -> None:
    value = R.resolve("value")
    root = Context(schema=R)
    left_scope = Scope(name="left")
    right_scope = Scope(name="right")
    left = root.fork(scope=left_scope)
    right = root.fork(scope=right_scope)

    left.set(value, "left")
    right.set(value, "right")
    assert left.get(value) == "left"
    assert right.get(value) == "right"

    combined_scope = Scope(name="combined", parents=(left_scope, right_scope))
    combined = right.fork(scope=combined_scope)
    assert combined.parent is right
    assert combined.get(value) == "left"


def test_independent_context_roots_do_not_share_data_through_a_scope() -> None:
    value = R.resolve("value")
    shared_scope = Scope(name="shared")
    left = Context(schema=R, scope=shared_scope)
    right = Context(schema=R, scope=shared_scope)

    left.set(value, 1)

    assert left.scope is right.scope
    assert right.get(value, "missing") == "missing"


def test_context_crud_uses_only_the_bound_scope() -> None:
    operations = (
        Context.extract,
        Context.get,
        Context.exists,
        Context.keys,
        Context.to_dict,
        Context.flatten,
        Context.mutate,
        Context.update,
        Context.drop,
        Context.set,
        Context.add,
        Context.update_tree,
        Context.delete,
    )

    for operation in operations:
        assert "scope" not in inspect.signature(operation).parameters
    assert "scope" in inspect.signature(Context.fork).parameters
    assert "scope" in inspect.signature(Context.contribute).parameters


def test_context_read_operations_can_select_local_or_effective_data() -> None:
    root = Context(schema=R)
    root.set(R.resolve("group.parent"), 1)
    child = root.fork(scope=root.scope.fork())
    child.set(R.resolve("group.child"), 2)

    assert tuple(child.keys(R.resolve("group"))) == ("child", "parent")
    assert tuple(child.keys(R.resolve("group"), local=True)) == ("child",)
    assert child.to_dict() == {"group": {"child": 2, "parent": 1}}
    assert child.to_dict(local=True) == {"group": {"child": 2}}
    assert child.flatten() == {
        R.resolve("group.child"): 2,
        R.resolve("group.parent"): 1,
    }
    assert child.flatten(local=True) == {R.resolve("group.child"): 2}
    assert child.extract([R.resolve("group.child")], local=True) == [2]


def test_context_views_follow_later_parent_and_child_changes() -> None:
    root = Context(schema=R)
    root.set(R.resolve("group.initial"), 1)
    child = root.fork(scope=root.scope.fork())
    view = child.get(R.resolve("group"))

    root.set(R.resolve("group.later"), 2)
    child.set(R.resolve("group.local"), 3)
    assert view.to_dict() == {"local": 3, "initial": 1, "later": 2}

    child.delete(R.resolve("group.local"))
    root.drop([R.resolve("group.initial"), R.resolve("group.later")])
    with pytest.raises(ContextPathError):
        view.to_dict()


def test_scope_uses_c3_for_multiple_parents() -> None:
    value = R.resolve("value")
    root_scope = Scope(name="root")
    left_scope = root_scope.fork(name="left")
    right_scope = root_scope.fork(name="right")
    child_scope = left_scope.fork(right_scope, name="child")
    root = Context(schema=R, scope=root_scope)
    left = root.fork(scope=left_scope)
    right = root.fork(scope=right_scope)
    child = left.fork(scope=child_scope)

    root.set(value, "root")
    right.set(value, "right")
    left.set(value, "left")

    assert child.scope.mro == (
        child_scope,
        left_scope,
        right_scope,
        root_scope,
    )
    assert child.get(value) == "left"

    x = root_scope.fork()
    y = root_scope.fork()
    xy = x.fork(y)
    yx = y.fork(x)
    with pytest.raises(TypeError, match="C3"):
        xy.fork(yx)
    with pytest.raises(TypeError, match="duplicate"):
        root_scope.fork(root_scope)


def test_context_structural_lookup_merges_declared_container_branches() -> None:
    root = Context(schema=R)
    root.set(R.resolve("a.b.c"), 1)

    child = root.fork(scope=root.scope.fork())
    child.set(R.resolve("a.b.d"), 2)
    assert child.to_dict(R.resolve("a.b")) == {"d": 2, "c": 1}

    root.set(R.resolve("a.from_oldest"), True)
    child.set(R.resolve("a.from_nearest"), True)
    assert child.to_dict(R.resolve("a")) == {
        "b": {"d": 2, "c": 1},
        "from_nearest": True,
        "from_oldest": True,
    }

    child.set(R.resolve("a.b.c"), 3)
    assert child.to_dict(R.resolve("a.b")) == {"d": 2, "c": 3}


def test_context_views_follow_schema_structure_and_declaration_order() -> None:
    schema = Schema(
        {
            "group": {
                "first": Schema.leaf(),
                "nested": {"value": Schema.leaf()},
                "empty": {"value": Schema.leaf()},
            }
        }
    )
    ctx = Context(schema=schema)
    ctx.set("group.nested.value", 2)
    ctx.set("group.first", 1)
    view = ctx.get("group")

    assert tuple(ctx.keys("group")) == ("first", "nested")
    assert tuple(view.keys()) == ("first", "nested")
    assert list(ctx.to_dict("group")) == ["first", "nested"]
    assert not ctx.exists("group.empty")

    schema.declare({"group": {"later": Schema.leaf()}})
    ctx.set("group.later", 3)
    assert tuple(view.keys()) == ("first", "nested", "later")


def test_scope_c3_branch_merge_uses_nearest_value_for_each_leaf() -> None:
    root_scope = Scope()
    root = Context(schema=R, scope=root_scope)
    root.set(R.resolve("a.root"), 1)
    left_scope = root_scope.fork()
    left = root.fork(scope=left_scope)
    left.set(R.resolve("a.left"), 2)
    right_scope = root_scope.fork()
    right = root.fork(scope=right_scope)
    right.set(R.resolve("a.root"), 3)

    child_scope = left_scope.fork(right_scope)
    child = left.fork(scope=child_scope)

    assert child.scope.mro == (
        child_scope,
        left_scope,
        right_scope,
        root_scope,
    )
    assert child.to_dict(R.resolve("a")) == {"left": 2, "root": 3}


def test_context_add_uses_schema_replaceability_and_is_exactly_reversible() -> None:
    value = R.resolve("service")
    root = Context(schema=R)
    remove_root = root.add(value, "root")

    with pytest.raises(ContextPathError, match="existing local"):
        root.add(value, "other")
    with pytest.raises(ContextPathError, match="non-replaceable"):
        root.set(value, "other")

    child = root.fork(scope=root.scope.fork())
    remove_child = child.add(value, "child")
    assert child.get(value) == "child"
    remove_child()
    remove_child()
    assert child.get(value) == "root"

    remove_root()
    assert not root.exists(value)


def test_context_add_does_not_define_runtime_replaceability() -> None:
    value = R.resolve("value")
    ctx = Context(schema=R)
    remove = ctx.add(value, 1)

    ctx.set(value, 2)
    remove()

    assert ctx.get(value) == 2


def test_context_isolate_blocks_inheritance_until_the_child_is_discarded() -> None:
    value = R.resolve("service")
    root = Context(schema=R)
    root.set(value, "root")

    isolated = root.isolate(value)
    assert isolated.get(value, "missing") == "missing"
    assert not isolated.exists(value)
    assert not isolated.exists(value, local=True)

    isolated.set(value, "local")
    assert isolated.get(value) == "local"
    with pytest.raises(ContextPathError, match="non-replaceable"):
        isolated.set(value, "other")
    isolated.delete(value)
    assert isolated.get(value, "missing") == "missing"
    assert root.get(value) == "root"


def test_context_isolation_stops_c3_lookup_before_later_parents() -> None:
    value = R.resolve("value")
    root = Context(schema=R)
    root.set(value, "root")
    left = root.isolate(value)
    right = root.fork(scope=root.scope.fork())
    right.set(value, "right")
    child_scope = left.scope.fork(right.scope)
    child = left.fork(scope=child_scope)

    assert child.scope.mro == (
        child_scope,
        left.scope,
        right.scope,
        root.scope,
    )
    assert child.get(value, "missing") == "missing"

    child.set(value, "child")
    assert child.get(value) == "child"
    child.delete(value)
    assert child.get(value, "missing") == "missing"


def test_context_isolate_requires_declared_leaves() -> None:
    ctx = Context(schema=R)

    with pytest.raises(ContextPathError, match="container.*not a leaf"):
        ctx.isolate(R.resolve("group"))


def test_context_add_disposer_prunes_a_temporary_container() -> None:
    ctx = Context(schema=R)

    remove = ctx.add(R.resolve("a.b.c"), "temporary")
    assert ctx.get(R.resolve("a.b.c")) == "temporary"
    remove()

    assert not ctx.exists(R.resolve("a"))
    assert not ctx.exists(R.resolve("a.b"), local=True)


def test_context_add_prunes_shared_temporary_branches_in_any_order() -> None:
    ctx = Context(schema=R)

    remove_first = ctx.add(R.resolve("a.first"), 1)
    remove_second = ctx.add(R.resolve("a.second"), 2)
    remove_first()
    assert ctx.to_dict(R.resolve("a")) == {"second": 2}
    remove_second()

    assert not ctx.exists(R.resolve("a"))


def test_deleting_an_added_leaf_prunes_its_temporary_branches() -> None:
    ctx = Context(schema=R)

    stale_disposer = ctx.add(R.resolve("a.old"), 1)
    ctx.delete(R.resolve("a.old"))
    assert not ctx.exists(R.resolve("a"))

    remove_new = ctx.add(R.resolve("a.new"), 2)
    remove_new()
    assert not ctx.exists(R.resolve("a"))
    stale_disposer()


def test_context_add_disposer_does_not_retain_removed_payload() -> None:
    class Payload:
        pass

    ctx = Context(schema=R)
    payload = Payload()
    payload_ref = weakref.ref(payload)
    dispose = ctx.add(R.resolve("payload"), payload)

    dispose()
    del payload
    gc.collect()

    assert payload_ref() is None
    dispose()


def test_context_parent_retains_children_until_explicit_disposal() -> None:
    class Payload:
        pass

    value = R.resolve("payload")
    root = Context(schema=R)
    child = root.fork(scope=root.scope.fork())
    payload = Payload()
    child_ref = weakref.ref(child)
    payload_ref = weakref.ref(payload)
    child.set(value, payload)

    del child
    del payload
    gc.collect()

    retained_child = child_ref()
    assert retained_child is not None
    assert payload_ref() is not None
    retained_child.dispose()
    del retained_child
    gc.collect()

    assert child_ref() is None
    assert payload_ref() is None
    assert not root.exists(value)


def test_context_removes_a_container_after_its_last_local_leaf() -> None:
    root = Context(schema=R)
    root.set(R.resolve("a.root"), "root")
    child = root.fork(scope=root.scope.fork())

    remove = child.add(R.resolve("a.temporary"), 1)
    child.set(R.resolve("a.regular"), 2)
    child.delete(R.resolve("a.regular"))
    remove()

    assert child.to_dict(R.resolve("a")) == {"root": "root"}
    assert not child.exists(R.resolve("a"), local=True)


def test_context_keeps_user_mappings_as_atomic_leaves() -> None:
    settings = {"theme": "dark"}
    ctx = Context({R.resolve("settings"): settings}, schema=R)

    assert ctx.get(R.resolve("settings")) is settings
    with pytest.raises(ContextPathError, match="theme"):
        ctx.exists("settings.theme")
    assert ctx.flatten() == {R.resolve("settings"): settings}


def test_flatten_can_materialize_an_effective_context() -> None:
    value = R.resolve("value")
    root = Context(schema=R)
    root.set(value, 1)
    child = root.fork(scope=root.scope.fork())

    snapshot = Context(child.flatten(), schema=child.schema)
    assert snapshot.parent is None
    assert snapshot.get(value) == 1

    root.set(value, 2)
    assert child.get(value) == 2
    assert snapshot.get(value) == 1


def test_flatten_does_not_copy_add_lifecycle_guards() -> None:
    value = R.resolve("value")
    ctx = Context(schema=R)
    dispose = ctx.add(value, 1)

    snapshot = Context(ctx.flatten(), schema=ctx.schema)
    snapshot.set(value, 2)

    assert snapshot.get(value) == 2
    assert ctx.get(value) == 1
    dispose()
