import inspect
from collections.abc import Iterable


# Resolve minimal classes.
def _resolve_minimal_classes_through_subclass(
    classes: Iterable[type], /
) -> tuple[type, ...]:
    """
    Implement ``resolve_minimal_classes`` through ``issubclass`` method.
    """
    # NOTE: should create a new tuple of ``__classes``, because some iterable items DO NOT
    # support iterating multiple times.
    classes = tuple(classes)
    # For class deduplication.
    seen_classes: set[type] = set()

    def _filter_func(cls: type) -> bool:
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


def _resolve_minimal_classes_through_mro(classes: Iterable[type], /) -> tuple[type, ...]:
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
    super_class_set: set[type] = set()
    for cls in classes:
        super_class_set.update(inspect.getmro(cls)[1:])

    def _filter_func(cls: type) -> bool:
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
    classes: Iterable[type], /, *, algo: str = "subclass"
) -> tuple[type, ...]:
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
    x_iterable: Iterable[type], y_iterable: Iterable[type], /
) -> tuple[type, ...]:
    """
    Implement ``class_difference`` through ``issubclass`` method.
    """
    # NOTE: should create new tuples of ``y_iterable``, because some iterable
    # items DO NOT support iterating multiple times.
    y_iterable = tuple(y_iterable)

    def _filter_func(x: type) -> bool:
        for y in y_iterable:
            if issubclass(y, x):
                # If ``y`` is exactly ``x`` or the subclass of ``x``, then ``x``
                # should be discarded.
                return False
        return True

    return tuple(filter(_filter_func, x_iterable))


def _class_difference_through_mro(
    x_iterable: Iterable[type], y_iterable: Iterable[type], /
) -> tuple[type, ...]:
    """
    Implement ``class_difference`` through ``mro`` method.

    NOTE: This implementation ignores the virtual subclasses (e.g., classes using
    ``ABC.register``), and it will be faster when number of ``__y_iterable`` is large.
    """
    y_mro_set: set[type] = set()
    for y in y_iterable:
        y_mro_set.update(inspect.getmro(y))

    return tuple((x for x in x_iterable if x not in y_mro_set))


_CLASS_DIFFERENCE_REGISTRY = {
    "subclass": _class_difference_through_subclass,
    "mro": _class_difference_through_mro,
}


def class_difference(
    x_iterable: Iterable[type],
    y_iterable: Iterable[type],
    /,
    *,
    algo: str = "subclass",
) -> tuple[type, ...]:
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
