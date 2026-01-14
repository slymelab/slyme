from collections.abc import Iterable


def resolve_minimal_classes(
    classes: Iterable[type], /
) -> tuple[type, ...]:
    """
    Resolve the 'minimal classes' of the given class iterable. 'minimal classes' denotes that
    any of the classes which do not have a subclass is contained in the tuple, otherwise not.
    The original order of 'minimal classes' is kept. If multiple same classes exist in the
    given class iterable, the first occurrence is kept.
    """
    # NOTE: should create a new tuple of ``classes``, because some iterable items DO NOT
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


def class_difference(
    x_iterable: Iterable[type],
    y_iterable: Iterable[type],
    /,
) -> tuple[type, ...]:
    """
    Given two iterable class items ``__x_iterable`` and ``__y_iterable``, compute
    ``__x_iterable - __y_iterable`` similar to the set difference but consider the inheritance
    relationship and keep the iterable order. In addition, elements won't be deduplicated like
    a set.

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
