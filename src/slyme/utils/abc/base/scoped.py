"""
ABCs for scoped lifecycle management.
"""

from abc import ABC, abstractmethod
from slyme.utils.typing.native import (
    Generic,
    TypeVar,
    Any,
    Generator,
    Union,
    Iterable,
    ContextManager,
    Tuple,
    Mapping,
    Callable,
    FrozenSet,
)
from slyme.utils.typing.extension import EmptyFlag, Missing, MISSING, Stop
from slyme.utils.abc.base.attr import AttrOpStateABC
from slyme.utils.abc.descriptor import DescriptorProtocol
from .collection import BaseListABC

_EnterT_co = TypeVar("_EnterT_co", covariant=True)
_ScopableT = TypeVar("_ScopableT")
_ScopedManagerT = TypeVar("_ScopedManagerT")
_ScopedSetattrGuardT = TypeVar("_ScopedSetattrGuardT")
_ScopedGetattrGuardT = TypeVar("_ScopedGetattrGuardT")
_ScopedDelattrGuardT = TypeVar("_ScopedDelattrGuardT")


class ScopedContextStackABC(ABC, Generic[_ScopableT, _EnterT_co]):

    # TODO: maybe call_scope
    @abstractmethod
    def call_scoped_context_stack_yield(
        self, scoped: _ScopableT
    ) -> Generator[_EnterT_co, Any, Any]:
        yield


#
# Scoped ABC.
#


class ScopedManagerABC(
    ScopedContextStackABC[_ScopableT, _EnterT_co], ABC, Generic[_ScopableT, _EnterT_co]
):
    """
    ABC of ``ScopedManager``.
    """

    @abstractmethod
    def scoped_manager_yield(
        self, scoped: _ScopableT
    ) -> Generator[_EnterT_co, Any, Any]:
        """
        Generator function used to create a context manager.
        """
        yield


class ScopedManagerCollectionABC(
    BaseListABC[_ScopedManagerT], ABC, Generic[_ScopedManagerT, _ScopableT]
):
    """
    ABC of ``ScopedManagerCollection``.
    """

    pass


class ScopedSetattrGuardABC(
    ScopedContextStackABC[_ScopableT, _EnterT_co], ABC, Generic[_ScopableT, _EnterT_co]
):

    @abstractmethod
    def scoped_setattr_guard_yield(
        self, __scoped: _ScopableT, __name: str, __value: Any
    ) -> Generator[Union[Stop, None], Any, Any]:
        """
        Guard on attribute set.
        """
        yield


class ScopedGetattrGuardABC(
    ScopedContextStackABC[_ScopableT, _EnterT_co], ABC, Generic[_ScopableT, _EnterT_co]
):

    @abstractmethod
    def scoped_getattr_guard_yield(
        self, __scoped: _ScopableT, __name: str
    ) -> Generator[Union[Stop, None], Any, Any]:
        """
        Guard on attribute get.
        """
        yield


class ScopedDelattrGuardABC(
    ScopedContextStackABC[_ScopableT, _EnterT_co], ABC, Generic[_ScopableT, _EnterT_co]
):

    @abstractmethod
    def scoped_delattr_guard_yield(
        self, __scoped: _ScopableT, __name: str
    ) -> Generator[Union[Stop, None], Any, Any]:
        """
        Guard on attribute delete.
        """
        yield


class ScopedAttrGuardCollectionABC(
    ABC,
    Generic[
        _ScopedSetattrGuardT, _ScopedGetattrGuardT, _ScopedDelattrGuardT, _ScopableT
    ],
):
    """
    ABC of ``ScopedGuardCollection``.
    """

    @abstractmethod
    def scoped_setattr_guard(
        self,
        __scoped: _ScopableT,
        __setattr_func: Callable[[str, Any], None],
        __name: str,
        __value: Any,
    ) -> None:
        """
        A controller function that calls guards on ``setattr``.
        """
        pass

    @abstractmethod
    def scoped_getattr_guard(
        self, __scoped: _ScopableT, __getattr_func: Callable[[str], Any], __name: str
    ) -> Any:
        """
        A controller function that calls guards on ``getattr``.
        """
        pass

    @abstractmethod
    def scoped_delattr_guard(
        self, __scoped: _ScopableT, __delattr_func: Callable[[str], None], __name: str
    ) -> None:
        """
        A controller function that calls guards on ``delattr``.
        """
        pass


class ScopedMixinABC(ABC, Generic[_ScopedManagerT]):
    """
    ABC of ``Scopable``.
    """

    # NOTE: These attributes should be created by subclasses.
    smx_scoped_managers: DescriptorProtocol[
        ScopedManagerCollectionABC[
            ScopedManagerABC["ScopedMixinABC", Any], "ScopedMixinABC"
        ]
    ]
    smx_scoped_attr_guards: DescriptorProtocol[
        ScopedAttrGuardCollectionABC[
            ScopedSetattrGuardABC["ScopedMixinABC", None],
            ScopedGetattrGuardABC["ScopedMixinABC", None],
            ScopedDelattrGuardABC["ScopedMixinABC", None],
            "ScopedMixinABC",
        ]
    ]
    smx_scoped_attr_guard_op_state: DescriptorProtocol[AttrOpStateABC]

    @abstractmethod
    def smx_scoped(
        self, __scoped_managers: Union[Iterable[_ScopedManagerT], EmptyFlag] = MISSING
    ) -> ContextManager[Tuple]:
        """
        Create a stack containing scoped context managers for initialization and cleanup.
        The scoped object ``self`` is bound to the context managers.
        """
        pass


#
# Attr Scopable ABC.
#


class AttrScopableABC(ABC):
    """
    ABC of ``AttrScopable``.
    """

    @abstractmethod
    def smx_assign(self, attr_assign: Mapping[str, Any]) -> ContextGeneratorABC:
        """
        Create a ``ScopedAttrAssign`` object and return ``scoped_gen`` with the
        ``scoped`` object bound to ``self``.
        """
        pass

    @abstractmethod
    def smx_restore(self, attrs: Iterable[str]) -> ContextGeneratorABC:
        """
        Create a ``ScopedAttrRestore`` object and return ``scoped_gen`` with the
        ``scoped`` object bound to ``self``.
        """
        pass
