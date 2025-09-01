from collections.abc import (
    Mapping,
    MutableMapping,
    Sequence,
    MutableSequence,
)
from slyme.utils.typing import (
    TypeVar,
    Iterator,
    Iterable,
    SupportsIndex,
    overload,
    Union,
    Tuple,
)
from slyme.utils.inspect import resolve_instance_classname
from slyme.utils.descriptor.protocol import Attribute
from slyme.utils.mixin import InitAdapterMixin

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")
_T = TypeVar("_T")
_ST = TypeVar("_ST")
MappingData = Union[Mapping[_KT, _VT], Iterable[Tuple[_KT, _VT]], None]
SequenceData = Union[Iterable[_T], None]


class _MappingMixin(Mapping[_KT, _VT]):
    """Mapping mixin methods."""

    __slots__ = ()
    _mapping_source: Attribute[Mapping[_KT, _VT], Mapping[_KT, _VT]]

    def __getitem__(self, key: _KT, /) -> _VT:
        return self._mapping_source[key]

    def __iter__(self, /) -> Iterator[_KT]:
        return iter(self._mapping_source)

    def __len__(self, /) -> int:
        return len(self._mapping_source)

    def __str__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._mapping_source}"
        )

    def __repr__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._mapping_source!r}"
        )


class MappingProxy(_MappingMixin[_KT, _VT], Mapping[_KT, _VT], InitAdapterMixin):
    """Readonly mapping proxy class.

    NOTE: ``_mapping_source`` should be set exactly once by the instance
    to avoid unexpected behavior.
    """

    __slots__ = ()

    def __init__(
        self,
        /,
        mapping_data: MappingData[_KT, _VT] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if mapping_data is None:
            self._mapping_source = {}
        else:
            self._mapping_source = dict(mapping_data)


class MutableMappingProxy(_MappingMixin[_KT, _VT], MutableMapping[_KT, _VT], InitAdapterMixin):
    """Mutable mapping proxy class.

    NOTE: ``_mapping_source`` should be set by the instance.
    """

    __slots__ = ()
    _mapping_source: Attribute[
        MutableMapping[_KT, _VT], MutableMapping[_KT, _VT]
    ]

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


class _SequenceMixin(Sequence[_T]):
    """Sequence mixin methods."""

    __slots__ = ()
    _sequence_source: Attribute[Sequence[_T], Sequence[_T]]

    def _from_seq(self, seq: _ST) -> _ST:
        """Return a sequence instance with the input ``seq`` as the data source.

        NOTE: This method will directly return the ``seq`` itself by default, rather than a newly
        created user class instance, because it is rather complicated to create such an instance.
        Overriding the method in the subclass to change this behavior is allowed, which may however
        influence all the subclasses of the overriding subclass. You may also need to change the
        corresponding type annotations (e.g., ``__getitem__``) to obtain more precise type hints.
        """
        return seq

    def __len__(self) -> int:
        return len(self._sequence_source)

    def __str__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._sequence_source}"
        )

    def __repr__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._sequence_source!r}"
        )

    def rindex(
        self, value: _T, start: int = 0, stop: Union[int, None] = None, /
    ) -> int:
        """Reversed index."""
        length = len(self)
        if start < 0:
            start = max(length + start, 0)

        if stop is None:
            stop = length
        elif stop < 0:
            stop += length

        for i in range(stop - 1, start - 1, -1):
            try:
                v = self[i]
            except IndexError:
                break
            if v is value or v == value:
                return i
        raise ValueError("value not found")


class SequenceProxy(_SequenceMixin[_T], Sequence[_T], InitAdapterMixin):
    """Readonly sequence proxy class.

    NOTE: ``_sequence_source`` should be set exactly once by the instance
    to avoid unexpected behavior.
    """

    __slots__ = ()

    def __init__(self, /, sequence_data: SequenceData[_T] = None, **kwargs):
        super().__init__(**kwargs)
        if sequence_data is None:
            self._sequence_source = ()
        else:
            self._sequence_source = tuple(sequence_data)

    @overload
    def __getitem__(self, index: SupportsIndex) -> _T: ...
    @overload
    def __getitem__(self, index: slice) -> Sequence[_T]: ...
    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._from_seq(self._sequence_source[index])
        return self._sequence_source[index]


class MutableSequenceProxy(_SequenceMixin[_T], MutableSequence[_T], InitAdapterMixin):
    """Mutable sequence proxy class.

    NOTE: ``_sequence_source`` should be set by the instance.
    """

    __slots__ = ()
    _sequence_source: Attribute[MutableSequence[_T], MutableSequence[_T]]

    def __init__(self, /, sequence_data: SequenceData[_T] = None, **kwargs):
        super().__init__(**kwargs)
        self._sequence_source = []
        if sequence_data is not None:
            self.extend(sequence_data)

    @overload
    def __getitem__(self, index: SupportsIndex) -> _T: ...
    @overload
    def __getitem__(self, index: slice) -> MutableSequence[_T]: ...
    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._from_seq(self._sequence_source[index])
        return self._sequence_source[index]

    @overload
    def __setitem__(self, index: SupportsIndex, value: _T) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[_T]) -> None: ...
    def __setitem__(self, index, value):
        self._sequence_source[index] = value

    @overload
    def __delitem__(self, index: SupportsIndex) -> None: ...
    @overload
    def __delitem__(self, index: slice) -> None: ...
    def __delitem__(self, index):
        del self._sequence_source[index]

    def insert(self, index: int, value: _T) -> None:
        self._sequence_source.insert(index, value)

    #
    # Other extended mixin methods.
    #
    def pop_all(self) -> MutableSequence[_T]:
        """Pop all the sequence elements."""
        # NOTE: Shallow copy the full sequence.
        seq = self[:]
        self.clear()
        return seq
