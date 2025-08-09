from abc import ABC, abstractmethod
from slyme.utils.abc.base.attr import AttrMixinABC
from slyme.utils.abc.base.scoped import ScopedManagerABC
from slyme.utils.typing.native import Union, Generic, TypeVar, Any, ContextManager
from slyme.utils.typing.extension import Nothing
from .scoped import ContextScopedInit

_CompileT = TypeVar("_CompileT")


class TempContextABC(AttrMixinABC[ScopedManagerABC], ABC):
    """
    NOTE: ``Temp`` in the name does NOT mean the context itself is temporal and may
    be destroyed, but means some attributes in the context can be re-initialized by
    calling the ``initialize__`` method.
    """

    def __init__(self) -> None:
        self.smx_initialize()

    @abstractmethod
    def smx_initialize(self) -> None:
        """
        Initialization of context object.
        """
        pass

    def smx_scoped_init(
        self, enter_init: bool = True, exit_init: bool = True
    ) -> ContextManager[None, Any, Any]:
        """
        A convenient wrapper for ``ContextScopedInit``.
        """
        return ContextScopedInit(
            enter_init=enter_init, exit_init=exit_init
        ).call_scoped_manager_yield(self)


class ContextABC(TempContextABC, ABC, Generic[_CompileT]):

    @property
    def compile(self) -> Union[_CompileT, Nothing]:
        """
        We additionally add the mixin property ``compile``, because we want to
        call the compile object like a function. (Specifically, ctx.compile(...)
        is more convenient than ctx.get_compile()(...))
        """
        return self.get_compile()

    @abstractmethod
    def set_compile(self, __compile: _CompileT) -> None:
        """
        Set the compile object to the context.
        """
        pass

    @abstractmethod
    def get_compile(self) -> Union[_CompileT, Nothing]:
        """
        Get the compile object. Return ``NOTHING`` if compile doesn't exit.
        """
        pass

    @abstractmethod
    def del_compile(self) -> None:
        """
        Remove the compile object.
        """
        pass


class HookContextABC(TempContextABC, ABC):
    """
    Builders, BuilderExtensions...
    """
