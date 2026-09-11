from __future__ import annotations

import asyncio
import gc
import weakref
from collections.abc import Callable

import pytest

from slyme.utils.retainer import Retainer


def test_registration_forwards_arguments_and_disposes_exact_entries_once() -> None:
    entries: dict[object, tuple[str, int, bool]] = {}
    events: list[str] = []

    def acquire(name: str, /, amount: int = 1, *, enabled: bool = True):
        token = object()
        entries[token] = (name, amount, enabled)

        def dispose():
            del entries[token]
            events.append(name)

        return dispose

    def should_cleanup():
        events.append("check")
        return not entries

    retainer = Retainer(
        acquire, should_cleanup=should_cleanup, cleanup=lambda: events.append("close")
    )
    assert not retainer.closed
    assert events == []
    first = retainer.acquire("same")
    second = retainer.acquire("same", 2, enabled=False)
    assert list(entries.values()) == [("same", 1, True), ("same", 2, False)]

    second()
    second()
    assert events == ["same", "check"]
    assert not retainer.closed
    first()
    first()
    assert not entries
    assert retainer.closed
    assert events == ["same", "check", "same", "check", "close"]
    with pytest.raises(RuntimeError, match="closed"):
        retainer.acquire("later")


@pytest.mark.parametrize("duplicate_first", [False, True])
def test_duplicate_policy_belongs_to_registration(duplicate_first: bool) -> None:
    entries: set[str] = set()
    events: list[str] = []

    def acquire(value: str):
        if value in entries:
            return lambda: events.append("noop")
        entries.add(value)
        return lambda: entries.remove(value)

    retainer = Retainer(
        acquire,
        should_cleanup=lambda: not entries,
        cleanup=lambda: events.append("close"),
    )
    original = retainer.acquire("value")
    duplicate = retainer.acquire("value")
    for release in (duplicate, original) if duplicate_first else (original, duplicate):
        release()
        release()
    assert events == (["noop", "close"] if duplicate_first else ["close", "noop"])
    assert not entries


def test_failed_acquisition_leaves_existing_disposals_available() -> None:
    entries: set[str] = set()

    def acquire(value: str):
        if value in entries:
            raise ValueError("duplicate")
        entries.add(value)
        return lambda: entries.remove(value)

    retainer = Retainer(
        acquire, should_cleanup=lambda: not entries, cleanup=lambda: None
    )
    release = retainer.acquire("value")
    with pytest.raises(ValueError, match="duplicate"):
        retainer.acquire("value")
    assert entries == {"value"}
    release()
    assert retainer.closed


def test_false_condition_keeps_retainer_open_without_a_reference_count() -> None:
    events: list[str] = []
    retainer = Retainer(
        lambda: lambda: events.append("dispose"),
        should_cleanup=lambda: False,
        cleanup=lambda: events.append("close"),
    )
    retainer.acquire()()
    retainer.acquire()()
    assert events == ["dispose", "dispose"]
    assert not retainer.closed


@pytest.mark.parametrize("stage", ["dispose", "condition", "cleanup"])
@pytest.mark.parametrize("error_type", [ValueError, asyncio.CancelledError])
def test_failure_is_replayed_without_retrying_callbacks(stage, error_type) -> None:
    events: list[str] = []
    error = error_type(stage)

    def run(current: str):
        events.append(current)
        if current == stage:
            raise error
        return True

    retainer = Retainer(
        lambda: lambda: run("dispose"),
        should_cleanup=lambda: run("condition"),
        cleanup=lambda: run("cleanup"),
    )
    release = retainer.acquire()
    for _ in range(2):
        with pytest.raises(error_type) as caught:
            release()
        assert caught.value is error
    expected = ["dispose", "condition", "cleanup"]
    assert events == expected[: expected.index(stage) + 1]
    assert retainer.closed is (stage == "cleanup")


@pytest.mark.parametrize("stage", ["acquire", "dispose", "condition", "cleanup"])
def test_reentrant_operations_are_rejected_without_consuming_another_handle(stage):
    entries: set[str] = set()
    active_stage: str | None = None

    def check_reentry(current: str):
        if active_stage != current:
            return
        with pytest.raises(RuntimeError, match="closed|re-enter"):
            retainer.acquire("nested")
        with pytest.raises(RuntimeError, match="re-enter"):
            other()

    def acquire(value: str):
        check_reentry("acquire")
        entries.add(value)

        def dispose():
            check_reentry("dispose")
            entries.remove(value)

        return dispose

    def condition():
        check_reentry("condition")
        return True

    def cleanup():
        assert retainer.closed
        check_reentry("cleanup")

    retainer = Retainer(acquire, should_cleanup=condition, cleanup=cleanup)
    other = retainer.acquire("other")
    active_stage = stage
    release = retainer.acquire("first")
    release()
    active_stage = None
    other()
    assert not entries


@pytest.mark.parametrize("stage", ["dispose", "condition", "cleanup"])
def test_repeating_the_executing_disposer_is_a_noop(stage: str) -> None:
    events: list[str] = []

    def run(current: str):
        events.append(current)
        if current == stage:
            release()
        return True

    retainer = Retainer(
        lambda: lambda: run("dispose"),
        should_cleanup=lambda: run("condition"),
        cleanup=lambda: run("cleanup"),
    )
    release = retainer.acquire()
    release()
    assert events == ["dispose", "condition", "cleanup"]
    assert retainer.closed


def test_cleanup_can_install_a_new_generation_in_an_external_index() -> None:
    retainers: dict[str, Retainer[[]]] = {}

    def create(cleanup: Callable[[], None]):
        return Retainer(
            lambda: lambda: None, should_cleanup=lambda: True, cleanup=cleanup
        )

    def cleanup():
        assert old.closed
        del retainers["key"]
        retainers["key"] = create(lambda: None)
        retainers["key"].acquire()()

    old = create(cleanup)
    retainers["key"] = old
    release = old.acquire()
    release()
    new = retainers["key"]
    release()
    assert new is not old
    assert retainers["key"] is new


def test_successful_disposal_drops_callbacks_and_storage_references() -> None:
    class Store:
        def acquire(self):
            return self.remove

        def remove(self):
            pass

        def empty(self):
            return True

        def cleanup(self):
            pass

    store = Store()
    store_ref = weakref.ref(store)
    retainer = Retainer(
        store.acquire, should_cleanup=store.empty, cleanup=store.cleanup
    )
    retainer_ref = weakref.ref(retainer)
    release = retainer.acquire()
    del store
    gc.collect()
    assert store_ref() is not None
    release()
    assert retainer.closed
    gc.collect()
    assert store_ref() is None
    del retainer
    gc.collect()
    assert retainer_ref() is None
    release()
