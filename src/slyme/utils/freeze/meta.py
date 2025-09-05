from threading import RLock
from slyme.utils.inspect import resolve_name


class FrozenClsMeta(type):
    _frozen_cls_rlock: RLock
    _frozen_cls_weakref_rlock: RLock

    def __init__(cls, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # NOTE: Should set RLock to each class.
        type.__setattr__(cls, "_frozen_cls_rlock", RLock())
        type.__setattr__(cls, "_frozen_cls_weakref_rlock", RLock())

    def __setattr__(cls, name, value):
        raise TypeError(f"Class ``{resolve_name(cls)}`` is frozen and cannot be modified.")

    def __delattr__(cls, name):
        raise TypeError(f"Class ``{resolve_name(cls)}`` is frozen and cannot be modified.")
