from abc import ABC, abstractmethod
from typing import TypeVar, Generic
from slyme.utils.collection import MutableSequenceProxy

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
    MutableSequenceProxy[_BuilderExtensionT],
    ABC,
    Generic[_ContextT, _BuilderExtensionT],
):
    """
    Extension container that calls extensions.
    """
    # TODO
    pass
