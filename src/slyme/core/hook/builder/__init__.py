from abc import ABC, abstractmethod
from slyme.utils.typing.native import TypeVar, Generic

_ContextT = TypeVar("_ContextT")


class BuilderTemplateABC(ABC, Generic[_ContextT]):

    @abstractmethod
    def build(self, ctx: _ContextT) -> None:
        """
        Build the handler structure for pipelines.
        """
        pass

    @abstractmethod
    def call_build(self, ctx: _ContextT) -> None:
        """
        Perform a complete build operation for building pipelines.
        """
        pass
