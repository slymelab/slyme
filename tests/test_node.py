from __future__ import annotations

import asyncio
import inspect
from collections import UserDict
from collections.abc import Awaitable, Callable
from types import MappingProxyType
from typing import Any

import pytest

from slyme.builder import builder
from slyme.context import ARG, Arg, Context, Ref, Schema
from slyme.node import (
    UNDEFINED,
    AsyncNode,
    Auto,
    Node,
    RenderConfig,
    Wrapper,
    async_eval_tree,
    async_sequential,
    async_sequential_exec,
    eval_tree,
    node,
    sequential,
    sequential_exec,
    spec,
    wrapper,
)
from slyme.node.eval import (
    EvaluationPlan,
    EvaluatorDef,
    async_execute_eval_plan,
    contains_eval_type,
    execute_eval_plan,
    prepare_eval_plan,
)
from slyme.node.exception import (
    NodeExceptionRecord,
    NodeTerminate,
    WrapperExceptionRecord,
)
from slyme.node.signature import Spec
from slyme.node.tree import NODE_ENGINE

R = Schema(
    {
        "auto": {
            "async_temporary": ...,
            "dynamic": ...,
            "inherited": ...,
            "temporary": ...,
        },
        "async_value": ...,
        "input": {"base": ..., "label": ..., "value": ...},
        "names": {"outer": ...},
        "output": {"value": ...},
        "sync": ...,
        "value": ...,
    }
)


def test_signature_analysis_merges_specs_and_exposes_factory_signature() -> None:
    @node
    def configured(
        ctx: Context,
        /,
        *,
        required: int,
        defaulted: int = 2,
        produced: int = spec(default_factory=lambda: 3),
        dynamic: Auto[int] = spec(default=R.input.value),
    ) -> tuple[int, int, int, int]:
        return required, defaulted, produced, dynamic

    # Public signatures retain only keyword-only build-time parameters.
    assert list(inspect.signature(configured).parameters) == [
        "required",
        "defaulted",
        "produced",
        "dynamic",
    ]
    instance = configured(required=1)
    assert instance.defaulted == 2
    assert instance.produced == 3
    assert instance.dynamic == Ref("input.value")
    ctx = Context(schema=R)
    ctx.set(R.input.value, 4)
    assert instance(ctx) == (1, 2, 3, 4)


def test_schema_integrates_with_auto_and_builder() -> None:
    schema = Schema({"input": {"value": ...}})

    @node
    def increment(ctx: Context, /, *, value: Auto[int]) -> int:
        return value + 1

    ctx = Context(schema=R)
    ctx.set(schema.input.value, 4)
    assert increment(value=schema.input.value)(ctx) == 5

    @builder
    def misspelled() -> Node[int]:
        return increment(value=schema.input.vlaue)

    with pytest.raises(AttributeError, match="Did you mean 'value'"):
        misspelled()


def test_prebuilt_node_uses_schema_declared_after_runtime_fork() -> None:
    plugin_schema = Schema({"plugin": {"value": ...}})

    @node
    def read(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    graph = read(value=plugin_schema.plugin.value)
    root = Context()
    turn = root.fork()

    root.declare(plugin_schema)
    turn.set(plugin_schema.plugin.value, 4)

    assert graph(turn) == 4


def test_spec_validation_and_missing_parameters() -> None:
    with pytest.raises(ValueError, match="cannot be set at the same time"):
        Spec(default=1, default_factory=lambda: 2)
    with pytest.raises(ValueError, match="auto_eval"):
        Spec().should_eval(1)

    @node
    def required(ctx: Context, /, *, value: int) -> int:
        return value

    item = required()
    assert item.value is UNDEFINED
    with pytest.raises(NodeExceptionRecord) as exc_info:
        item(Context(schema=R))
    assert isinstance(exc_info.value.exception, ValueError)
    assert "Missing required parameter" in str(exc_info.value.exception)


def test_decorator_validation_and_factory_behavior() -> None:
    with pytest.raises(TypeError, match="exactly 1 runtime"):

        @node
        def no_context(*, value: int) -> int:
            return value

    with pytest.raises(TypeError, match="exactly 3 runtime"):

        @wrapper
        def bad_wrapper(ctx: Context, /) -> None:
            return None

    with pytest.raises(TypeError, match="reserved Node attributes"):

        @node
        def conflict(ctx: Context, /, *, run: int) -> int:
            return run

    with pytest.raises(ValueError, match="mode must be"):
        node(mode="invalid")(lambda ctx: None)  # type: ignore[call-overload]

    async def async_function(ctx: Context) -> None:
        return None

    with pytest.raises(TypeError, match="cannot decorate an async"):
        node(mode="sync")(async_function)

    @node
    async def detected(ctx: Context, /) -> int:
        return 1

    assert detected.mode == "async"
    assert detected.element_type is AsyncNode
    assert "NodeFactory[async]" in repr(detected)
    assert detected.func.__name__ == "detected"


def test_variadic_node_and_wrapper_parameters_are_rejected() -> None:
    with pytest.raises(TypeError, match=r"Variadic parameter '\*args'"):

        @node
        def variadic_node_args(*args: Any) -> None:
            return None

    with pytest.raises(TypeError, match=r"Variadic parameter '\*\*kwargs'"):

        @node
        def variadic_node_kwargs(**kwargs: Any) -> None:
            return None

    with pytest.raises(TypeError, match=r"Variadic parameter '\*args'"):

        @wrapper
        def variadic_wrapper_args(ctx: Context, wrapped: Node[Any], *args: Any) -> Any:
            return None

    with pytest.raises(TypeError, match=r"Variadic parameter '\*\*kwargs'"):

        @wrapper
        def variadic_wrapper_kwargs(
            ctx: Context,
            wrapped: Node[Any],
            /,
            **kwargs: Any,
        ) -> Any:
            return None


def test_node_attributes_are_declared_and_mutable() -> None:
    @node
    def identity(ctx: Context, /, *, value: int = 1) -> int:
        return value

    instance = identity()
    instance.value = 2
    assert instance(Context(schema=R)) == 2
    assert instance.specs["value"].default == 1
    assert instance.func is identity.func
    with pytest.raises(AttributeError, match="unknown attribute"):
        instance.other = 3  # type: ignore[attr-defined]
    with pytest.raises(AttributeError, match="Cannot delete"):
        del instance.value
    with pytest.raises(TypeError, match="unexpected keyword"):
        identity(other=3)  # type: ignore[call-arg]


def test_auto_evaluation_for_refs_nodes_and_nested_containers() -> None:
    calls: list[str] = []

    @node
    def child(ctx: Context, /, *, offset: Auto[int]) -> int:
        calls.append("child")
        return offset + 1

    @node
    def parent(
        ctx: Context,
        /,
        *,
        payload: Auto[dict[str, Any]],
    ) -> dict[str, Any]:
        return payload

    ctx = Context(schema=R)
    ctx.set(R.input.base, 4)
    graph = parent(
        payload={
            "raw": R.input.base,
            "computed": child(offset=R.input.base),
            "constant": [1, 2],
        }
    )
    assert contains_eval_type(graph.payload)
    assert graph(ctx) == {"raw": 4, "computed": 5, "constant": [1, 2]}
    assert calls == ["child"]
    assert not contains_eval_type({"plain": [1, 2]})
    assert eval_tree(ctx, [R.input.base, 9]) == [4, 9]


def test_node_call_uses_live_mutable_parameter_containers() -> None:
    @node
    def mutate_argument(
        ctx: Context,
        /,
        *,
        values: list[int],
        state: dict[str, int],
    ) -> tuple[tuple[int, ...], int]:
        values.append(4)
        state["calls"] += 1
        return tuple(values), state["calls"]

    instance = mutate_argument(values=[1, 2, 3], state={"calls": 0})
    values = instance.values
    state = instance.state

    assert instance(Context(schema=R)) == ((1, 2, 3, 4), 1)
    assert instance.values is values
    assert instance.state is state
    assert instance(Context(schema=R)) == ((1, 2, 3, 4, 4), 2)


async def test_async_node_and_wrapper_calls_use_live_parameters() -> None:
    @wrapper
    async def record(
        ctx: Context,
        wrapped: AsyncNode[Any],
        call_next: Callable[[Context], Awaitable[Any]],
        /,
        *,
        events: list[str],
    ) -> Any:
        events.append("wrapper")
        return await call_next(ctx)

    @node
    async def mutate(
        ctx: Context,
        /,
        *,
        values: list[int],
    ) -> tuple[int, ...]:
        values.append(3)
        return tuple(values)

    wrapper_instance = record(events=[])
    instance = mutate(values=[1, 2]).add_wrappers(wrapper_instance)

    assert await instance(Context(schema=R)) == (1, 2, 3)
    assert await instance(Context(schema=R)) == (1, 2, 3, 3)
    assert wrapper_instance.events == ["wrapper", "wrapper"]


def test_wrappers_execute_in_declared_order_and_evaluate_parameters() -> None:
    events: list[str] = []

    @wrapper
    def trace(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
        *,
        name: Auto[str],
    ) -> Any:
        events.append(f"before:{name}:{wrapped.func.__name__}")
        result = call_next(ctx)
        events.append(f"after:{name}")
        return result

    @node
    def work(ctx: Context, /, *, value: int) -> int:
        events.append("work")
        return value

    ctx = Context(schema=R)
    ctx.set(R.names.outer, "outer")
    result = work(value=7).add_wrappers(trace(name=R.names.outer), trace(name="inner"))(
        ctx
    )

    assert result == 7
    assert events == [
        "before:outer:work",
        "before:inner:work",
        "work",
        "after:inner",
        "after:outer",
    ]


def test_node_and_wrapper_exceptions_preserve_provenance() -> None:
    @node
    def fails(ctx: Context, /) -> None:
        raise RuntimeError("boom")

    instance = fails()
    with pytest.raises(NodeExceptionRecord) as exc_info:
        instance(Context(schema=R))
    assert exc_info.value.exception_node is instance
    assert isinstance(exc_info.value.exception, RuntimeError)
    assert "exception_node" in str(exc_info.value)
    assert exc_info.value.__cause__ is exc_info.value.exception

    @wrapper
    def broken_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        raise LookupError("wrapper boom")

    wrapped_instance = fails().add_wrappers(broken_wrapper())
    with pytest.raises(WrapperExceptionRecord) as wrapper_exc:
        wrapped_instance(Context(schema=R))
    assert wrapper_exc.value.wrapped_node is wrapped_instance
    assert isinstance(wrapper_exc.value.exception_node, Wrapper)
    assert isinstance(wrapper_exc.value.exception, LookupError)
    assert "exception_wrapper" in str(wrapper_exc.value)


def test_node_terminate_sets_source_once() -> None:
    @node
    def stop(ctx: Context, /) -> None:
        raise NodeTerminate("done")

    instance = stop()
    with pytest.raises(NodeTerminate) as exc_info:
        instance(Context(schema=R))
    assert exc_info.value.source_node is instance
    assert exc_info.value.msg == "done"
    exc_info.value.msg = "changed"
    assert "changed" in str(exc_info.value)


async def test_async_node_wrapper_and_mixed_evaluation() -> None:
    events: list[str] = []

    @node
    def sync_child(ctx: Context, /, *, value: Auto[int]) -> int:
        events.append("sync")
        return value + 1

    @node
    async def async_child(ctx: Context, /, *, value: Auto[int]) -> int:
        await asyncio.sleep(0)
        events.append("async")
        return value + 2

    @wrapper
    async def async_trace(
        ctx: Context,
        wrapped: AsyncNode[Any],
        call_next: Callable[[Context], Awaitable[Any]],
        /,
        *,
        label: Auto[str],
    ) -> Any:
        events.append(f"before:{label}")
        value = await call_next(ctx)
        events.append("after")
        return value

    @node
    async def parent(
        ctx: Context,
        /,
        *,
        values: Auto[list[int]],
    ) -> int:
        return sum(values)

    ctx = Context(schema=R)
    ctx.update({R.input.value: 3, R.input.label: "trace"})
    graph = parent(
        values=[
            sync_child(value=R.input.value),
            async_child(value=R.input.value),
        ]
    ).add_wrappers(async_trace(label=R.input.label))

    assert await graph(ctx) == 9
    assert events[0] == "before:trace"
    assert set(events[1:3]) == {"sync", "async"}
    assert events[-1] == "after"
    assert await async_eval_tree(ctx, {"x": R.input.value}) == {"x": 3}


async def test_sync_evaluation_rejects_async_node() -> None:
    @node
    async def async_child(ctx: Context, /) -> int:
        return 1

    child = async_child()
    with pytest.raises(RuntimeError, match="Cannot evaluate AsyncNode"):
        eval_tree(Context(schema=R), child)


def test_evaluator_result_count_is_validated() -> None:
    @node
    def identity(ctx: Context, /) -> None:
        return None

    base = prepare_eval_plan(identity())
    evaluator = EvaluatorDef(
        sync_func=lambda _ctx, _values: [],
        async_func=lambda _ctx, _values: _empty_async(),
    )
    bad_plan = EvaluationPlan(
        tree_def=base.tree_def,
        batches=((evaluator, (0,), (identity(),)),),
        pass_through=(),
        num_leaves=1,
    )
    with pytest.raises(ValueError, match="expected 1"):
        execute_eval_plan(Context(schema=R), bad_plan)


async def _empty_async() -> list[Any]:
    return []


async def test_async_evaluator_result_count_is_validated() -> None:
    @node
    async def identity(ctx: Context, /) -> None:
        return None

    base = prepare_eval_plan(identity())
    evaluator = EvaluatorDef(
        sync_func=lambda _ctx, _values: [],
        async_func=lambda _ctx, _values: _empty_async(),
    )
    bad_plan = EvaluationPlan(
        tree_def=base.tree_def,
        batches=((evaluator, (0,), (identity(),)),),
        pass_through=(),
        num_leaves=1,
    )
    with pytest.raises(ValueError, match="expected 1"):
        await async_execute_eval_plan(Context(schema=R), bad_plan)


def test_auto_nodes_receive_isolated_child_contexts() -> None:
    inherited = Ref("auto.inherited")
    temporary = Ref("auto.temporary")
    seen: list[Context] = []

    @node
    def child(ctx: Context, /) -> int:
        assert ctx.get(inherited) == 4
        ctx.add(temporary, len(seen))
        seen.append(ctx)
        return len(seen)

    @node
    def parent(
        ctx: Context,
        /,
        *,
        left: Auto[int],
        right: Auto[int],
    ) -> tuple[int, int]:
        return left, right

    ctx = Context(schema=R)
    ctx.set(inherited, 4)
    shared_child = child()
    assert parent(left=shared_child, right=shared_child)(ctx) == (1, 2)
    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert seen[0].parents == (ctx,)
    assert seen[1].parents == (ctx,)
    assert not ctx.exists(temporary)


def test_auto_realizes_each_original_leaf_only_once() -> None:
    dynamic = Ref("auto.dynamic")
    calls = 0

    @node
    def child(ctx: Context, /) -> int:
        nonlocal calls
        calls += 1
        return calls

    @node
    def parent(ctx: Context, /, *, value: Auto[Any]) -> Any:
        return value

    produced_node = child()
    produced_mapping = {"child": produced_node}
    ctx = Context(schema=R)

    ctx.set(dynamic, produced_node)
    assert parent(value=dynamic)(ctx) is produced_node

    ctx.set(dynamic, produced_mapping)
    assert parent(value=dynamic)(ctx) is produced_mapping
    assert calls == 0


async def test_async_auto_nodes_receive_isolated_child_contexts() -> None:
    temporary = Ref("auto.async_temporary")
    started = 0
    both_started = asyncio.Event()
    seen: list[Context] = []

    @node
    async def child(ctx: Context, /) -> int:
        nonlocal started
        ctx.add(temporary, started)
        seen.append(ctx)
        started += 1
        if started == 2:
            both_started.set()
        await both_started.wait()
        return started

    @node
    async def parent(ctx: Context, /, *, values: Auto[list[int]]) -> int:
        return sum(values)

    ctx = Context(schema=R)
    result = await asyncio.wait_for(
        parent(values=[child(), child()])(ctx),
        timeout=5,
    )
    assert result == 4
    assert len(seen) == 2
    assert seen[0] is not seen[1]
    assert seen[0].parents == (ctx,)
    assert seen[1].parents == (ctx,)
    assert not ctx.exists(temporary)


def test_sequential_nodes_share_context() -> None:
    @node
    def increment(ctx: Context, /, *, source: Ref[int], target: Ref[int]) -> None:
        ctx.set(target, ctx.get(source, 0) + 1)

    first = increment(source=R.value, target=R.value)
    second = increment(source=R.value, target=R.value)
    ctx = Context(schema=R)
    sequential_exec(ctx, [first, second])
    assert ctx.get(R.value) == 2

    sequential(nodes=[first, second])(ctx)
    assert ctx.get(R.value) == 4


def test_sync_sequential_rejects_non_sync_execution_entries() -> None:
    @node
    async def async_child(ctx: Context, /) -> None:
        return None

    with pytest.raises(TypeError, match="only accepts synchronous Nodes"):
        sequential_exec(Context(schema=R), [async_child()])  # type: ignore[list-item]
    with pytest.raises(TypeError, match="only accepts synchronous Nodes"):
        sequential_exec(Context(schema=R), [object()])  # type: ignore[list-item]


async def test_async_sequential_accepts_sync_and_async_nodes() -> None:
    @node
    def sync_step(ctx: Context, /) -> None:
        ctx.set(R.sync, True)

    @node
    async def async_step(ctx: Context, /) -> None:
        ctx.set(R.async_value, True)

    ctx = Context(schema=R)
    nodes = [sync_step(), async_step()]
    await async_sequential_exec(ctx, nodes)
    assert ctx.get(R.sync) and ctx.get(R.async_value)
    await async_sequential(nodes=nodes)(ctx)

    with pytest.raises(TypeError, match="only accepts Nodes and AsyncNodes"):
        await async_sequential_exec(ctx, [object()])  # type: ignore[list-item]


def test_builder_constructs_nodes_and_reports_missing_returns() -> None:
    @node
    def leaf(ctx: Context, /, *, value: int = 1) -> int:
        return value

    @builder
    def build() -> Node[int]:
        return leaf()

    assert isinstance(build(), Node)
    assert build.__name__ == "build"

    @node
    async def async_leaf(ctx: Context, /) -> int:
        return 1

    @builder
    def build_async() -> AsyncNode[int]:
        return async_leaf()

    assert isinstance(build_async(), AsyncNode)

    @builder()
    def called_builder() -> Node[int]:
        return leaf(value=2)

    assert called_builder()(Context(schema=R)) == 2

    @builder
    def missing() -> Any:
        return None

    with pytest.raises(ValueError, match="returned None"):
        missing()

    @builder
    def invalid() -> Any:
        return "not a node"

    with pytest.raises(TypeError, match="expected a Node or AsyncNode"):
        invalid()


def test_node_elements_can_nest_freely_but_wrapper_slots_match_mode() -> None:
    @wrapper
    def sync_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
        *,
        dependency: Any = None,
    ) -> Any:
        return call_next(ctx)

    @wrapper
    async def async_wrapper(
        ctx: Context,
        wrapped: AsyncNode[Any],
        call_next: Callable[[Context], Awaitable[Any]],
        /,
        *,
        dependency: Any = None,
    ) -> Any:
        return await call_next(ctx)

    @node
    def sync_node(ctx: Context, /, *, dependency: Any = None) -> None:
        return None

    @node
    async def async_node(ctx: Context, /, *, dependency: Any = None) -> None:
        return None

    nested_wrapper = sync_wrapper(dependency=sync_node())
    nested_node = sync_node(dependency=nested_wrapper)
    mixed = sync_node(dependency=[nested_node, async_node()])
    assert isinstance(mixed.dependency[0].dependency, Wrapper)
    assert isinstance(nested_wrapper.dependency, Node)

    with pytest.raises(TypeError, match="synchronous Wrappers"):
        sync_node().add_wrappers(async_wrapper())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AsyncWrappers"):
        async_node().add_wrappers(sync_wrapper())  # type: ignore[arg-type]

    invalid_sync = sync_node()
    invalid_sync.wrappers.append(async_wrapper())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="synchronous Wrappers"):
        invalid_sync(Context(schema=R))


async def test_async_wrapper_slot_is_revalidated_at_execution() -> None:
    @wrapper
    def sync_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        return call_next(ctx)

    @node
    async def async_node(ctx: Context, /) -> None:
        return None

    invalid_async = async_node()
    invalid_async.wrappers.append(sync_wrapper())  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="AsyncWrappers"):
        await invalid_async(Context(schema=R))


def test_node_tree_round_trip_and_render_configuration() -> None:
    @wrapper
    def trace(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        return call_next(ctx)

    @node
    def child(ctx: Context, /, *, value: int = 1) -> int:
        return value

    @node
    def parent(ctx: Context, /, *, nested: Auto[int]) -> int:
        return nested

    graph = parent(nested=child()).add_wrappers(trace())
    leaves, definition = NODE_ENGINE.flatten(graph)
    rebuilt = NODE_ENGINE.unflatten(definition, leaves)
    assert isinstance(rebuilt, Node)
    assert rebuilt(Context(schema=R)) == 1

    rendered = repr(graph)
    assert "parent<Node>" in rendered
    assert "@wrappers" in rendered
    assert "(nodes)" in rendered
    assert "? .nested" in rendered
    assert "1" in rendered
    assert "#refs" in repr(parent(nested=R.input.value))
    assert RenderConfig.visible_categories is None

    try:
        RenderConfig.visible_categories = ("nodes",)
        filtered = repr(graph)
        assert "(nodes)" in filtered
        assert "@wrappers" not in filtered
        assert "(values)" not in filtered
    finally:
        RenderConfig.visible_categories = None


def test_render_distinguishes_executable_edges_from_stored_node_values() -> None:
    @wrapper
    def trace(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
    ) -> Any:
        return call_next(ctx)

    @node
    def child(ctx: Context, /) -> int:
        return 1

    @node
    def container(ctx: Context, /, *, stored: Any = None) -> Any:
        return stored

    @wrapper
    def configured_wrapper(
        ctx: Context,
        wrapped: Node[Any],
        call_next: Callable[[Context], Any],
        /,
        *,
        wrappers: list[int],
    ) -> Any:
        return call_next(ctx)

    stored_wrapper = trace()
    stored_node = child()
    rendered = repr(container(stored=[stored_wrapper, stored_node]))

    assert "@wrappers" not in rendered
    assert "(nodes)" not in rendered
    assert "(values)" in rendered
    assert "(nodes)" in repr(container(stored=stored_node))
    assert "@wrappers" not in repr(container(stored=stored_wrapper))
    assert ".wrappers" not in repr(child())
    assert "(nodes)" in repr(sequential(nodes=[stored_node]))

    user_mapping = UserDict({"secret": 42})
    assert "{'secret': 42}" in repr(container(stored=user_mapping))

    proxy = MappingProxyType({"secret": 42})
    assert repr(container(stored=proxy)).count("secret") == 1

    configured = repr(configured_wrapper(wrappers=[]))
    assert ".wrappers" in configured
    assert "@wrappers" not in configured


def test_node_run_boundary_defaults_outputs_and_context() -> None:
    schema = Schema(
        {
            "input": {
                "value": Ref(
                    metadata={
                        ARG: Arg(
                            type=int,
                            default_factory=lambda: 5,
                            required=True,
                        )
                    }
                )
            },
            "output": {"value": ...},
        }
    )

    @node
    def application(
        ctx: Context,
        /,
        *,
        value: Auto[int],
        output: Ref[int],
    ) -> None:
        ctx.set(output, value * 2)

    graph = application(value=schema.input.value, output=schema.output.value)
    output, ctx = graph.run(outputs=schema.output.value, return_context=True)
    assert output == 10
    assert ctx.get(schema.input.value) == 5
    assert ctx.schema.input.value().path == "input.value"
    assert ctx.schema.output.value().path == "output.value"
    assert graph.run(inputs={schema.input.value: 4}, outputs=schema.output.value) == 8
    assert isinstance(graph.run(), Context)
    with pytest.raises(KeyError, match="not declared"):
        graph.run(Context(schema=Schema({"unrelated": ...})))
    with pytest.raises(TypeError, match="context must be"):
        graph.run({})  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cli_args requires"):
        graph.run(cli_args=[])


async def test_async_node_run_boundary() -> None:
    @node
    async def application(ctx: Context, /, *, output: Ref[int]) -> None:
        ctx.set(output, 8)

    assert await application(output=R.output).run(outputs=R.output) == 8
