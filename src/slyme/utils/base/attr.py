#
# ItemAttrBinding
#

from functools import wraps
from slyme.utils.abc.base.attr import (
    AttrMixinABC,
    ItemAttrDelMixinABC,
    ItemAttrGetMixinABC,
    ItemAttrMixinABC,
    ItemAttrSetMixinABC,
    AttrOpStateABC,
    BaseAttrOpMixinABC,
    SetattrOpMixinABC,
    GetattrOpMixinABC,
    DelattrOpMixinABC,
    ATTR_OP_STATE_ATTR_NAME,
)
from slyme.utils.metaclass.class_attr import ClassAttrCompute
from slyme.utils.typing.extension import MISSING, resolve_instance_classname
from slyme.utils.typing.native import Any, Mapping, FrozenSet
from slyme.utils.decorator import auto_decorator
from slyme.utils.descriptor import (
    get_descriptor_private_name,
    ReadonlyCachedDescriptor,
)
from . import BaseObjectInit
from .class_attr import ComputedClassAttrMixin


class ItemAttrSetMixin(ItemAttrSetMixinABC):
    """
    Bind ``__setitem__`` to ``__setattr__``.
    """

    def __setitem__(self, __name: str, __value: Any) -> None:
        return setattr(self, __name, __value)


class ItemAttrGetMixin(ItemAttrGetMixinABC):
    """
    Bind ``__getitem__`` to ``getattr``.
    """

    def __getitem__(self, __name: str) -> Any:
        return getattr(self, __name)


class ItemAttrDelMixin(ItemAttrDelMixinABC):
    """
    Bind ``__delitem__`` to ``delattr``.
    """

    def __delitem__(self, __name: str) -> None:
        return delattr(self, __name)


class ItemAttrMixin(
    ItemAttrSetMixin, ItemAttrGetMixin, ItemAttrDelMixin, ItemAttrMixinABC
):
    """
    Bind item operations to attribute operations.
    """

    pass


#
# AttrMixin
#


class AttrMixin(AttrMixinABC, BaseObjectInit):
    """
    ``Base`` class provides abundant object services:

    - Other extended APIs (such as ``from_kwargs__``, ``from__dict__``, ``pop__``, etc.).
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    def smx_from_kwargs(self, **kwargs) -> None:
        self.smx_from_dict(kwargs)

    def smx_from_dict(self, __dict: Mapping[str, Any]) -> None:
        # FIXME: directly update __dict__ will escape from custom ``__setattr__`` methods.
        self.__dict__.update(__dict)

    def smx_hasattr(self, __name: str) -> bool:
        return hasattr(self, __name)

    def smx_popattr(self, __name: str, __default: Any = MISSING) -> Any:
        if self.smx_hasattr(__name):
            value = getattr(self, __name)
            delattr(self, __name)
        else:
            value = __default
        return value

    def __str__(self) -> str:
        from slyme.utils.common import dict_to_key_value_str

        classname = resolve_instance_classname(self)
        _id = str(hex(id(self)))
        _dict = dict_to_key_value_str(self.__dict__)
        return f"{classname}<{_id}>({_dict})"


#
# AttrOpMixin
#


class AttrOpState(AttrOpStateABC):
    def __init__(self):
        # ``active`` is ``True`` by default.
        self.active = True

    def set(self, active: bool):
        if not isinstance(active, bool):
            raise ValueError(
                f"``value`` should be a boolean type, got ``{type(active)}`` instead."
            )
        self.active = active


class BaseAttrOpMixin(ComputedClassAttrMixin, BaseAttrOpMixinABC):
    smx_class_attr_compute = (
        ClassAttrCompute("smx_escaped_setattrs", "smx_escaped_setattrs_computed"),
        ClassAttrCompute("smx_escaped_getattrs", "smx_escaped_getattrs_computed"),
        ClassAttrCompute("smx_escaped_delattrs", "smx_escaped_delattrs_computed"),
    )
    smx_escaped_setattrs = (
        ATTR_OP_STATE_ATTR_NAME,
        get_descriptor_private_name(ATTR_OP_STATE_ATTR_NAME),
    )
    smx_escaped_getattrs = tuple(smx_escaped_setattrs)
    smx_escaped_delattrs = tuple(smx_escaped_setattrs)

    @ReadonlyCachedDescriptor
    def smx_attr_op_state(self) -> AttrOpStateABC:
        return AttrOpState()


class SetattrOpMixin(BaseAttrOpMixin, SetattrOpMixinABC):
    # NOTE: Add the following ``smx_escaped_getattrs`` to:
    # 1. Hide internal implementation details (i.e., the outside classes will not be informed when getting the following attributes).
    # 2. Support multiple inheritance scenarios (i.e., the ``smx_escaped_getattrs`` will only work when inheriting ``GetattrOpMixin``
    # in the subclass).
    smx_escaped_getattrs = (
        "smx_escaped_setattrs",
        "smx_escaped_setattrs_computed",
        "smx_setattr_op",
    )

    def __setattr__(self, name, value, /):
        if (
            name in self.smx_escaped_setattrs_computed
            or not self.smx_attr_op_state.active
        ):
            return super().__setattr__(name, value)
        else:
            # Call ``smx_setattr_op`` method chain.
            return self.smx_setattr_op(name, value)

    def smx_setattr_op(self, name, value, /):
        # Call ``super().__setattr__`` to continue the method chain.
        return super().__setattr__(name, value)


class GetattrOpMixin(BaseAttrOpMixin, GetattrOpMixinABC):
    # NOTE: Add the following ``smx_escaped_getattrs`` to:
    # 1. Avoid ``RecursionError``.
    # 2. Hide internal implementation details (i.e., the outside classes will not be informed when getting the following attributes).
    smx_escaped_getattrs = (
        "smx_escaped_getattrs",
        "smx_escaped_getattrs_computed",
        "smx_getattr_op",
    )

    def __getattribute__(self, name, /):
        # Avoid ``RecursionError`` here.
        escaped_getattrs: FrozenSet[str] = super().__getattribute__(
            "smx_escaped_getattrs_computed"
        )
        if name in escaped_getattrs or not self.smx_attr_op_state.active:
            return super().__getattribute__(name)
        else:
            # Call ``smx_getattr_op`` method chain.
            return self.smx_getattr_op(name)

    def smx_getattr_op(self, name, /):
        # Call ``super().__getattribute__`` to continue the method chain.
        return super().__getattribute__(name)


class DelattrOpMixin(BaseAttrOpMixin, DelattrOpMixinABC):
    # NOTE: Add the following ``smx_escaped_getattrs`` to:
    # 1. Hide internal implementation details (i.e., the outside classes will not be informed when getting the following attributes).
    # 2. Support multiple inheritance scenarios (i.e., the ``smx_escaped_getattrs`` will only work when inheriting ``GetattrOpMixin``
    # in the subclass).
    smx_escaped_getattrs = (
        "smx_escaped_delattrs",
        "smx_escaped_delattrs_computed",
        "smx_delattr_op",
    )

    def __delattr__(self, name, /):
        if (
            name in self.smx_escaped_delattrs_computed
            or not self.smx_attr_op_state.active
        ):
            return super().__delattr__(name)
        else:
            # Call ``smx_delattr_op`` method chain.
            return self.smx_delattr_op(name)

    def smx_delattr_op(self, name, /):
        # Call ``super().__delattr__`` to continue the method chain.
        return super().__delattr__(name)


@auto_decorator(index=0, keyword="_func")
def with_attr_op_state(_func=MISSING, *, active: bool):
    def decorator(func):
        @wraps(func)
        def wrapper(self: BaseAttrOpMixin, *args, **kwargs):
            with self.smx_attr_op_state.scoped_set(active):
                return func(self, *args, **kwargs)

        return wrapper

    return decorator
