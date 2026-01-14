from types import MappingProxyType
from collections import OrderedDict
from collections.abc import (
    Mapping,
    MutableMapping,
    Sequence,
    MutableSequence,
    Set,
    MutableSet,
    Collection,
    Iterator,
    Iterable,
)
from typing import (
    TypeVar,
    overload,
    Union,
)

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")
_T = TypeVar("_T")
_ST = TypeVar("_ST")
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


class MappingProxy(_MappingMixin[_KT, _VT], Mapping[_KT, _VT]):
    """Readonly mapping proxy class.

    NOTE: ``_mapping_source`` should be set exactly once by the instance
    to avoid unexpected behavior.
    """

    def __init__(
        self,
        /,
        mapping_data: MappingData[_KT, _VT] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        if mapping_data is None:
            self._mapping_source = MappingProxyType({})
        else:
            self._mapping_source = MappingProxyType(dict(mapping_data))


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


class _SequenceMixin(Sequence[_T]):
    """Sequence mixin methods."""

    _sequence_source: Sequence[_T]

    def _from_seq(self, seq: _ST, /) -> _ST:
        """Return a sequence instance with the input ``seq`` as the data source.

        NOTE: This method will directly return the ``seq`` itself by default, rather than a newly
        created user class instance, because it is rather complicated to create such an instance.
        Overriding the method in the subclass to change this behavior is allowed, which may however
        influence all the subclasses of the overriding subclass. You may also need to change the
        corresponding type annotations (e.g., ``__getitem__``) to obtain more precise type hints.
        """
        return seq

    def __len__(self, /) -> int:
        return len(self._sequence_source)

    def __repr__(self, /) -> str:
        return (
            f"{type(self).__name__}<{hex(id(self))}>"
            f"{self._sequence_source!r}"
        )


class SequenceProxy(_SequenceMixin[_T], Sequence[_T]):
    """Readonly sequence proxy class.

    NOTE: ``_sequence_source`` should be set exactly once by the instance
    to avoid unexpected behavior.
    """

    def __init__(self, /, sequence_data: SequenceData[_T] = None, **kwargs):
        super().__init__(**kwargs)
        if sequence_data is None:
            self._sequence_source = ()
        else:
            self._sequence_source = tuple(sequence_data)

    # NOTE: ``SupportsIndex`` type may pass type checker in future versions.
    @overload
    def __getitem__(self, index: int, /) -> _T: ...
    @overload
    def __getitem__(self, index: slice, /) -> Sequence[_T]: ...
    def __getitem__(self, index: Union[int, slice], /) -> Union[_T, Sequence[_T]]:
        if isinstance(index, slice):
            return self._from_seq(self._sequence_source[index])
        return self._sequence_source[index]


class MutableSequenceProxy(_SequenceMixin[_T], MutableSequence[_T]):
    """Mutable sequence proxy class.

    NOTE: ``_sequence_source`` should be set by the instance.
    """

    _sequence_source: MutableSequence[_T]

    def __init__(self, /, sequence_data: SequenceData[_T] = None, **kwargs):
        super().__init__(**kwargs)
        self._sequence_source = []
        if sequence_data is not None:
            self.extend(sequence_data)

    # NOTE: ``SupportsIndex`` type may pass type checker in future versions.
    @overload
    def __getitem__(self, index: int, /) -> _T: ...
    @overload
    def __getitem__(self, index: slice, /) -> MutableSequence[_T]: ...
    def __getitem__(
        self, index: Union[int, slice], /
    ) -> Union[_T, MutableSequence[_T]]:
        if isinstance(index, slice):
            return self._from_seq(self._sequence_source[index])
        return self._sequence_source[index]

    # NOTE: ``SupportsIndex`` type may pass type checker in future versions.
    @overload
    def __setitem__(self, index: int, value: _T, /) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[_T], /) -> None: ...
    def __setitem__(
        self, index: Union[int, slice], value: Union[_T, Iterable[_T]], /
    ) -> None:
        self._sequence_source[index] = value

    # NOTE: ``SupportsIndex`` type may pass type checker in future versions.
    @overload
    def __delitem__(self, index: int, /) -> None: ...
    @overload
    def __delitem__(self, index: slice, /) -> None: ...
    def __delitem__(self, index: Union[int, slice], /) -> None:
        del self._sequence_source[index]

    def insert(self, index: int, value: _T, /) -> None:
        self._sequence_source.insert(index, value)

    #
    # Other extended mixin methods.
    #
    def pop_all(self, /) -> MutableSequence[_T]:
        """Pop all the sequence elements."""
        # NOTE: Shallow copy the full sequence.
        seq = self[:]
        self.clear()
        return seq


class _SetMixin(Set[_T]):
    """Set mixin methods."""

    _set_source: Collection[_T]

    def __contains__(self, value: object, /) -> bool:
        return value in self._set_source

    def __iter__(self, /) -> Iterator[_T]:
        return iter(self._set_source)

    def __len__(self, /) -> int:
        return len(self._set_source)

    def __repr__(self, /) -> str:
        return (
            f"{type(self).__name__}<{hex(id(self))}>"
            f"{{{', '.join(map(repr, self._set_source))}}}"
        )


class OrderedSetProxy(_SetMixin[_T], MutableSet[_T]):
    """Mutable ordered set proxy class using OrderedDict."""

    _set_source: OrderedDict[_T, None]

    def __init__(self, /, set_data: SetData[_T] = None, **kwargs):
        super().__init__(**kwargs)
        self._set_source = OrderedDict()
        if set_data is not None:
            self.update(set_data)

    def add(self, value: _T, /) -> None:
        """Add an element to the set."""
        self._set_source[value] = None

    def discard(self, value: _T, /) -> None:
        """Remove an element from the set if it is a member."""
        self._set_source.pop(value, None)

    def update(self, *others: Iterable[_T]) -> None:
        """Update the set with the union of itself and others."""
        for other in others:
            for value in other:
                self.add(value)


# Public alias for user convenience
OrderedSet = OrderedSetProxy
