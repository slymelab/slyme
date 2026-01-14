from abc import ABC, abstractmethod
from typing import TypeVar, Generic

_ContextT = TypeVar("_ContextT")


class Builder(ABC, Generic[_ContextT]):

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
        TODO: impl
        """
        pass
