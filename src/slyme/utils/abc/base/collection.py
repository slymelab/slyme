from abc import ABC, abstractmethod
from slyme.utils.typing.native import (
    TypeVar,
    Union,
    Generic,
    MutableMapping,
    MutableSequence,
)
from slyme.utils.typing.extension import MISSING, Missing
from slyme.utils.decorator import not_implemented

_T = TypeVar("_T")
_KT = TypeVar("_KT")
_VT = TypeVar("_VT")

#
# BaseDict ABC
#


class BaseDictABC(MutableMapping[_KT, _VT], ABC, Generic[_KT, _VT]):
    """
    ABC of ``BaseDict``.
    """

    @abstractmethod
    def smx_set_dict(self, __dict: MutableMapping[_KT, _VT]) -> None:
        """
        Change the dict reference.
        """
        pass

    @abstractmethod
    def smx_get_dict(self) -> MutableMapping[_KT, _VT]:
        """
        Get the dict reference.
        """
        pass


#
# BaseList ABC
#


class BaseListABC(MutableSequence[_T], ABC, Generic[_T]):
    """
    ABC of ``BaseList``.
    """

    @abstractmethod
    def smx_set_list(self, __list: MutableSequence[_T]) -> None:
        """
        Change the list reference.
        """
        pass

    @abstractmethod
    def smx_get_list(self) -> MutableSequence[_T]:
        """
        Get the list reference.
        """
        pass

    @not_implemented
    def smx_rindex(
        self, __value: _T, __start: int = 0, __stop: Union[int, Missing] = MISSING
    ) -> int:
        """
        Reversed index method. Return the last occurrence of ``__value``. Raise
        ``ValueError`` if ``__value`` is not present.

        NOTE: This method is optionally implemented.
        """
        pass
