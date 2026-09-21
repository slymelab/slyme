"""Compose preserves each layer's registration signature and query result types."""

from collections.abc import Callable, Iterable
from typing import Literal

from tests.compose_helpers import ValueLayer, collect_values
from typing_extensions import assert_type

from slyme.context import Compose, Scope


def total(layers: Iterable[ValueLayer[int]]) -> int:
    return sum(value for layer in layers for value in layer.values())


def check_types(scope: Scope) -> None:
    collected: Compose[ValueLayer[str], tuple[str, ...]] = Compose(
        factory=ValueLayer[str], query=collect_values
    )
    assert_type(collected.resolve(scope), tuple[str, ...])
    assert_type(collected.layers(scope), Iterable[ValueLayer[str]])
    assert_type(collected.layers(), Iterable[ValueLayer[str]])
    assert_type(collected.register(scope, "value"), Callable[[], None])
    assert_type(collected.resolve(scope, lambda layers: len(tuple(layers))), int)
    collected.register(scope, 1)  # type: ignore[arg-type]

    custom = Compose(
        factory=ValueLayer[int],
        query=total,
    )
    assert_type(custom.resolve(scope), int)
    assert_type(custom.resolve(scope, lambda layers: [*layers]), list[ValueLayer[int]])

    raw: Compose[ValueLayer[int], None] = Compose(factory=ValueLayer[int])
    assert_type(raw.resolve(scope, total), int)


class NamedLayer:
    def register(
        self,
        token: object,
        /,
        name: str,
        value: int,
        *,
        position: Literal["prepend", "append"] = "append",
    ) -> None:
        pass

    def delete(self, token: object, /) -> None:
        pass


def check_signature(scope: Scope) -> None:
    names = Compose(factory=NamedLayer, query=lambda layers: len(tuple(layers)))
    assert_type(names, Compose[NamedLayer, int])
    assert_type(names.register(scope, "x", 1, position="prepend"), Callable[[], None])
    names.register(scope, name="x", value=1)
    names.register(scope, "x", "wrong")  # type: ignore[arg-type]
    names.register(scope, "x")  # type: ignore[call-arg]
    names.register(scope, "x", 1, position="other")  # type: ignore[arg-type]
    names.register(scope, "x", 1, unknown=True)  # type: ignore[call-arg]
