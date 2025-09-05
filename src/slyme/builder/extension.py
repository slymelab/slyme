from abc import ABC, abstractmethod
from slyme.utils.collection import MutableSequenceProxy
from slyme.utils.typing import TypeVar, Generic, Generator

_ContextT = TypeVar("_ContextT")
_BuilderExtensionTemplateT = TypeVar("_BuilderExtensionTemplateT")


class BuilderExtensionTemplate(ABC, Generic[_ContextT]):
    """
    Extension for custom handler build.
    """

    @abstractmethod
    def _build_yield(self, ctx: _ContextT) -> Generator:
        """
        Build operations before and after ``build`` is called.
        """
        yield


class BuilderExtensionContainerTemplate(
    BuilderExtensionTemplate[_ContextT],
    MutableSequenceProxy[_BuilderExtensionTemplateT],
    ABC,
    Generic[_ContextT, _BuilderExtensionTemplateT],
):
    """
    Extension container that calls extensions.
    """
    # TODO
    pass
