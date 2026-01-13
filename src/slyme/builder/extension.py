from abc import ABC, abstractmethod
from slyme.utils.composite import Component, ComponentList
from slyme.utils.typing import TypeVar, Generic, Generator

_ContextT = TypeVar("_ContextT")
_BuilderExtensionT = TypeVar("_BuilderExtensionT")


class BuilderExtension(ABC, Generic[_ContextT]):
    """
    Extension for custom handler build.
    """

    @abstractmethod
    def apply(self, ctx: _ContextT, node):
        """
        Build operations before and after ``build`` is called.
        TODO: 只保留 after hook
        """
        yield


class BuilderExtensionList(
    BuilderExtension[_ContextT],
    ComponentList[_BuilderExtensionT],
    ABC,
    Generic[_ContextT, _BuilderExtensionT],
):
    """
    Extension container that calls extensions.
    """
    # TODO
    pass
