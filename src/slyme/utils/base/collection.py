#
# NOTE: ``BaseDict`` should be placed at the beginning of the file in order
# to avoid circular import error (caused by ``slyme.logging.logger``).
#
from slyme.utils.typing.native import (
    TypeVar,
)
from slyme.utils.typing.extension import (
    EmptyFlag,
)

_KT = TypeVar("_KT")
_VT = TypeVar("_VT")

#
# NOTE: Other modules should be placed bellow.
#

from slyme.utils.typing.extension import (
    SlymeConstant,
)

_T = TypeVar("_T")
_SlymeConstantT = TypeVar("_SlymeConstantT", bound=SlymeConstant)
#
# Base Dict
#


from slyme.utils.abc.base.collection import BaseDictABC, BaseListABC
from slyme.utils.base import BaseObjectInit
from slyme.utils.typing.extension import (
    MISSING,
    Missing,
    is_empty_flag,
    is_slyme_constant,
    resolve_instance_classname,
)
from slyme.utils.typing.native import (
    Generic,
    Iterable,
    Iterator,
    MutableMapping,
    MutableSequence,
    SupportsIndex,
    Tuple,
    cast,
    overload,
    Union,
)


class BaseDict(BaseDictABC[_KT, _VT], BaseObjectInit, Generic[_KT, _VT]):
    """
    A dict-like (mutable mapping) object that wraps a real Python ``dict`` (or ``MutableMapping``).
    Compared to directly inheriting from ``dict``, ``BaseDict`` implements ``set_dict__`` method,
    which can conveniently change the ``dict`` reference without using ``copy``.
    """

    def __init__(
        self,
        dict_like__: Union[
            MutableMapping[_KT, _VT], Iterable[Tuple[_KT, _VT]], EmptyFlag
        ] = MISSING,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.__dict: MutableMapping[_KT, _VT] = {}
        if is_empty_flag(dict_like__):
            dict_like__ = {}
        # Use ``self.update`` here to make the initialization process controllable. Otherwise, if
        # ``self.__dict = dict(__dict_like)`` is used here, the initialization process can't be
        # restricted by the user-defined operations.
        self.update(dict_like__)

    def smx_set_dict(self, __dict: MutableMapping[_KT, _VT]) -> None:
        self.__dict = __dict

    def smx_get_dict(self) -> MutableMapping[_KT, _VT]:
        return self.__dict

    @overload
    def __getitem__(self, __key: _KT) -> _VT:
        pass

    @overload
    def __setitem__(self, __key: _KT, __value: _VT) -> None:
        pass

    @overload
    def __delitem__(self, __key: _KT) -> None:
        pass

    @overload
    def __iter__(self) -> Iterator[_KT]:
        pass

    @overload
    def __len__(self) -> int:
        pass

    def __getitem__(self, __key):
        return self.__dict[__key]

    def __setitem__(self, __key, __value):
        self.__dict[__key] = __value

    def __delitem__(self, __key):
        del self.__dict[__key]

    def __iter__(self):
        return iter(self.__dict)

    def __len__(self):
        return len(self.__dict)

    def __str__(self) -> str:
        classname = resolve_instance_classname(self)
        _id = str(hex(id(self)))
        _dict = str(self.__dict)
        return f"{classname}<{_id}>({_dict})"


#
# Base List
#


class BaseList(BaseListABC[_T], BaseObjectInit, Generic[_T]):
    """
    A list-like (mutable sequence) object that wraps a real Python ``list`` (or ``MutableSequence``).
    Compared to directly inheriting from ``list``, ``BaseList`` implements ``set_list__`` method,
    which can conveniently change the ``list`` reference without using ``copy``.
    """

    def __init__(self, list_like__: Union[Iterable[_T], EmptyFlag] = MISSING, **kwargs):
        super().__init__(**kwargs)
        self.__list: MutableSequence[_T] = []
        if not is_empty_flag(list_like__):
            # Use ``self.extend`` here to make the initialization process controllable. Otherwise,
            # if ``self.__list = list(__list_like)`` is used here, the initialization process can't
            # be restricted by the user-defined operations.
            self.extend(list_like__)

    @classmethod
    def create__(
        cls,
        __list_like: Union[Iterable[_T], _SlymeConstantT, None] = None,
        *,
        return_constant: bool = True,
    ) -> Union["BaseList[_T]", _SlymeConstantT]:
        """
        TODO: Remove this method.
        Similar to ``BaseList.__init__``, but can return ``__list_like`` itself if it is a slyme
        constant and ``return_constant`` is ``True``.

        NOTE: The following two are equivalent:

        ```Python
        # The first.
        foo = BaseList.create__(bar, return_constant=True)
        # The second.
        foo = bar if is_slyme_constant(bar) else BaseList(bar)
        ```
        """
        if return_constant and is_slyme_constant(__list_like):
            return __list_like
        return cls(cast(Union[Iterable[_T], None], __list_like))

    def smx_set_list(self, __list: MutableSequence[_T]) -> None:
        self.__list = __list

    def smx_get_list(self) -> MutableSequence[_T]:
        return self.__list

    def smx_rindex(
        self, __value: _T, __start: int = 0, __stop: Union[int, Missing] = MISSING
    ) -> int:
        if __start < 0:
            __start = max(len(self) + __start, 0)

        if __stop is MISSING:
            __stop = len(self) - 1
        else:
            __stop = cast(int, __stop)
            if __stop < 0:
                __stop += len(self) - 1

        i = __stop
        while i >= __start:
            try:
                v = self[i]
            except IndexError:
                break
            if v is __value or v == __value:
                return i
            i -= 1
        raise ValueError

    @overload
    def __getitem__(self, __i: SupportsIndex) -> _T:
        pass

    @overload
    def __getitem__(self, __s: slice) -> MutableSequence[_T]:
        pass

    @overload
    def __setitem__(self, __key: SupportsIndex, __value: _T) -> None:
        pass

    @overload
    def __setitem__(self, __key: slice, __value: Iterable[_T]) -> None:
        pass

    @overload
    def __delitem__(self, __key: Union[SupportsIndex, slice]) -> None:
        pass

    @overload
    def insert(self, __index: SupportsIndex, __object: _T) -> None:
        pass

    def __getitem__(self, __key):
        return self.__list[__key]

    def __setitem__(self, __key, __value):
        self.__list[__key] = __value

    def __delitem__(self, __key):
        del self.__list[__key]

    def __len__(self) -> int:
        return len(self.__list)

    def insert(self, __index, __object):
        return self.__list.insert(__index, __object)

    def __str__(self) -> str:
        classname = resolve_instance_classname(self)
        _id = str(hex(id(self)))
        _list = str(self.__list)
        return f"{classname}<{_id}>({_list})"
