from slyme.utils.store import Store, StoreKey
from slyme.utils.typing import TypeVar

_T = TypeVar("_T")


class RequiresKey(StoreKey[_T]):
    pass


class ProducesKey(StoreKey[_T]):
    pass


class RequiresProducesKey(RequiresKey[_T], ProducesKey[_T]):
    pass


class Context(Store):
    pass
