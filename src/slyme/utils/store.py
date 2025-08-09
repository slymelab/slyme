"""
Global store module that provides global data management.
"""

import threading
from abc import ABCMeta

from .base.attr import ItemAttrMixin

from .base.observer import AttrObservable

from .metaclass.adapter import metaclasses
from .metaclass.singleton import SingletonMetaclass
from .metaclass.metabase import SingletonMetabase
from .typing.native import (
    Any,
    Union,
    TYPE_CHECKING,
    Sequence,
    Mapping,
    Iterable,
    ContextManager,
    Tuple,
    FrozenSet,
)
from .typing.extension import (
    is_slyme_naming,
    Missing,
    MISSING,
    NoneOrNothing,
    EmptyFlag,
)
from .base.attr import AttrMixin
from .decorator import not_implemented

# type hint only
if TYPE_CHECKING:
    from .base.observer import AttrObserver
    from .base.scoped import ScopedAttrAssign, ScopedAttrRestore
    from .execution import generator_context_manager
    from .abc.base.scoped import (
        ScopedManagerABC,
        ScopedManagerCollectionABC,
        ScopedAttrGuardCollectionABC,
        ScopedGuardABC,
        ScopedMixinABC,
    )

#
# Scoped Store
#


class StoreInstance(AttrMixin, AttrObservable):
    """
    A store instance that contains data.
    """

    def __init__(self) -> None:
        AttrMixin.__init__(self)
        AttrObservable.__init__(self)

    def smx_init(self, __name: str, __value: Any) -> None:
        """
        Init attribute only when it is not set or is ``MISSING``.
        """
        if not self.smx_hasattr(__name) or getattr(self, __name, MISSING) is MISSING:
            setattr(self, __name, __value)


#
# Store
#

# Attribute name used when assigning the ``ScopedStore`` to ``threading.local``.
STORE_INSTANCE_ATTR_NAME = "smx_store_instance"


class StoreLocal:
    """
    Plain local object that does not support thread-independent store. Can be faster
    than ``threading.local``, but you should make sure that the store won't be used
    in thread-independent scenarios.
    """

    __slots__ = (STORE_INSTANCE_ATTR_NAME,)


class StoreABC(
    ItemAttrMixin, SingletonMetabase, metaclass=metaclasses(SingletonMetaclass, ABCMeta)
):
    """
    ``StoreABC`` provides a global singleton helper that manages a set of
    ``ScopedStore`` instances.

    Attribute resolution order: If the attribute name to be accessed is a
    slyme naming, then it will first try to get the attribute from the
    ``StoreABC``, and if the attribute does not exist, then it will try to
    get the attribute from the ``ScopedStore`` returned by ``current__`` (
    referred to as 'the current store'). If the attribute name is NOT a
    slyme naming, then directly get it from the current store. Attribute set
    and del operations on ``StoreABC`` will be directly proxied to the
    current store, without considering the naming.

    NOTE: ``StoreABC`` should be strictly subclassed and create a new
    ``scoped_smx_store_local`` attribute in each subclass you create to ensure
    consistency and namespace independence.

    ``scoped_smx_store_local`` can be set to a ``threading.local`` object to
    make the store thread-independent, or can be set to a ``StoreLocal``
    object (or any other plain object) to be faster under thread-dependent
    scenarios (where multi-threading is not used or the multiple threads
    share the same store data).
    """

    smx_store_local: Union[StoreLocal, threading.local]

    def smx_current(self) -> StoreInstance:
        """
        Get the current ``ScopedStore``. The returned store will be different if
        ``scoped_smx_store_local`` is set to ``threading.local`` in multi-threading
        scenarios.
        """
        store_instance: Union[StoreInstance, Missing] = getattr(
            self.smx_store_local, STORE_INSTANCE_ATTR_NAME, MISSING
        )
        if store_instance is MISSING:
            store_instance = StoreInstance()
            setattr(self.smx_store_local, STORE_INSTANCE_ATTR_NAME, store_instance)
        return store_instance

    def __setattr__(self, __name: str, __value: Any) -> None:
        # Directly set the attribute to the current store.
        setattr(self.smx_current(), __name, __value)

    def __getattribute__(self, __name: str) -> Any:
        if __name in _STORE_ESCAPED_GETATTRS:
            return super().__getattribute__(__name)
        if is_slyme_naming(__name):
            # TODO: Change slyme_naming check into a static ESCAPED_GETATTR list
            # If it is slyme naming, then first try to
            # get the attribute from self.
            try:
                return super().__getattribute__(__name)
            except AttributeError:
                # ``AttributeError`` is ignored, and continue
                # to get the attribute from the current store.
                pass
        # NOTE: We do not use ``__getattr__`` to process the
        # above ``AttributeError``, because if the current store
        # does not have the attribute, the following ``getattr``
        # will be called twice (the first time is here, and the
        # second time is in the ``__getattr__``).
        # Get the attribute from the current store.
        return getattr(self.smx_current(), __name)

    def __delattr__(self, __name: str) -> None:
        # Directly del the attribute from the current store.
        delattr(self.smx_current(), __name)

    #
    # Overload functions for type hints.
    #

    # ScopedStore APIs.
    @not_implemented
    def init__(self, __name: str, __value: Any) -> None:
        pass

    # Observable APIs.
    @not_implemented
    def attach__(
        self,
        __observer: "AttrObserver",
        *,
        init: Union[bool, Missing] = MISSING,
        namespaces: Union[Sequence[str], Missing, NoneOrNothing] = MISSING
    ) -> None:
        pass

    @not_implemented
    def attach_attr__(
        self, __observer: "AttrObserver", __name: str, *, init: bool = True
    ) -> None:
        pass

    @not_implemented
    def detach__(
        __observer: "AttrObserver",
        *,
        namespaces: Union[Sequence[str], Missing, NoneOrNothing] = MISSING
    ) -> None:
        pass

    @not_implemented
    def detach_attr__(self, __observer: "AttrObserver", __name: str) -> None:
        pass

    # Scoped APIs.
    escaped_scoped_attrs__: Union[Iterable[str], Missing]
    escaped_scoped_attrs_computed__: FrozenSet[str]
    scoped_managers__: "ScopedManagerCollectionABC[ScopedManagerABC[ScopedMixinABC, Any], ScopedMixinABC]"
    scoped_guards__: "ScopedAttrGuardCollectionABC[ScopedGuardABC[ScopedMixinABC, Any], ScopedMixinABC]"

    @not_implemented
    def scoped__(
        self,
        __scoped_managers: Union[Iterable["ScopedManagerABC"], EmptyFlag] = MISSING,
    ) -> ContextManager[Tuple]:
        pass

    @not_implemented
    def is_scoped_guard_enabled__(self) -> bool:
        pass

    # ScopedAttr APIs.
    @not_implemented
    def assign__(
        self, attr_assign: Mapping[str, Any]
    ) -> "generator_context_manager[ScopedAttrAssign, Any, Any]":
        pass

    @not_implemented
    def restore__(
        self, attrs: Iterable[str]
    ) -> "generator_context_manager[ScopedAttrRestore, Any, Any]":
        pass

    # Base APIs.
    @not_implemented
    def from_kwargs__(self, **kwargs) -> None:
        pass

    @not_implemented
    def from_dict__(self, __dict: Mapping[str, Any]) -> None:
        pass

    @not_implemented
    def hasattr__(self, __name: str) -> bool:
        pass

    @not_implemented
    def pop__(self, __name: str, __default: Any = MISSING) -> Any:
        pass


# These attributes are escaped from ``__getattribute__`` and won't be passed
# to the scoped store.
_STORE_ESCAPED_GETATTRS = frozenset(
    [
        # ``scoped_smx_store_local`` should always be accessed in
        # ``StoreABC`` rather than in ``ScopedStore``, and the
        # ``AttributeError`` should be directly raised if the
        # attribute does not exist (mostly because the subclass
        # did not manually create it).
        "smx_store_local"
    ]
)
