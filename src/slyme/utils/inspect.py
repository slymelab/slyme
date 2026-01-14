import inspect
from types import FunctionType, MethodType
from .typing import Any, Tuple, Union, Iterable, Type, Set

FuncOrMethodType = Union[FunctionType, MethodType]


def resolve_name(named: Any, /) -> str:
    """
    Resolve the name of the given object based on the following order:

    - ``__named.__name__``
    - ``__named.__qualname__``
    - ``str(__named)``
    - ``repr(__named)``

    NOTE: Empty str will be seen as failure, and the function will continue
    to check the next naming item until the end.
    """
    # NOTE: Use multiple if-return statements here to improve efficiency.
    name: Union[str, None] = getattr(named, "__name__", None)
    if name:
        return name
    name: Union[str, None] = getattr(named, "__qualname__", None)
    if name:
        return name
    name = str(named)
    if name:
        return name
    return repr(named)


def resolve_instance_classname(obj: Any, /) -> str:
    """
    Try to resolve the classname of the given instance object.
    """
    # NOTE: Use ``type`` rather than ``__obj.__class__``, because the former is more valid,
    # especially when the ``__getattribute__`` method is overridden by ``__obj`` (e.g.,
    # ``NOTHING.__class__`` will return ``NOTHING`` itself rather than the ``Nothing`` class).
    return resolve_name(type(obj))


def resolve_bases(cls: Type, /) -> Tuple[Type, ...]:
    """
    Safely resolve the bases of any given class. NOTE: If the class has the
    attribute ``__bases__``, then directly return it. Otherwise (e.g.,
    typing.Sequence), this function resolves the mro and returns the
    'minimal base class set'.

    The 'minimal base class set' denotes that there doesn't exist inheritance
    relationship in this set and the set only keeps the most subclasses classes.

    Example:
        ```Python
        class A: pass

        class B(A): pass

        class C(B, A): pass

        # The bases of class ``C`` is (B, A)
        print(C.__bases__)
        # However, the 'minimal base class set' of ``C`` is (B,) according to the
        # definition.

        # ``resolve_bases(C)`` still returns (B, A) because class ``C`` has attribute
        # ``__bases__``
        print(resolve_bases(C))

        # NOTE: ``typing.Sequence`` is different from ``collections.abc.Sequence``
        # and it doesn't have ``__bases__`` or ``__mro__``.
        from typing import Sequence
        print(hasattr(Sequence, '__bases__'))
        print(hasattr(Sequence, '__mro__'))
        print(resolve_bases(Sequence))

        # Output:
        # False
        # False
        # (<class 'collections.abc.Sequence'>,)
        ```
    """
    # If ``cls`` has ``__bases__``, then directly return.
    if hasattr(cls, "__bases__"):
        return cls.__bases__

    # Get the mro of ``cls`` (excluding itself).
    mro_classes = list((_cls for _cls in inspect.getmro(cls) if _cls is not cls))
    bases = []
    while mro_classes:
        # NOTE: should pop the first element in the list (index=0).
        base = mro_classes.pop(0)
        bases.append(base)
        # Get the mro of ``base`` and remove them from ``mro_classes``.
        base_mro_set = set(inspect.getmro(base))
        mro_classes = list((_cls for _cls in mro_classes if _cls not in base_mro_set))
    return tuple(bases)


#
# Resolve minimal classes.
#


def _resolve_minimal_classes_through_subclass(
    classes: Iterable[Type], /
) -> Tuple[Type, ...]:
    """
    Implement ``resolve_minimal_classes`` through ``issubclass`` method.
    """
    # NOTE: should create a new tuple of ``__classes``, because some iterable items DO NOT
    # support iterating multiple times.
    classes = tuple(classes)
    # For class deduplication.
    seen_classes: Set[Type] = set()

    def _filter_func(cls: Type) -> bool:
        if cls in seen_classes:
            return False

        for other_cls in classes:
            if issubclass(other_cls, cls) and cls is not other_cls:
                # Not the 'minimal class'.
                return False
        # For deduplication.
        seen_classes.add(cls)
        return True

    return tuple(filter(_filter_func, classes))


def _resolve_minimal_classes_through_mro(classes: Iterable[Type], /) -> Tuple[Type, ...]:
    """
    Implement ``resolve_minimal_classes`` through ``mro`` method.

    NOTE: This implementation ignores the virtual subclasses (e.g., classes using
    ``ABC.register``), and it will be faster when number of ``__classes`` is large.
    """
    # NOTE: should create a new tuple of ``__classes``, because some iterable items DO NOT
    # support iterating multiple times.
    classes = tuple(classes)
    # Build a super class set that contains all the super classes of ``__classes`` (excluding
    # themselves).
    super_class_set: Set[Type] = set()
    for cls in classes:
        super_class_set.update(inspect.getmro(cls)[1:])

    def _filter_func(cls: Type) -> bool:
        result = cls not in super_class_set
        # NOTE: Add ``cls`` into ``super_class_set``
        # to deduplicate the following classes.
        super_class_set.add(cls)
        return result

    return tuple(filter(_filter_func, classes))


_RESOLVE_MINIMAL_CLASSES_REGISTRY = {
    "subclass": _resolve_minimal_classes_through_subclass,
    "mro": _resolve_minimal_classes_through_mro,
}


def resolve_minimal_classes(
    classes: Iterable[Type], /, *, algo: str = "subclass"
) -> Tuple[Type, ...]:
    """
    Resolve the 'minimal classes' of the given class iterable. 'minimal classes' denotes that
    any of the classes which do not have a subclass is contained in the tuple, otherwise not.
    The original order of 'minimal classes' is kept. If multiple same classes exist in the
    given class iterable, the first occurrence is kept.

    Different ``algo`` values correspond to different implementations:

    - subclass: compare the classes using ``issubclass`` and a two-level loop.
    - mro: compare the classes using a super class mro set. May be faster when the number of
    classes is large, but ignore the virtual subclasses which do not follow the mro mechanism.
    """
    return _RESOLVE_MINIMAL_CLASSES_REGISTRY[algo](classes)


#
# Class difference.
#


def _class_difference_through_subclass(
    x_iterable: Iterable[Type], y_iterable: Iterable[Type], /
) -> Tuple[Type, ...]:
    """
    Implement ``class_difference`` through ``issubclass`` method.
    """
    # NOTE: should create new tuples of ``y_iterable``, because some iterable
    # items DO NOT support iterating multiple times.
    y_iterable = tuple(y_iterable)

    def _filter_func(x: Type) -> bool:
        for y in y_iterable:
            if issubclass(y, x):
                # If ``y`` is exactly ``x`` or the subclass of ``x``, then ``x``
                # should be discarded.
                return False
        return True

    return tuple(filter(_filter_func, x_iterable))


def _class_difference_through_mro(
    x_iterable: Iterable[Type], y_iterable: Iterable[Type], /
) -> Tuple[Type, ...]:
    """
    Implement ``class_difference`` through ``mro`` method.

    NOTE: This implementation ignores the virtual subclasses (e.g., classes using
    ``ABC.register``), and it will be faster when number of ``__y_iterable`` is large.
    """
    y_mro_set: Set[Type] = set()
    for y in y_iterable:
        y_mro_set.update(inspect.getmro(y))

    return tuple((x for x in x_iterable if x not in y_mro_set))


_CLASS_DIFFERENCE_REGISTRY = {
    "subclass": _class_difference_through_subclass,
    "mro": _class_difference_through_mro,
}


def class_difference(
    x_iterable: Iterable[Type],
    y_iterable: Iterable[Type],
    /,
    *,
    algo: str = "subclass",
) -> Tuple[Type, ...]:
    """
    Given two iterable class items ``__x_iterable`` and ``__y_iterable``, compute
    ``__x_iterable - __y_iterable`` similar to the set difference but consider the inheritance
    relationship and keep the iterable order. In addition, elements won't be deduplicated like
    a set.

    Different ``algo`` values correspond to different implementations:

    - subclass: compare the classes using ``issubclass`` and a two-level loop.
    - mro: compare the classes using a class mro set. May be faster when the number of classes
    is large, but ignore the virtual subclasses which do not follow the mro mechanism.

    Example:
        ```Python
        class A: pass

        class B(A): pass

        class C: pass

        class D(C): pass

        # output: (<class '__main__.D'>, <class '__main__.D'>)
        print(class_difference((A, C, D, D), (B, C)))
        ```
    """
    return _CLASS_DIFFERENCE_REGISTRY[algo](x_iterable, y_iterable)
