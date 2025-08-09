"""
This module defines common types, introduces special constants, provides other 
introspection utilities, etc.
"""

import re
import threading
from types import FunctionType, MethodType
from .native import Any, Literal, Tuple, Union, overload, Iterable, Type, Set, cast

#
# Special constants defined in ``slyme``.
#


class _SingletonMetaclass(type):
    """
    Singleton metaclass that makes a specific class a singleton class.

    Used for special constants. It is defined here rather than in ``slyme.utils.metaclass``, because
    the typing module should be an independent module and can only be imported by other ``slyme`` modules
    (to avoid circular import error). The ``SingletonMetaclass`` in ``slyme.utils.metaclass`` and
    ``Singleton`` in ``slyme.utils.metaclass.metabase`` are just based on this class for more general use.

    NOTE: The ``_SingletonMetaclass`` works for each class (even subclasses) independently, because it sets
    locks and ``__instance`` separately for each class it creates.
    """

    def __init__(cls, /, *args, **kwargs):
        super().__init__(*args, **kwargs)
        cls.__t_lock = threading.RLock()
        cls.__instance = None

    def __call__(cls, /, *args: Any, **kwargs: Any) -> Any:
        if cls.__instance is None:
            with cls.__t_lock:
                if cls.__instance is None:
                    cls.__instance = super().__call__(*args, **kwargs)
        return cls.__instance


SINGLETON_T_LOCK_ATTR_NAME = "_SingletonMetaclass__t_lock"
SINGLETON_INSTANCE_ATTR_NAME = "_SingletonMetaclass__instance"


class _SingletonMetabase(metaclass=_SingletonMetaclass):
    """
    Base class of singleton classes.

    NOTE: This class is for ``slyme.utils.typing.extension`` only.
    """

    __slots__ = ()

    def __new__(cls, /, *args, **kwargs):
        if getattr(cls, SINGLETON_INSTANCE_ATTR_NAME) is None:
            with getattr(cls, SINGLETON_T_LOCK_ATTR_NAME):
                if getattr(cls, SINGLETON_INSTANCE_ATTR_NAME) is None:
                    # NOTE: Directly use ``object.__new__`` here.
                    instance = object.__new__(cls)
                    setattr(cls, SINGLETON_INSTANCE_ATTR_NAME, instance)
        return getattr(cls, SINGLETON_INSTANCE_ATTR_NAME)


# ``Nothing`` class, ``NOTHING`` instance and related functions.


class Nothing(_SingletonMetabase):
    """
    This class defines a ``NOTHING`` constant. Different from ``None`` in Python, ``NOTHING``
    is more exception-friendly, which means no exception will be raised under the following
    situations:

    - Getting attributes or items that ``NOTHING`` does not have (which will return ``NOTHING``).
    - Setting attributes or items to ``NOTHING`` (which will actually do nothing).
    - Calling ``NOTHING`` with arbitrary param settings (which will return ``NOTHING``).
    - Using ``NOTHING`` as a context manager (which will do nothing).
    - Converting ``NOTHING`` to int, float, bool or other types.
    - Applying arithmetic operations to ``NOTHING`` and other values (which will return ``NOTHING``).
    - Other similar situations...

    NOTE: We use ``*args`` and ``**kwargs`` signatures in all methods to maximize compatibility with
    possible future changes.
    """

    __slots__ = ()

    def __init__(self, *args, **kwargs) -> None:
        pass

    # Basic methods.
    def __repr__(self, *args, **kwargs) -> str:
        return f"NOTHING<{str(hex(id(self)))}>"

    def __str__(self, *args, **kwargs) -> Literal["NOTHING"]:
        return "NOTHING"

    def __format__(self, *args, **kwargs) -> Literal["NOTHING"]:
        return "NOTHING"

    def __hash__(self, *args, **kwargs) -> int:
        return id(self)

    def __bool__(self, *args, **kwargs) -> Literal[False]:
        return False

    def __bytes__(self, *args, **kwargs) -> Literal[b""]:
        return b""

    # Comparison operations.
    # NOTE: All the comparison operations without equality will return ``False``, so
    # it is recommended that you manually check whether the object is ``NOTHING``,
    # otherwise this may cause unexpected results.
    # Example:
    # NOTHING > 114514 (False)
    # NOTHING < 1919810 (False)
    # NOTHING <= 114514 (False)
    # NOTHING >= NOTHING (True)
    # NOTHING == NOTHING (True)
    def __eq__(self, __other: Any, *args, **kwargs) -> bool:
        if __other is NOTHING:
            return True
        return False

    def __lt__(self, *args, **kwargs) -> Literal[False]:
        return False

    def __le__(self, __other: Any, *args, **kwargs) -> bool:
        return self == __other

    def __gt__(self, *args, **kwargs) -> Literal[False]:
        return False

    def __ge__(self, __other: Any, *args, **kwargs) -> bool:
        return self == __other

    # Attribute operations.
    def __setattr__(self, *args, **kwargs) -> None:
        pass

    def __getattr__(self, *args, **kwargs) -> "Nothing":
        return self

    def __getattribute__(self, *args, **kwargs) -> "Nothing":
        return self

    def __delattr__(self, *args, **kwargs) -> None:
        pass

    def __dir__(self, *args, **kwargs) -> Tuple[()]:
        return ()

    # Descriptor.
    def __get__(self, *args, **kwargs) -> "Nothing":
        return self

    def __set__(self, *args, **kwargs) -> None:
        pass

    def __delete__(self, *args, **kwargs) -> None:
        pass

    # Calling ``NOTHING`` with arbitrary param settings will return ``NOTHING`` itself.
    def __call__(self, *args, **kwargs) -> "Nothing":
        return self

    # Iterator.
    def __next__(self, *args, **kwargs):
        raise StopIteration

    # Container operations.
    def __len__(self, *args, **kwargs) -> Literal[0]:
        return 0

    def __getitem__(self, *args, **kwargs) -> "Nothing":
        return self

    def __setitem__(self, *args, **kwargs) -> None:
        pass

    def __delitem__(self, *args, **kwargs) -> None:
        pass

    def __iter__(self, *args, **kwargs) -> "Nothing":
        return self

    def __reversed__(self, *args, **kwargs) -> "Nothing":
        return self

    def __contains__(self, *args, **kwargs) -> Literal[False]:
        return False

    # Arithmetic operations.
    def __add__(self, *args, **kwargs) -> "Nothing":
        return self

    def __radd__(self, *args, **kwargs) -> "Nothing":
        return self

    def __iadd__(self, *args, **kwargs) -> "Nothing":
        return self

    def __sub__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rsub__(self, *args, **kwargs) -> "Nothing":
        return self

    def __isub__(self, *args, **kwargs) -> "Nothing":
        return self

    def __mul__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rmul__(self, *args, **kwargs) -> "Nothing":
        return self

    def __imul__(self, *args, **kwargs) -> "Nothing":
        return self

    def __matmul__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rmatmul__(self, *args, **kwargs) -> "Nothing":
        return self

    def __imatmul__(self, *args, **kwargs) -> "Nothing":
        return self

    def __truediv__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rtruediv__(self, *args, **kwargs) -> "Nothing":
        return self

    def __itruediv__(self, *args, **kwargs) -> "Nothing":
        return self

    def __floordiv__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rfloordiv__(self, *args, **kwargs) -> "Nothing":
        return self

    def __ifloordiv__(self, *args, **kwargs) -> "Nothing":
        return self

    def __mod__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rmod__(self, *args, **kwargs) -> "Nothing":
        return self

    def __imod__(self, *args, **kwargs) -> "Nothing":
        return self

    def __divmod__(self, *args, **kwargs) -> Tuple["Nothing", "Nothing"]:
        return self, self

    def __rdivmod__(self, *args, **kwargs) -> Tuple["Nothing", "Nothing"]:
        return self, self

    def __pow__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rpow__(self, *args, **kwargs) -> "Nothing":
        return self

    def __ipow__(self, *args, **kwargs) -> "Nothing":
        return self

    def __lshift__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rlshift__(self, *args, **kwargs) -> "Nothing":
        return self

    def __ilshift__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rshift__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rrshift__(self, *args, **kwargs) -> "Nothing":
        return self

    def __irshift__(self, *args, **kwargs) -> "Nothing":
        return self

    def __and__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rand__(self, *args, **kwargs) -> "Nothing":
        return self

    def __iand__(self, *args, **kwargs) -> "Nothing":
        return self

    def __xor__(self, *args, **kwargs) -> "Nothing":
        return self

    def __rxor__(self, *args, **kwargs) -> "Nothing":
        return self

    def __ixor__(self, *args, **kwargs) -> "Nothing":
        return self

    def __or__(self, *args, **kwargs) -> "Nothing":
        return self

    def __ror__(self, *args, **kwargs) -> "Nothing":
        return self

    def __ior__(self, *args, **kwargs) -> "Nothing":
        return self

    def __neg__(self, *args, **kwargs) -> "Nothing":
        return self

    def __pos__(self, *args, **kwargs) -> "Nothing":
        return self

    def __abs__(self, *args, **kwargs) -> "Nothing":
        return self

    def __invert__(self, *args, **kwargs) -> "Nothing":
        return self

    def __complex__(self, *args, **kwargs) -> complex:
        return 0j

    def __int__(self, *args, **kwargs) -> Literal[0]:
        return 0

    def __float__(self, *args, **kwargs) -> float:
        return 0.0

    def __index__(self, *args, **kwargs) -> Literal[0]:
        return 0

    def __round__(self, *args, **kwargs) -> Literal[0]:
        return 0

    def __trunc__(self, *args, **kwargs) -> Literal[0]:
        return 0

    def __floor__(self, *args, **kwargs) -> Literal[0]:
        return 0

    def __ceil__(self, *args, **kwargs) -> Literal[0]:
        return 0

    # Context manager.
    def __enter__(self, *args, **kwargs) -> "Nothing":
        return self

    def __exit__(self, *args, **kwargs) -> Literal[False]:
        return False

    # Asynchronous operations.
    def __await__(self, *args, **kwargs) -> "Nothing":
        return self

    def __aiter__(self, *args, **kwargs) -> "Nothing":
        return self

    async def __anext__(self, *args, **kwargs):
        raise StopAsyncIteration

    # Async context manager.
    async def __aenter__(self, *args, **kwargs) -> "Nothing":
        return self

    async def __aexit__(self, *args, **kwargs) -> Literal[False]:
        return False


NOTHING = Nothing()

#
# Flag constants.
#


class _FlagConstant(_SingletonMetabase):
    __slots__ = ()

    def __str__(self) -> str:
        return resolve_instance_classname(self).upper()

    def __repr__(self) -> str:
        return f"{str(self)}<{str(hex(id(self)))}>"


# ``Pass`` singleton constant
class Pass(_FlagConstant):
    __slots__ = ()

    def __contains__(self, *args, **kwargs) -> Literal[True]:
        """
        NOTE: ``PASS`` is seen to contain anything.
        """
        return True


PASS = Pass()


# ``Missing`` singleton constant
class Missing(_FlagConstant):
    __slots__ = ()

    def __bool__(self) -> bool:
        return False


MISSING = Missing()


# ``Stop`` singleton constant
class Stop(_FlagConstant):
    __slots__ = ()

    def __bool__(self) -> bool:
        return False


STOP = Stop()

#
# Null types.
#

NoneOrNothing = Union[None, Nothing]
EmptyFlag = Union[NoneOrNothing, Missing]
SlymeConstant = Union[_FlagConstant, Nothing]


def is_none_or_nothing(__obj: Any) -> bool:
    """
    Check whether an object is ``None``, ``NOTHING`` or neither.
    """
    return __obj is None or __obj is NOTHING


def is_empty_flag(__obj: Any) -> bool:
    """
    Check whether an object is an ``EmptyFlag`` (i.e., ``None``,
    ``NOTHING`` or ``MISSING``).
    """
    return __obj is None or __obj is NOTHING or __obj is MISSING


def is_slyme_constant(__obj: Any) -> bool:
    """
    Check whether an object is a slyme constant (i.e., ``NOTHING``
    or a ``_FlagConstant`` object).
    """
    return __obj is NOTHING or isinstance(__obj, _FlagConstant)


#
# Other types, type check and naming check.
#

FuncOrMethodType = Union[FunctionType, MethodType]
RawFuncType = FunctionType
MAGIC_PATTERN = re.compile("^_{2}[^_](?:.*[^_])?_{2}$")
SLYME_PATTERN = re.compile("^smx_[a-zA-Z_][a-zA-Z0-9_]*$")


def is_function_or_method(__item: Any) -> bool:
    return isinstance(__item, (MethodType, FunctionType))


def is_magic_naming(__name: str) -> bool:
    return MAGIC_PATTERN.match(str(__name)) is not None


def is_slyme_naming(__name: str) -> bool:
    return SLYME_PATTERN.match(str(__name)) is not None


#
# Introspection utilities.
#


@overload
def unwrap_method(__func: FuncOrMethodType) -> RawFuncType:
    pass


@overload
def unwrap_method(__func: NoneOrNothing) -> NoneOrNothing:
    pass


def unwrap_method(
    __func: Union[FuncOrMethodType, NoneOrNothing]
) -> Union[RawFuncType, NoneOrNothing]:
    """
    Get the original static function if the given ``func`` is a method.
    """
    while isinstance(__func, MethodType):
        # get the original function body of the method
        __func = __func.__func__
    return __func


def compare_method(__func1: FuncOrMethodType, __func2: FuncOrMethodType) -> bool:
    """
    Compare whether the two methods have the same static function reference.

    Example:
        ```Python
        class A:
            def method(self):
                pass

        a1 = A()
        a2 = A()
        # False
        print(a1.method is a1.method)
        # False
        print(a1.method is a2.method)
        # True
        print(compare_method(a1.method, a1.method))
        # True
        print(compare_method(a1.method, a2.method))
        ```
    """
    return unwrap_method(__func1) is unwrap_method(__func2)


def resolve_name(__named: Any) -> str:
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
    name: Union[str, Missing] = getattr(__named, "__name__", MISSING)
    if name:
        return cast(str, name)
    name: Union[str, Missing] = getattr(__named, "__qualname__", MISSING)
    if name:
        return cast(str, name)
    name = str(__named)
    if name:
        return name
    name = repr(__named)
    return name


def resolve_instance_classname(__obj: Any) -> str:
    """
    Try to resolve the classname of the given instance object.
    """
    # NOTE: Use ``type`` rather than ``__obj.__class__``, because the former is more valid,
    # especially when the ``__getattribute__`` method is overridden by ``__obj`` (e.g.,
    # ``NOTHING.__class__`` will return ``NOTHING`` itself rather than the ``Nothing`` class).
    return resolve_name(type(__obj))


def resolve_private_attr_name(__cls: Type, __name: str) -> str:
    """
    Resolve the private attribute name based on the given class and the original name. Can
    be robust to the renaming refactor of the class (because the attr name is computed
    dynamically rather than a fixed str).

    NOTE: This applies only to classes defined using class definition syntax, and the
    ``__name__`` attribute of the class should not be manually changed.

    Example:
        ```Python
        class A:
            def __init__(self):
                self.__a = 1

        # _A__a
        print(resolve_private_attr_name(A, '__a'))

        a_obj = A()
        # 1
        print(getattr(a_obj, resolve_private_attr_name(A, '__a')))
        ```
    """
    # NOTE: We do not use ``resolve_name`` to get the classname, because the private name
    # is strictly based on the ``__name__`` of the class.
    # According to 'Python Language Reference': The transformation inserts the class name,
    # with leading underscores removed and a single underscore inserted, in front of the
    # name.
    return f'_{__cls.__name__.lstrip("_")}{__name}'


def resolve_mro(__cls: Type) -> Tuple[Type, ...]:
    """
    Safely resolve the mro of any given class. NOTE: If the class has the
    attribute ``__mro__``, then directly return it. Otherwise, call the
    corresponding ``mro()`` method to the the mro.
    """
    # If ``cls`` has ``__mro__``, then directly return.
    if hasattr(__cls, "__mro__"):
        return __cls.__mro__

    try:
        # NOTE: Some class (e.g., typing.Sequence) doesn't support subclass
        # check, and ``issubclass`` will raise an exception.
        is_type_subclass = issubclass(__cls, type)
    except Exception:
        is_type_subclass = False

    if is_type_subclass:
        # NOTE: If the given class is a metaclass, then the corresponding
        # ``mro`` method to be called should be in the 'metaclass of the
        # given metaclass' (i.e., type(cls)).
        return tuple(type(__cls).mro(__cls))
    else:
        # Normal classes simply call the ``mro`` method.
        return tuple(__cls.mro())


def resolve_bases(__cls: Type) -> Tuple[Type, ...]:
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
    if hasattr(__cls, "__bases__"):
        return __cls.__bases__

    # Get the mro of ``cls`` (excluding itself).
    mro_classes = list((_cls for _cls in resolve_mro(__cls) if _cls is not __cls))
    bases = []
    while len(mro_classes) > 0:
        # NOTE: should pop the first element in the list (index=0).
        base = mro_classes.pop(0)
        bases.append(base)
        # Get the mro of ``base`` and remove them from ``mro_classes``.
        base_mro_set = set(resolve_mro(base))
        mro_classes = list((_cls for _cls in mro_classes if _cls not in base_mro_set))
    return tuple(bases)


#
# Resolve minimal classes.
#


def _resolve_minimal_classes_through_subclass(
    __classes: Iterable[Type],
) -> Tuple[Type, ...]:
    """
    Implement ``resolve_minimal_classes`` through ``issubclass`` method.
    """
    # NOTE: should create a new tuple of ``__classes``, because some iterable items DO NOT
    # support iterating multiple times.
    __classes = tuple(__classes)
    # For class deduplication.
    seen_classes: Set[Type] = set()

    def _filter_func(cls: Type) -> bool:
        if cls in seen_classes:
            return False

        for other_cls in __classes:
            if issubclass(other_cls, cls) and cls is not other_cls:
                # Not the 'minimal class'.
                return False
        # For deduplication.
        seen_classes.add(cls)
        return True

    return tuple(filter(_filter_func, __classes))


def _resolve_minimal_classes_through_mro(__classes: Iterable[Type]) -> Tuple[Type, ...]:
    """
    Implement ``resolve_minimal_classes`` through ``mro`` method.

    NOTE: This implementation ignores the virtual subclasses (e.g., classes using
    ``ABC.register``), and it will be faster when number of ``__classes`` is large.
    """
    # NOTE: should create a new tuple of ``__classes``, because some iterable items DO NOT
    # support iterating multiple times.
    __classes = tuple(__classes)
    # Build a super class set that contains all the super classes of ``__classes`` (excluding
    # themselves).
    super_class_set: Set[Type] = set()
    for cls in __classes:
        super_class_set.update(resolve_mro(cls)[1:])

    def _filter_func(cls: Type) -> bool:
        result = cls not in super_class_set
        # NOTE: Add ``cls`` into ``super_class_set``
        # to deduplicate the following classes.
        super_class_set.add(cls)
        return result

    return tuple(filter(_filter_func, __classes))


_RESOLVE_MINIMAL_CLASSES_REGISTRY = {
    "subclass": _resolve_minimal_classes_through_subclass,
    "mro": _resolve_minimal_classes_through_mro,
}


def resolve_minimal_classes(
    __classes: Iterable[Type], *, algo: str = "subclass"
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
    return _RESOLVE_MINIMAL_CLASSES_REGISTRY[algo](__classes)


#
# Class difference.
#


def _class_difference_through_subclass(
    __x_iterable: Iterable[Type], __y_iterable: Iterable[Type]
) -> Tuple[Type, ...]:
    """
    Implement ``class_difference`` through ``issubclass`` method.
    """
    # NOTE: should create new tuples of ``__y_iterable``, because some iterable
    # items DO NOT support iterating multiple times.
    __y_iterable = tuple(__y_iterable)

    def _filter_func(x: Type) -> bool:
        for y in __y_iterable:
            if issubclass(y, x):
                # If ``y`` is exactly ``x`` or the subclass of ``x``, then ``x``
                # should be discarded.
                return False
        return True

    return tuple(filter(_filter_func, __x_iterable))


def _class_difference_through_mro(
    __x_iterable: Iterable[Type], __y_iterable: Iterable[Type]
) -> Tuple[Type, ...]:
    """
    Implement ``class_difference`` through ``mro`` method.

    NOTE: This implementation ignores the virtual subclasses (e.g., classes using
    ``ABC.register``), and it will be faster when number of ``__y_iterable`` is large.
    """
    y_mro_set: Set[Type] = set()
    for y in __y_iterable:
        y_mro_set.update(resolve_mro(y))

    return tuple((x for x in __x_iterable if x not in y_mro_set))


_CLASS_DIFFERENCE_REGISTRY = {
    "subclass": _class_difference_through_subclass,
    "mro": _class_difference_through_mro,
}


def class_difference(
    __x_iterable: Iterable[Type],
    __y_iterable: Iterable[Type],
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
    return _CLASS_DIFFERENCE_REGISTRY[algo](__x_iterable, __y_iterable)
