import weakref
from threading import RLock
from slyme.utils.typing import (
    TypeVar,
    Self,
    Type,
    ClassVar,
    Any,
    Dict,
)
from slyme.utils.inspect import resolve_instance_classname, resolve_name
from slyme.utils.collection import MappingProxy
from .descriptor import FrozenAttr
from .meta import FrozenClsMeta

_FreezeMixinT = TypeVar("_FreezeMixinT", bound="FreezeMixin")


def _readonly_error(self, *args, **kwargs):
    raise TypeError(f"Instance of {resolve_instance_classname(self)} is frozen, and attributes can not be modified.")


def _freeze_eager(obj: _FreezeMixinT) -> _FreezeMixinT:
    """Create a frozen instance in the eager mode.

    In this mode, each frozen instance will dynamically create a new frozen subclass, which will cause more memory usage
    if a large number of frozen instances need to be created. However, eager mode can be safer than other implementations,
    because it uses ``FrozenAttr`` descriptor to provide the frozen view, and can raise an exception even if the attribute 
    is modified through ``object.__setattr__`` or ``object.__delattr__``.
    """
    cls = type(obj)
    attrs = obj._frozen_attr_view()
    namespace = {
        "__slots__": (),
        "__setattr__": _readonly_error,
        "__delattr__": _readonly_error,
    }
    for k, v in attrs.items():
        namespace[k] = FrozenAttr(v)
    frozen_cls = type(f"FrozenViewOf{resolve_name(cls)}", (cls,), namespace)  # NOTE: Metaclass will automatically be chosen from cls.
    frozen_instance = frozen_cls._create_frozen()
    return frozen_instance


def _create_cached_frozen_cls(cls: Type[_FreezeMixinT]) -> Type[_FreezeMixinT]:
    """Create a cached frozen cls using ``_frozen_view`` attribute as the frozen view."""
    namespace = {
        "__slots__": ("_frozen_view",),
        "__setattr__": _readonly_error,
        "__delattr__": _readonly_error,
    }
    frozen_cls = type(f"FrozenViewOf{resolve_name(cls)}", (cls,), namespace)  # NOTE: Metaclass will automatically be chosen from cls.

    def __getattribute__(self, name):
        _frozen_view = object.__getattribute__(self, "_frozen_view")
        if name in _frozen_view:
            return _frozen_view[name]
        return super(frozen_cls, self).__getattribute__(name)

    type.__setattr__(frozen_cls, "__getattribute__", __getattribute__)
    return frozen_cls


class _FrozenMappingProxy(MappingProxy[str, Any]):
    __slots__ = ("_mapping_source",)


def _freeze_cached(obj: _FreezeMixinT) -> _FreezeMixinT:
    """Create a frozen instance using cached frozen cls."""
    cls = type(obj)
    attr_view = _FrozenMappingProxy(obj._frozen_attr_view())
    if "_frozen_cls" in cls.__dict__:
        frozen_cls = cls._frozen_cls
    else:
        with cls._frozen_cls_rlock:
            if "_frozen_cls" in cls.__dict__:
                frozen_cls = cls._frozen_cls
            else:
                # Create a subclass of ``cls`` with frozen attributes.
                # This is done only once per class.
                # The frozen class will be cached in the class attribute.
                frozen_cls = _create_cached_frozen_cls(cls)
                type.__setattr__(cls, "_frozen_cls", frozen_cls)
    frozen_instance = frozen_cls._create_frozen()
    object.__setattr__(frozen_instance, "_frozen_view", attr_view)
    return frozen_instance


def _freeze_weakref_cached(obj: _FreezeMixinT) -> _FreezeMixinT:
    """Create a frozen instance using weakref frozen cls."""
    cls = type(obj)
    attr_view = _FrozenMappingProxy(obj._frozen_attr_view())
    frozen_cls = None
    if "_frozen_cls_weakref" in cls.__dict__:
        frozen_cls = cls._frozen_cls_weakref()
    if frozen_cls is None:
        with cls._frozen_cls_weakref_rlock:
            if "_frozen_cls_weakref" in cls.__dict__:
                frozen_cls = cls._frozen_cls_weakref()
            if frozen_cls is None:
                # Create a subclass of ``cls`` with frozen attributes.
                # The frozen class will be cached in the class attribute in a weakref manner.
                frozen_cls = _create_cached_frozen_cls(cls)
                type.__setattr__(cls, "_frozen_cls_weakref", weakref.ref(frozen_cls))
    frozen_instance = frozen_cls._create_frozen()
    object.__setattr__(frozen_instance, "_frozen_view", attr_view)
    return frozen_instance


_FREEZE_STRATEGY = {
    "eager": _freeze_eager,
    "cached": _freeze_cached,
    "weakref_cached": _freeze_weakref_cached,
}


class FreezeMixin(metaclass=FrozenClsMeta):
    """Dynamically create a frozen view of the instance."""
    _frozen_cls: ClassVar[Type[Self]]
    _frozen_cls_rlock: ClassVar[RLock]
    _frozen_cls_weakref: ClassVar[weakref.ReferenceType[Type[Self]]]
    _frozen_cls_weakref_rlock: ClassVar[RLock]

    def _frozen_attr_view(self) -> Dict[str, Any]:
        return {}

    @classmethod
    def _create_frozen(cls: Type[Self]) -> Self:
        return object.__new__(cls)

    def freeze(self, strategy: str = "weakref_cached") -> Self:
        return _FREEZE_STRATEGY[strategy](self)
