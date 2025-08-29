from collections.abc import (
    Mapping,
    MutableMapping,
    Sequence,
    MutableSequence,
)
from slyme.utils.typing import (
    TypeVar,
    Generic,
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
MappingData = Union[Mapping[_KT, _VT], Iterable[Tuple[_KT, _VT]], None]
SequenceData = Union[Iterable[_T], None]


class MappingProxy(Mapping[_KT, _VT], InitAdapterMixin, Generic[_KT, _VT]):
    """Readonly mapping proxy class.

    NOTE: ``_mapping_source`` should be set exactly once by the instance
    to avoid unexpected behavior.
    """

    __slots__ = ()
    _mapping_source: Attribute[Mapping[_KT, _VT], Mapping[_KT, _VT]]

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


class MutableMappingProxy(
    MutableMapping[_KT, _VT], InitAdapterMixin, Generic[_KT, _VT]
):
    """Mutable mapping proxy class.

    NOTE: ``_mutable_mapping_source`` should be set by the instance.
    """

    __slots__ = ()
    _mutable_mapping_source: Attribute[
        MutableMapping[_KT, _VT], MutableMapping[_KT, _VT]
    ]

    def __init__(
        self,
        /,
        mapping_data: MappingData[_KT, _VT] = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._mutable_mapping_source = {}
        if mapping_data is not None:
            self.update(mapping_data)

    def __getitem__(self, key: _KT, /) -> _VT:
        return self._mutable_mapping_source[key]

    def __setitem__(self, key: _KT, value: _VT, /) -> None:
        self._mutable_mapping_source[key] = value

    def __delitem__(self, key: _KT, /) -> None:
        del self._mutable_mapping_source[key]

    def __iter__(self, /) -> Iterator[_KT]:
        return iter(self._mutable_mapping_source)

    def __len__(self, /) -> int:
        return len(self._mutable_mapping_source)

    def __str__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._mutable_mapping_source}"
        )

    def __repr__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._mutable_mapping_source!r}"
        )


class SequenceProxy(Sequence[_T], InitAdapterMixin, Generic[_T]):
    """Readonly sequence proxy class.

    NOTE: ``_sequence_source`` should be set exactly once by the instance
    to avoid unexpected behavior.
    """

    __slots__ = ()
    _sequence_source: Attribute[Sequence[_T], Sequence[_T]]

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

    def _from_seq(self, seq: Sequence[_T]) -> Sequence[_T]:
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


class MutableSequenceProxy(MutableSequence[_T], InitAdapterMixin, Generic[_T]):
    """Mutable sequence proxy class.

    NOTE: ``_mutable_sequence_source`` should be set by the instance.
    """

    __slots__ = ()
    _mutable_sequence_source: Attribute[MutableSequence[_T], MutableSequence[_T]]

    def __init__(self, /, sequence_data: SequenceData[_T] = None, **kwargs):
        super().__init__(**kwargs)
        self._mutable_sequence_source = []
        if sequence_data is not None:
            self.extend(sequence_data)

    @overload
    def __getitem__(self, index: SupportsIndex) -> _T: ...
    @overload
    def __getitem__(self, index: slice) -> MutableSequence[_T]: ...
    def __getitem__(self, index):
        if isinstance(index, slice):
            return self._from_seq(self._mutable_sequence_source[index])
        return self._mutable_sequence_source[index]

    def _from_seq(self, seq: MutableSequence[_T]) -> MutableSequence[_T]:
        """Return a sequence instance with the input ``seq`` as the data source.

        NOTE: This method will directly return the ``seq`` itself by default, rather than a newly
        created user class instance, because it is rather complicated to create such an instance.
        Overriding the method in the subclass to change this behavior is allowed, which may however
        influence all the subclasses of the overriding subclass. You may also need to change the
        corresponding type annotations (e.g., ``__getitem__``) to obtain more precise type hints.
        """
        return seq

    @overload
    def __setitem__(self, index: SupportsIndex, value: _T) -> None: ...
    @overload
    def __setitem__(self, index: slice, value: Iterable[_T]) -> None: ...
    def __setitem__(self, index, value):
        self._mutable_sequence_source[index] = value

    @overload
    def __delitem__(self, index: SupportsIndex) -> None: ...
    @overload
    def __delitem__(self, index: slice) -> None: ...
    def __delitem__(self, index):
        del self._mutable_sequence_source[index]

    def insert(self, index: int, value: _T) -> None:
        self._mutable_sequence_source.insert(index, value)

    def __len__(self) -> int:
        return len(self._mutable_sequence_source)

    def __str__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._mutable_sequence_source}"
        )

    def __repr__(self) -> str:
        return (
            f"{resolve_instance_classname(self)}<{hex(id(self))}>"
            f"{self._mutable_sequence_source!r}"
        )
