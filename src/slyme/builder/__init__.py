from abc import ABC, abstractmethod
from slyme.utils.typing import TypeVar, Generic

_ContextT = TypeVar("_ContextT")


class BuilderTemplate(ABC, Generic[_ContextT]):

    @abstractmethod
    def _build(self, ctx: _ContextT) -> None:
        """
        Build the handler structure for pipelines.
        """
        pass

    @abstractmethod
    def build(self, ctx: _ContextT) -> None:
        """
        Perform a complete build operation for building pipelines.
        """
        pass
