from __future__ import annotations

import pytest

from slyme.context import Context, Metadata
from slyme.execution import await_result
from slyme.node import Node, node


def test_decorator_metadata_is_a_snapshot_and_does_not_reserve_a_business_parameter() -> (
    None
):
    marker = Metadata()
    supplied = {"description": marker}
    configured = node(metadata=supplied)

    @configured
    def function(*runtime, metadata):
        return metadata

    supplied.clear()
    first = function.partial(metadata="business")
    second = function.partial(metadata="another")
    assert first.metadata == second.metadata == {"description": marker}
    assert first.metadata["description"] is marker
    first = first.replace(metadata={"new": marker})
    assert first.metadata.get("new") is marker
    assert first.metadata == {"new": marker}
    assert second.metadata == {"description": marker}
    assert function.partial(metadata="later").metadata == {"description": marker}
    assert supplied == {}
    ctx = Context()
    runtime = (ctx,)
    assert first(*runtime) == "business"
    assert second(*runtime) == "another"
    ctx.dispose()


def test_creation_snapshots_metadata_without_merging_or_evaluating_it() -> None:

    class Marker(Metadata):
        def equals(self, other):
            pytest.fail("Metadata equality must not affect Node execution")

    marker = Marker()
    supplied = {"tag": marker}
    instance = node(lambda *args: 1, metadata=supplied)
    supplied.clear()
    assert instance.metadata["tag"] is marker
    assert instance.params == {}
    empty = node(lambda *args: 1)
    configured = empty.replace(metadata={"tag": marker})
    assert configured.metadata == {"tag": marker}
    assert empty.metadata == {}
    assert node(lambda *args: 1).metadata == {}
    ctx = Context()
    try:
        runtime = (ctx,)
        assert instance(*runtime) == 1
    finally:
        ctx.dispose()


def test_direct_constructor_accepts_metadata() -> None:
    marker = Metadata()
    supplied = {"tag": marker}
    value = Node(func=lambda *args: 1, params={}, metadata=supplied)
    sibling = Node(func=lambda *args: 1, params={}, metadata=supplied)
    assert value.metadata["tag"] is marker
    cleared = value.replace(metadata=None)
    assert cleared.metadata == {}
    assert value.metadata == {"tag": marker}
    assert supplied == sibling.metadata == {"tag": marker}


async def test_async_partial_metadata_is_separate_from_call_overrides() -> None:
    marker = Metadata()
    supplied = {"tag": marker}

    async def function(*runtime, metadata):
        return metadata

    base = node(function, metadata=supplied)
    supplied.clear()
    instance = base.partial(metadata="saved")
    assert base.func is function
    assert instance.metadata["tag"] is marker
    ctx = Context()
    try:
        runtime = (ctx,)
        assert await await_result(instance(*runtime, metadata="override")) == "override"
        assert await await_result(instance(*runtime)) == "saved"
        assert instance.metadata["tag"] is marker
    finally:
        await await_result(ctx.dispose())
