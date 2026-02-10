from collections.abc import (
    Mapping,
    MutableMapping,
    Iterator,
    Iterable,
)
from typing import (
    TypeVar,
    Union,
)

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")
_T = TypeVar("_T")
MappingData = Union[Mapping[_KT, _VT], Iterable[tuple[_KT, _VT]], None]
SequenceData = Union[Iterable[_T], None]
SetData = Union[Iterable[_T], None]


class _MappingMixin(Mapping[_KT, _VT]):
    """Mapping mixin methods."""

    _mapping_source: Mapping[_KT, _VT]

    def __getitem__(self, key: _KT, /) -> _VT:
        return self._mapping_source[key]

    def __iter__(self, /) -> Iterator[_KT]:
        return iter(self._mapping_source)

    def __len__(self, /) -> int:
        return len(self._mapping_source)

    def __repr__(self, /) -> str:
        return (
            f"{type(self).__name__}<{hex(id(self))}>"
            f"{self._mapping_source!r}"
        )


class MutableMappingProxy(_MappingMixin[_KT, _VT], MutableMapping[_KT, _VT]):
    """Mutable mapping proxy class.

    NOTE: ``_mapping_source`` should be set by the instance.
    """

    _mapping_source: MutableMapping[_KT, _VT]

    def __init__(
        self,
        /,
        mapping_data: MappingData[_KT, _VT] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._mapping_source = {}
        if mapping_data is not None:
            self.update(mapping_data)

    def __setitem__(self, key: _KT, value: _VT, /) -> None:
        self._mapping_source[key] = value

    def __delitem__(self, key: _KT, /) -> None:
        del self._mapping_source[key]
