from contextlib import contextmanager
from abc import ABC, abstractmethod
from slyme.utils.typing.native import Any, Mapping, FrozenSet, Iterable, Self
from slyme.utils.typing.extension import MISSING
from slyme.utils.abc.descriptor import DescriptorProtocol
from .class_attr import ComputedClassAttrMixinABC


class ItemAttrSetMixinABC(ABC):

    @abstractmethod
    def __setitem__(self, __name: str, __value: Any) -> None:
        """
        Bind ``__setitem__`` to ``__setattr__``.
        """
        pass


class ItemAttrGetMixinABC(ABC):

    @abstractmethod
    def __getitem__(self, __name: str) -> Any:
        """
        Bind ``__getitem__`` to ``getattr``.
        """
        pass


class ItemAttrDelMixinABC(ABC):

    @abstractmethod
    def __delitem__(self, __name: str) -> None:
        """
        Bind ``__delitem__`` to ``delattr``.
        """
        pass


class ItemAttrMixinABC(
    ItemAttrSetMixinABC, ItemAttrGetMixinABC, ItemAttrDelMixinABC, ABC
):
    """
    Bind item operations to attribute operations.
    """

    pass


class AttrMixinABC(ABC):
    """
    ABC of ``Base``.
    """

    @abstractmethod
    def smx_from_kwargs(self, **kwargs) -> None:
        """
        Update ``Base`` attributes using kwargs.
        """
        pass

    @abstractmethod
    def smx_from_dict(self, __dict: Mapping[str, Any]) -> None:
        """
        Update ``Base`` attributes using a dict (or a mapping object).
        """
        pass

    @abstractmethod
    def smx_hasattr(self, __name: str) -> bool:
        """
        Check whether ``Base`` has the given attribute.
        """
        pass

    @abstractmethod
    def smx_popattr(self, __name: str, __default: Any = MISSING) -> Any:
        """
        Pop the given attribute. Similar to ``dict.pop``. If the attribute
        does not exist, return ``__default``.
        """
        pass


class AttrOpStateABC(ABC):
    active: bool

    @abstractmethod
    def set(self, active: bool):
        pass

    @contextmanager
    def scoped_set(self, active: bool):
        prev = self.active
        self.set(active)
        try:
            yield
        finally:
            self.set(prev)


ATTR_OP_STATE_ATTR_NAME = "smx_attr_op_state"


class BaseAttrOpMixinABC(ComputedClassAttrMixinABC, ABC):
    """Offers a unified base class for attr op.

    This class defines custom computed attrs, and offers ``smx_attr_op_state`` ability.

    NOTE: The following computed attributes are not separated into each mixin subclass,
    because, for example, ``SetattrOpMixin`` may need to escape some ``getattrs``, and
    the corresponding class attributes therefore should be computed properly.
    """

    smx_escaped_setattrs: Iterable[str]
    smx_escaped_setattrs_computed: FrozenSet[str]
    smx_escaped_getattrs: Iterable[str]
    smx_escaped_getattrs_computed: FrozenSet[str]
    smx_escaped_delattrs: Iterable[str]
    smx_escaped_delattrs_computed: FrozenSet[str]
    smx_attr_op_state: DescriptorProtocol[AttrOpStateABC]


class SetattrOpMixinABC(BaseAttrOpMixinABC):
    @abstractmethod
    def __setattr__(self, name, value, /) -> None:
        return super().__setattr__(name, value)

    @abstractmethod
    def smx_setattr_op(self, name, value, /) -> None:
        pass


class GetattrOpMixinABC(BaseAttrOpMixinABC):
    @abstractmethod
    def __getattribute__(self, name, /) -> Any:
        return super().__getattribute__(name)

    @abstractmethod
    def smx_getattr_op(self, name, /) -> Any:
        pass


class DelattrOpMixinABC(BaseAttrOpMixinABC):
    @abstractmethod
    def __delattr__(self, name, /) -> None:
        return super().__delattr__(name)

    @abstractmethod
    def smx_delattr_op(self, name, /) -> None:
        pass
