from __future__ import annotations

import pytest

from slyme.context import Context, Metadata
from slyme.execution import await_result
from slyme.node import Node, Wrapper, create_node, create_wrapper, node, wrapper


@pytest.mark.parametrize("decorate", [node, wrapper])
def test_decorator_metadata_is_a_snapshot_and_does_not_reserve_a_business_parameter(
    decorate,
) -> None:
    marker = Metadata()
    supplied = {"description": marker}
    configured = decorate(metadata=supplied)
    supplied.clear()

    @configured
    def function(*runtime, metadata):
        return metadata

    first = function(metadata="business")
    second = function(metadata="another")
    assert first.metadata == second.metadata == {"description": marker}
    assert first.metadata["description"] is marker
    first.metadata["new"] = marker
    del first.metadata["description"]
    assert first.metadata.get("new") is marker
    assert first.metadata == {"new": marker}
    assert second.metadata == {"description": marker}
    assert function(metadata="later").metadata == {"description": marker}
    assert supplied == {}
    ctx = Context()
    runtime = (
        (ctx,)
        if isinstance(first, Node)
        else (ctx, create_node(lambda ctx: None), lambda ctx: None)
    )
    assert first(*runtime) == "business"
    assert second(*runtime) == "another"
    ctx.dispose()


@pytest.mark.parametrize("create", [create_node, create_wrapper])
def test_creation_snapshots_metadata_without_merging_or_evaluating_it(create) -> None:
    class Marker(Metadata):
        def equals(self, other):
            pytest.fail("Metadata equality must not affect Node execution")

    marker = Marker()
    supplied = {"tag": marker}
    instance = create(lambda *args: 1, metadata=supplied)
    supplied.clear()
    assert instance.metadata["tag"] is marker
    assert instance.params == {}
    empty = create(lambda *args: 1)
    empty.metadata["tag"] = marker
    assert create(lambda *args: 1).metadata == {}
    ctx = Context()
    try:
        runtime = (
            (ctx,)
            if isinstance(instance, Node)
            else (ctx, create_node(lambda ctx: None), lambda ctx: None)
        )
        assert instance(*runtime) == 1
    finally:
        ctx.dispose()


@pytest.mark.parametrize("cls", [Node, Wrapper])
def test_direct_constructor_accepts_metadata(cls) -> None:
    marker = Metadata()
    supplied = {"tag": marker}
    value = cls(func=lambda *args: 1, params={}, metadata=supplied)
    sibling = cls(func=lambda *args: 1, params={}, metadata=supplied)
    assert value.metadata["tag"] is marker
    value.metadata.clear()
    assert supplied == sibling.metadata == {"tag": marker}


@pytest.mark.parametrize("decorate", [node, wrapper])
async def test_async_factory_metadata_is_separate_from_call_overrides(decorate) -> None:
    marker = Metadata()
    supplied = {"tag": marker}

    async def function(*runtime, metadata):
        return metadata

    factory = decorate(function, metadata=supplied)
    supplied.clear()
    instance = factory(metadata="saved")
    assert factory.__wrapped__ is function
    assert instance.metadata["tag"] is marker
    ctx = Context()
    try:
        runtime = (
            (ctx,)
            if isinstance(instance, Node)
            else (ctx, create_node(lambda ctx: None), lambda ctx: None)
        )
        assert await await_result(instance(*runtime, metadata="override")) == "override"
        assert await await_result(instance(*runtime)) == "saved"
        assert instance.metadata["tag"] is marker
    finally:
        await await_result(ctx.dispose())
