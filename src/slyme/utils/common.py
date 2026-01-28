"""
useful but unrelated utils in slyme.
"""

# NOTE: The below module block is used to help ``Metaclasses`` create
# new classes, and it should be at the beginning of the file in order
# to avoid circular imports.
from typing import Hashable, Union, Any


class FuncParams:
    """
    Pack multiple function params in a single object.
    """
    __slots__ = ("args", "kwargs")

    def __init__(self, /, *args, **kwargs) -> None:
        self.args = args
        self.kwargs = kwargs

    def __repr__(self) -> str:
        sep = ", "
        params: list[str] = []
        arg_str = sep.join(map(repr, self.args))
        if arg_str:
            params.append(arg_str)
        kwarg_str = sep.join([f"{key}={value!r}" for key, value in self.kwargs.items()])
        if kwarg_str:
            params.append(kwarg_str)
        return f"{type(self).__name__}({sep.join(params)})"


class HashCache:
    """
    Cache the hash value of the given hashable item in order to improve
    efficiency.

    NOTE: ``hashable`` and ``hash_value`` are set as readonly attributes
    to maintain the consistency.

    NOTE: If the ``hashable`` param is actually not hashable, then the
    ``hash_value`` will be set to ``None``, and any attempt to get
    the hash value of ``HashCache`` will raise a ``TypeError``.
    """

    @property
    def hashable(self) -> Hashable:
        return self._hashable

    @hashable.setter
    def hashable(self, hashable: Hashable) -> None:
        if hasattr(self, "_hashable"):
            raise AttributeError("``hashable`` is readonly.")
        self._hashable = hashable

    @property
    def hash_value(self) -> int:
        return self._hash_value

    @hash_value.setter
    def hash_value(self, hash_value: int) -> None:
        if hasattr(self, "_hash_value"):
            raise AttributeError("``hash_value`` is readonly.")
        self._hash_value = hash_value

    def __init__(self, hashable: Hashable) -> None:
        self.hashable = hashable
        self.hash_value = hash(hashable)

    def __hash__(self) -> int:
        return self.hash_value

    def __eq__(self, other: Any, /) -> bool:
        """
        Determine whether the two hashable objects are equal.
        """
        return (
            isinstance(other, HashCache)
            and self.hash_value == other.hash_value
            and self.hashable == other.hashable
        )


def make_params_hashable(
    func_params: FuncParams,
    typed: bool = False,
    kwarg_mark: Hashable = object(),
    type_mark: Hashable = object(),
    fast_types: set[type] = {int, str},
) -> Union[Hashable, HashCache, None]:
    """
    Make the function params a hashable item and return. If there is
    only a single argument and its type is in ``fast_types``, directly
    return it as the hashable item. If ``typed`` is True, then the type
    of each param value is added in the hashable item and same values
    with different types will be treated as different (e.g., 1 and 1.0).

    NOTE: ``kwarg_mark`` and ``type_mark`` are two newly created object so
    that they are distinguishable from any given func params.

    NOTE: If the ``func_params`` contains non-hashable item (i.e., it is
    NOT hashable), then return ``None``.
    """
    args = func_params.args
    kwargs = func_params.kwargs
    # Make ``hashable`` a list here, because list modification is faster
    # than tuple.
    hashable = list(args)
    if kwargs:
        hashable.append(kwarg_mark)
        hashable.extend(kwargs.items())
    if typed:
        # Distinguish between different param types.
        hashable.append(type_mark)
        hashable.extend((type(v) for v in args))
        if kwargs:
            hashable.extend((type(v) for v in kwargs.values()))
    elif len(hashable) == 1 and type(hashable[0]) in fast_types:
        # If fast type, return the value itself.
        return hashable[0]
    # Return ``HashCache`` to improve efficiency.
    try:
        return HashCache(tuple(hashable))
    except TypeError:
        return


# NOTE: Other module blocks should be placed below (including the related
# imports) in order to avoid possible circular imports.
import sys
from contextlib import contextmanager
from collections.abc import Generator


@contextmanager
def enrich_exception(
    info: str,
    exc_types: Union[type[Exception], tuple[type[Exception], ...]] = Exception,
) -> Generator[None, None, None]:
    """
    Context manager to enrich exceptions with context info.
    """
    try:
        yield
    except exc_types as e:
        # Strategy 1: Modern Python (Preferred)
        if sys.version_info >= (3, 11):
            e.add_note(info)
            raise

        # Strategy 2: Legacy / Compatibility
        # Construct the new message
        if len(e.args) > 0 and isinstance(e.args[0], str):
            e.args = (f"{e.args[0]} ({info})", *e.args[1:])
        else:
            e.args = (*e.args, f"({info})")
        raise
