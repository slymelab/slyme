"""Application-defined storage and queries used by composition tests."""

from collections.abc import Iterable, Mapping
from typing import TypeVar

_T = TypeVar("_T")
_K = TypeVar("_K")
_V = TypeVar("_V")


class ValueLayer(dict[object, _T]):
    def register(self, token: object, /, value: _T) -> None:
        self[token] = value

    def delete(self, token: object, /) -> None:
        del self[token]


def collect_values(layers: Iterable[ValueLayer[_T]]) -> tuple[_T, ...]:
    return tuple(value for layer in layers for value in layer.values())


def first_value(layers: Iterable[ValueLayer[_T]]) -> _T:
    for layer in layers:
        for value in layer.values():
            return value
    raise LookupError("no value")


def merge_values(layers: Iterable[ValueLayer[Mapping[_K, _V]]]) -> dict[_K, _V]:
    result: dict[_K, _V] = {}
    for layer in layers:
        for mapping in layer.values():
            for key, value in mapping.items():
                result.setdefault(key, value)
    return result
