"""
useful but unrelated utils in slyme.
"""

#
# NOTE: The below module block is used to help ``Metaclasses`` create
# new classes, and it should be at the beginning of the file in order
# to avoid circular imports.
#
from .typing import Generic, TypeVar, Hashable, Set, Type, Union, Any, List
from .inspect import resolve_instance_classname
from .descriptor.property import ReadonlyProperty, get_descriptor_private_name

_ArgsT = TypeVar("_ArgsT")
_KwargsT = TypeVar("_KwargsT")


class FuncParams(Generic[_ArgsT, _KwargsT]):
    """
    Pack multiple function params in a single object.
    """

    def __init__(self, /, *args: _ArgsT, **kwargs: _KwargsT) -> None:
        self.args = args
        self.kwargs = kwargs

    def __str__(self) -> str:
        sep = ", "
        params: List[str] = []
        arg_str = sep.join(map(str, self.args))
        if arg_str:
            params.append(arg_str)
        kwarg_str = dict_to_key_value_str(self.kwargs, str_sep=sep)
        if kwarg_str:
            params.append(kwarg_str)
        return f"{resolve_instance_classname(self)}({sep.join(params)})"


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

    __slots__ = (
        get_descriptor_private_name("hashable"),
        get_descriptor_private_name("hash_value"),
    )
    hashable = ReadonlyProperty()
    hash_value = ReadonlyProperty()

    def __init__(self, hashable: Hashable) -> None:
        self.hashable = hashable
        try:
            self.hash_value = hash(hashable)
        except TypeError:
            self.hash_value = None

    def __hash__(self) -> int:
        return self.hash_value

    def __eq__(self, __value: Any) -> bool:
        """
        Determine whether the two hashable objects are equal.
        """
        return (
            isinstance(__value, HashCache)
            and self.hash_value == __value.hash_value
            and self.hashable == __value.hashable
        )


def make_params_hashable(
    func_params: FuncParams[Hashable, Hashable],
    typed: bool = False,
    kwarg_mark: Hashable = object(),
    type_mark: Hashable = object(),
    fast_types: Set[Type] = {int, str},
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
    hash_cache = HashCache(tuple(hashable))
    return hash_cache if hash_cache.hash_value is not None else None


#
# NOTE: Other module blocks should be placed below (including the related
# imports) in order to avoid possible circular imports.
#
from .typing import Mapping


# dict and list formatter
def dict_to_key_value_str_list(dict_: Mapping, key_value_sep: str = "=", /) -> list:
    """
    Parse items in a dict to a str list using ``key_value_sep`` to concat
    the keys and values.
    """
    return [f"{key}{key_value_sep}{value}" for key, value in dict_.items()]


def dict_to_key_value_str(
    dict_: Mapping, key_value_sep: str = "=", str_sep: str = ", ", /
) -> str:
    """
    Parse items in a dict to a str using ``key_value_sep`` to concat the
    keys and values, and using ``str_sep`` to concat the items.
    """
    return str_sep.join(dict_to_key_value_str_list(dict_, key_value_sep=key_value_sep))
