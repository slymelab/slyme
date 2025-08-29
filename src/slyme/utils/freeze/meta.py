from threading import RLock
from slyme.utils.inspect import resolve_name

FROZEN_CLS_RLOCK_ATTR_NAME = "_frozen_cls_rlock"
FROZEN_CLS_WEAKREF_RLOCK_ATTR_NAME = "_frozen_cls_weakref_rlock"


class FrozenClsMeta(type):
    def __init__(cls, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # NOTE: Should set RLock to each class.
        type.__setattr__(cls, FROZEN_CLS_RLOCK_ATTR_NAME, RLock())
        type.__setattr__(cls, FROZEN_CLS_WEAKREF_RLOCK_ATTR_NAME, RLock())

    def __setattr__(cls, name, value):
        raise TypeError(f"Class ``{resolve_name(cls)}`` is frozen and cannot be modified.")

    def __delattr__(cls, name):
        raise TypeError(f"Class ``{resolve_name(cls)}`` is frozen and cannot be modified.")
