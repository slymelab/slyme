from abc import abstractmethod
from contextlib import contextmanager
from slyme.utils.typing import (
    Generator,
    Any,
    Union,
    Iterable,
    Dict,
    TypeVar,
    Mapping,
    Self,
)
from slyme.utils.execution import GeneratorExecutor
from slyme.utils.constant import MISSING, Missing
from slyme.utils.collection import MutableSequenceProxy
from .common import Scope

_ScopedManagerT = TypeVar("_ScopedManagerT", bound="ScopedManager")


class ScopedManager(Scope):
    """``ScopedManager`` defines a generator method API used for scoped management."""

    @abstractmethod
    def scope(self, scoped: Any) -> Generator:
        """Inner API to be overridden by subclasses.

        This generator method will be called by ``enter_scope``.
        """
        pass

    @contextmanager
    def enter_scope(self, scoped: Any) -> Generator:
        manager_collection: Union[
            ScopedManagerList[ScopedManager], Missing
        ] = getattr(scoped, "_scoped_managers", MISSING)
        if manager_collection is MISSING:
            # Disable traceback.
            yield from self.scope(scoped)
        else:
            manager_collection.append(self)
            try:
                yield from self.scope(scoped)
            finally:
                del manager_collection[manager_collection.rindex(self)]


class ScopedManagerList(MutableSequenceProxy[_ScopedManagerT]):
    """A collection that contains entered scoped managers."""

    pass


# Scoped Attribute.
class ScopedAttrRestore(ScopedManager):

    def __init__(self, attrs: Iterable[str]) -> None:
        self.attrs = tuple(attrs)
        self.prev_values: Dict[str, Any] = {}

    def scope(self, scoped: Any) -> Generator[Self, Any, None]:
        for attr in self.attrs:
            # Only cache existing attributes of ``obj``.
            if hasattr(scoped, attr):
                self.prev_values[attr] = getattr(scoped, attr)
        try:
            yield self
        finally:
            for attr in self.attrs:
                # Restore the attributes.
                if attr in self.prev_values:
                    # Restore previously existing attributes before the scope.
                    setattr(scoped, attr, self.prev_values[attr])
                elif hasattr(scoped, attr):
                    # Remove previously non-existing attributes before the scope.
                    delattr(scoped, attr)
            # NOTE: Should clear the ``prev_value_dict`` for reuse.
            self.prev_values.clear()


class ScopedAttrAssign(ScopedAttrRestore):

    def __init__(self, attr_assign: Mapping[str, Any]) -> None:
        super().__init__(attr_assign.keys())
        self.attr_assign = attr_assign

    def scope(self, scoped: Any) -> Generator[Self, Any, None]:
        with GeneratorExecutor(super().scope(scoped)):
            for attr, value in self.attr_assign.items():
                setattr(scoped, attr, value)
            yield self
