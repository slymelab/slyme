from abc import ABC, abstractmethod
from slyme.utils.typing.native import Dict, Any, TypeVar, Generic, Union, TypeVar
from slyme.utils.typing.extension import EmptyFlag, Nothing
from slyme.utils.common import FuncParams

_ContextT = TypeVar("_ContextT")
_ArgsT = TypeVar("_ArgsT")
_KwargsT = TypeVar("_KwargsT")


class CompileFuncParams(FuncParams[_ArgsT, _KwargsT], Generic[_ArgsT, _KwargsT]):
    """
    Pack multiple func parameters in a single ``CompileFuncParams`` object.
    Used as an indicator that the compile function accepts multiple args and
    the ``CompileFuncArgs`` object should be unpacked to call the compile
    function.
    """

    pass


class CompileABC(ABC, Generic[_ContextT]):

    @property
    def ctx(self) -> Union[_ContextT, Nothing]:
        """
        We additionally add the mixin property ``ctx`` for more convenient
        access of the attribute.
        """
        return self.get_ctx()

    @abstractmethod
    def set_ctx(self, ctx: Union[_ContextT, EmptyFlag]) -> None:
        """
        Bind context to the Compile object.
        """
        pass

    @abstractmethod
    def get_ctx(self) -> Union[_ContextT, Nothing]:
        """
        Get the bound context of the Compile object. If no context is bound,
        return ``NOTHING``.
        """
        pass

    @abstractmethod
    def del_ctx(self) -> None:
        """
        Remove the bound context from the Compile object.
        """
        pass

    @abstractmethod
    def __call__(self, **kwargs: Dict[str, Any]) -> None:
        """
        Compile attributes using given ``kwargs``.
        """
        pass
