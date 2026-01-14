import inspect
from itertools import chain, filterfalse
from slyme.utils.inspect import (
    class_difference,
    resolve_bases,
    resolve_minimal_classes,
)
from slyme.utils.typing import Any, Mapping, Union
from slyme.utils.common import make_params_hashable, FuncParams


def create_metaclass_adapter(*metaclasses: type, **kwargs) -> type[type]:
    """
    Create a new metaclass adapter with given ``metaclasses`` and ``kwargs``.
    """

    class MetaclassAdapter(*metaclasses, **kwargs):
        # ``_is_metaclass_adapter`` works as an indicator attribute that denotes the
        # metaclass simply inherits multiple metaclasses and does nothing else.
        _is_metaclass_adapter: bool = True

        def __repr__(self) -> str:
            return f"{super().__repr__()}{metaclasses}"

    return MetaclassAdapter


def is_metaclass_adapter(cls: type) -> bool:
    """
    Check if a class is a metaclass adapter. Return ``True`` if and only if ``cls`` has attribute
    ``_is_metaclass_adapter`` and the value is ``True``.
    """
    return getattr(cls, "_is_metaclass_adapter", False) is True


class MetaclassResolver:
    """
    Resolve a proper metaclass that is compatible with both user specified metaclasses and
    the metaclasses of the bases. Use a cache dict to improve performance.
    """

    # A dict that stores metaclass adapters with tuple of bases and kwargs as the dict key.
    _metaclass_adapter_dict: dict[Any, type] = {}

    @classmethod
    def resolve(
        cls,
        bases: tuple[type, ...],
        metaclasses: tuple[Union[type, None], ...],
        *,
        strict: bool = True,
        meta_kwargs: Union[Mapping[str, Any], None] = None,
    ) -> type:
        """
        Resolve a proper metaclass with given ``bases`` and ``metaclasses``.
        """
        # Get the metaclass of each base class.
        meta_bases = (type(base) for base in bases)
        # Get the minimal meta bases.
        meta_bases = resolve_minimal_classes(meta_bases)
        # Get the pure metaclasses without ``None``.
        pure_metaclasses: tuple[type, ...] = tuple(
            filter(cls.class_filter, metaclasses)
        )
        # Get meta bases difference.
        meta_bases = class_difference(meta_bases, pure_metaclasses)

        equivalent_metaclasses = resolve_minimal_classes(meta_bases + pure_metaclasses)
        if len(equivalent_metaclasses) == 0:
            # If no metaclasses can be found, then directly return ``type``.
            return type
        elif len(equivalent_metaclasses) == 1:
            # If the 'most derived class' can be automatically found, then
            # directly return it.
            return equivalent_metaclasses[0]

        if strict:
            # Resolve all the non-adapter metaclasses that should be specified
            # in ``metaclasses``.
            meta_bases = cls.resolve_required_and_adapters(meta_bases, pure_metaclasses)
        final_metaclasses = cls.resolve_final_metaclasses(meta_bases, metaclasses)
        return cls.load_metaclass_adapter(
            final_metaclasses, meta_kwargs if meta_kwargs is not None else {}
        )

    @classmethod
    def resolve_required_and_adapters(
        cls, meta_bases: tuple[type, ...], metaclasses: tuple[type, ...]
    ) -> tuple[type, ...]:
        """
        Parse and check the required metaclasses to be specified by the users in
        the strict mode. Return the remaining metaclass adapters to be further
        processed.
        """
        required_metaclasses = set(filterfalse(is_metaclass_adapter, meta_bases))
        # The ``meta_bases`` should only keep adapter metaclasses.
        meta_bases = tuple(filter(is_metaclass_adapter, meta_bases))

        meta_queue = list(meta_bases)
        while meta_queue:
            meta_cls = meta_queue.pop(0)
            if is_metaclass_adapter(meta_cls):
                # Add the bases of the adapter metaclass to the queue.
                meta_queue.extend(resolve_bases(meta_cls))
            else:
                required_metaclasses.add(meta_cls)
        # Resolve all the mro of metaclasses.
        metaclass_set = set(chain(*(inspect.getmro(_cls) for _cls in metaclasses)))
        missing_metaclasses = tuple(
            (
                required_cls
                for required_cls in required_metaclasses
                if required_cls not in metaclass_set
            )
        )
        if missing_metaclasses:
            raise ValueError(
                f"When ``strict`` is set to ``True`` in ``Metaclasses``, you should "
                f"manually list all the required metaclasses excluding the metaclass "
                f"adapters. Missing metaclasses: {missing_metaclasses}"
            )
        return meta_bases

    @classmethod
    def resolve_final_metaclasses(
        cls, meta_bases: tuple[type, ...], metaclasses: tuple[type, ...]
    ) -> tuple[type, ...]:
        """
        Resolve the final metaclass sequence. Insert ``meta_bases`` into ``metaclasses``
        at proper positions and remove ``None`` values.
        """
        final_metaclasses = list(metaclasses)
        if None not in final_metaclasses:
            # ``None`` is the last item by default.
            final_metaclasses.append(None)
        # Insert the ``meta_bases`` into proper positions.
        insertions: list[tuple[type, Union[type, None]]] = []
        for meta_base in meta_bases:
            insertion_found = False
            for meta_cls in final_metaclasses:
                if meta_cls is None:
                    # Insert before ``None``
                    insertions.append((meta_base, None))
                    insertion_found = True
                    break
                elif issubclass(meta_base, meta_cls):
                    # Insert before the first occurrence of super class.
                    insertions.append((meta_base, meta_cls))
                    insertion_found = True
                    break
            if not insertion_found:
                raise TypeError(
                    f"The ``meta_base`` {meta_base} cannot find an insertion position in "
                    f"the metaclasses: ``{final_metaclasses}``"
                )
        for meta_base, insert_anchor in insertions:
            final_metaclasses.insert(final_metaclasses.index(insert_anchor), meta_base)
        # Remove ``None`` from ``final_metaclasses``.
        final_metaclasses = tuple(filter(cls.class_filter, final_metaclasses))
        return final_metaclasses

    @classmethod
    def load_metaclass_adapter(
        cls, final_metaclasses: tuple[type, ...], meta_kwargs: Mapping[str, Any]
    ) -> type:
        """
        Load metaclass with given ``final_metaclasses`` and ``meta_kwargs``. If adapter
        cache found, then directly return, otherwise create a new metaclass adapter and
        cache it in the ``_metaclass_adapter_dict``. If the params are not hashable,
        then directly create a new metaclass adapter and return (without caching it).
        """
        key = make_params_hashable(FuncParams(*final_metaclasses, **meta_kwargs))
        if key is not None and key in cls._metaclass_adapter_dict:
            return cls._metaclass_adapter_dict[key]
        # Create a new metaclass adapter.
        metaclass_adapter = create_metaclass_adapter(*final_metaclasses, **meta_kwargs)
        if key is not None:
            cls._metaclass_adapter_dict[key] = metaclass_adapter
        return metaclass_adapter

    @classmethod
    def make_func(
        cls,
        *metaclasses: Union[type, None],
        strict: bool = True,
        meta_kwargs: Union[Mapping[str, Any], None] = None,
    ):
        """
        Make a metaclass function used to create a class.
        """

        def _metaclass_func(
            name: str,
            bases: tuple[type, ...],
            namespace: dict[str, Any],
            /,
            **kwargs: Any,
        ):
            return cls.resolve(
                bases, metaclasses, strict=strict, meta_kwargs=meta_kwargs
            )(name, bases, namespace, **kwargs)

        return _metaclass_func

    @staticmethod
    def class_filter(cls: Union[type, None], /) -> bool:
        """
        Filter function that removes ``None`` values.
        """
        return cls is not None


def metaclasses(
    *metaclasses: Union[type, None],
    strict: bool = True,
    meta_kwargs: Union[Mapping[str, Any], None] = None,
):
    """
    Return a proper metaclass that is compatible with all the user specified ``metaclasses``
    as well as the metaclasses of the bases. It makes the adaptation of metaclasses convenient,
    which does not need the user manually define a new sub metaclass.

    NOTE: This function only applies when each of the metaclasses to be adapted is a ``class``
    rather than a ``function``. Note that ``class Example(metaclass=func)`` is also accepted
    by the Python interpreter, but it doesn't apply here. If you want to make the ``func``
    compatible with it, you can define a new metaclass and call the ``func`` in the ``__new__``
    method of the metaclass.
    """
    return MetaclassResolver.make_func(
        *metaclasses, strict=strict, meta_kwargs=meta_kwargs
    )
