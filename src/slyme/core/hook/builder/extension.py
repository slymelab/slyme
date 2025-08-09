from abc import ABC, abstractmethod
from slyme.utils.abc.base.collection import BaseListABC
from slyme.utils.typing.native import TypeVar, Generic, Generator

_ContextT = TypeVar("_ContextT")
_BuilderExtensionTemplateT = TypeVar("_BuilderExtensionTemplateT")


class BuilderExtensionTemplateABC(ABC, Generic[_ContextT]):
    """
    Extension for custom handler build.
    """

    @abstractmethod
    def build_yield(self, ctx: _ContextT) -> Generator:
        """
        Build operations before and after ``build`` is called.
        """
        yield


class BuilderExtensionContainerTemplateABC(
    BuilderExtensionTemplateABC[_ContextT],
    BaseListABC[_BuilderExtensionTemplateT],
    ABC,
    Generic[_ContextT, _BuilderExtensionTemplateT],
):
    """
    Extension container that calls extensions.
    """

    pass
