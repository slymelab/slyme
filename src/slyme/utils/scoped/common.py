from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from slyme.utils.typing import Any


class Scope(ABC):
    @abstractmethod
    def enter_scope(self, scoped: Any) -> AbstractContextManager:
        pass
