from contextlib import AbstractContextManager
from slyme.utils.typing import (
    Any,
    Iterable,
    Mapping,
)
from slyme.utils.execution import context_manager_stack
from slyme.utils.descriptor.protocol import Attribute
from .common import Scope
from .manager import (
    ScopedManagerList,
    ScopedManager,
    ScopedAttrRestore,
    ScopedAttrAssign,
)


class ScopedMixin:
    """Provides scoped APIs"""

    __slots__ = ()
    _scoped_managers: Attribute[
        ScopedManagerList[ScopedManager], ScopedManagerList[ScopedManager]
    ]

    def __init__(self, /, **kwargs):
        super().__init__(**kwargs)
        self._scoped_managers = ScopedManagerList()

    def scoped(self, scopes: Iterable[Scope]) -> AbstractContextManager:
        return context_manager_stack(scope.enter_scope(self) for scope in scopes)

    def scoped_attr_restore(self, attrs: Iterable[str]) -> AbstractContextManager:
        return ScopedAttrRestore(attrs).enter_scope(self)

    def scoped_attr_assign(
        self, attr_assign: Mapping[str, Any]
    ) -> AbstractContextManager:
        return ScopedAttrAssign(attr_assign).enter_scope(self)
