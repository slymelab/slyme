from slyme.utils.store import Store, Key
from slyme.utils.typing import TypeVar

_T = TypeVar("_T")


class RequiresKey(Key[_T]):
    pass


class ProducesKey(Key[_T]):
    pass


class RequiresProducesKey(RequiresKey[_T], ProducesKey[_T]):
    pass


class Context(Store):
    pass
