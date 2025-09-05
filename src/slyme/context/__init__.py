from abc import ABC, abstractmethod
from slyme.utils.abc.base.attr import AttrMixinABC
from slyme.utils.abc.base.scoped import ScopedManagerABC
from slyme.utils.typing import Union, Generic, TypeVar, Any, ContextManager
from slyme.utils.constant import Nothing
from .scoped import ContextScopedInit

_CompileT = TypeVar("_CompileT")


class Context:
    pass
