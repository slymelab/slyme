"""This module defines special constants in ``slyme``."""

from threading import RLock
from enum import Enum
from .typing import Any, Literal, Tuple, Union, Self


# Flag constants.
class FlagConstant(Enum):
    MISSING = object()
    STOP = object()


class _SingletonMeta(type):
    """Singleton metaclass that makes a specific class a singleton class.

    NOTE: The `_SingletonMetaclass` works for each class (even subclasses) independently, because it sets
    locks and `instance` separately for each class it creates.
    """

    _singleton_t_lock: RLock
    _singleton_instance: Union[None, Any]

    def __init__(cls, /, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type.__setattr__(cls, "_singleton_t_lock", RLock())
        type.__setattr__(cls, "_singleton_instance", None)

    def __call__(cls, /, *args: Any, **kwargs: Any) -> Any:
        if cls._singleton_instance is None:
            with cls._singleton_t_lock:
                if cls._singleton_instance is None:
                    type.__setattr__(
                        cls, "_singleton_instance", super().__call__(*args, **kwargs)
                    )
        return cls._singleton_instance


class _ConstantMeta(_SingletonMeta):
    """Freeze class attribute modification."""

    def __setattr__(cls, name, value):
        raise TypeError(f"Class ``{cls.__name__}`` is frozen and cannot be modified.")

    def __delattr__(cls, name):
        raise TypeError(f"Class ``{cls.__name__}`` is frozen and cannot be modified.")


class _Constant(metaclass=_ConstantMeta):
    """Base class of singleton classes.

    NOTE: This class is for ``slyme.utils.constant`` only.
    """

    __slots__ = ()

    def __new__(cls, /, *args, **kwargs):
        if cls._singleton_instance is None:
            with cls._singleton_t_lock:
                if cls._singleton_instance is None:
                    # NOTE: Directly use ``object.__new__`` here.
                    instance = object.__new__(cls)
                    type.__setattr__(cls, "_singleton_instance", instance)
        return cls._singleton_instance


# ``Nothing`` class and ``NOTHING`` instance.
class Nothing(_Constant):
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

    def __getattr__(self, *args, **kwargs) -> Self:
        return self

    def __getattribute__(self, *args, **kwargs) -> Self:
        return self

    def __delattr__(self, *args, **kwargs) -> None:
        pass

    def __dir__(self, *args, **kwargs) -> Tuple[()]:
        return ()

    # Descriptor.
    def __get__(self, *args, **kwargs) -> Self:
        return self

    def __set__(self, *args, **kwargs) -> None:
        pass

    def __delete__(self, *args, **kwargs) -> None:
        pass

    # Calling ``NOTHING`` with arbitrary param settings will return ``NOTHING`` itself.
    def __call__(self, *args, **kwargs) -> Self:
        return self

    # Iterator.
    def __next__(self, *args, **kwargs):
        raise StopIteration

    # Container operations.
    def __len__(self, *args, **kwargs) -> Literal[0]:
        return 0

    def __getitem__(self, *args, **kwargs) -> Self:
        return self

    def __setitem__(self, *args, **kwargs) -> None:
        pass

    def __delitem__(self, *args, **kwargs) -> None:
        pass

    def __iter__(self, *args, **kwargs) -> Self:
        return self

    def __reversed__(self, *args, **kwargs) -> Self:
        return self

    def __contains__(self, *args, **kwargs) -> Literal[False]:
        return False

    # Arithmetic operations.
    def __add__(self, *args, **kwargs) -> Self:
        return self

    def __radd__(self, *args, **kwargs) -> Self:
        return self

    def __iadd__(self, *args, **kwargs) -> Self:
        return self

    def __sub__(self, *args, **kwargs) -> Self:
        return self

    def __rsub__(self, *args, **kwargs) -> Self:
        return self

    def __isub__(self, *args, **kwargs) -> Self:
        return self

    def __mul__(self, *args, **kwargs) -> Self:
        return self

    def __rmul__(self, *args, **kwargs) -> Self:
        return self

    def __imul__(self, *args, **kwargs) -> Self:
        return self

    def __matmul__(self, *args, **kwargs) -> Self:
        return self

    def __rmatmul__(self, *args, **kwargs) -> Self:
        return self

    def __imatmul__(self, *args, **kwargs) -> Self:
        return self

    def __truediv__(self, *args, **kwargs) -> Self:
        return self

    def __rtruediv__(self, *args, **kwargs) -> Self:
        return self

    def __itruediv__(self, *args, **kwargs) -> Self:
        return self

    def __floordiv__(self, *args, **kwargs) -> Self:
        return self

    def __rfloordiv__(self, *args, **kwargs) -> Self:
        return self

    def __ifloordiv__(self, *args, **kwargs) -> Self:
        return self

    def __mod__(self, *args, **kwargs) -> Self:
        return self

    def __rmod__(self, *args, **kwargs) -> Self:
        return self

    def __imod__(self, *args, **kwargs) -> Self:
        return self

    def __divmod__(self, *args, **kwargs) -> Tuple[Self, Self]:
        return self, self

    def __rdivmod__(self, *args, **kwargs) -> Tuple[Self, Self]:
        return self, self

    def __pow__(self, *args, **kwargs) -> Self:
        return self

    def __rpow__(self, *args, **kwargs) -> Self:
        return self

    def __ipow__(self, *args, **kwargs) -> Self:
        return self

    def __lshift__(self, *args, **kwargs) -> Self:
        return self

    def __rlshift__(self, *args, **kwargs) -> Self:
        return self

    def __ilshift__(self, *args, **kwargs) -> Self:
        return self

    def __rshift__(self, *args, **kwargs) -> Self:
        return self

    def __rrshift__(self, *args, **kwargs) -> Self:
        return self

    def __irshift__(self, *args, **kwargs) -> Self:
        return self

    def __and__(self, *args, **kwargs) -> Self:
        return self

    def __rand__(self, *args, **kwargs) -> Self:
        return self

    def __iand__(self, *args, **kwargs) -> Self:
        return self

    def __xor__(self, *args, **kwargs) -> Self:
        return self

    def __rxor__(self, *args, **kwargs) -> Self:
        return self

    def __ixor__(self, *args, **kwargs) -> Self:
        return self

    def __or__(self, *args, **kwargs) -> Self:
        return self

    def __ror__(self, *args, **kwargs) -> Self:
        return self

    def __ior__(self, *args, **kwargs) -> Self:
        return self

    def __neg__(self, *args, **kwargs) -> Self:
        return self

    def __pos__(self, *args, **kwargs) -> Self:
        return self

    def __abs__(self, *args, **kwargs) -> Self:
        return self

    def __invert__(self, *args, **kwargs) -> Self:
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
    def __enter__(self, *args, **kwargs) -> Self:
        return self

    def __exit__(self, *args, **kwargs) -> Literal[False]:
        return False

    # Asynchronous operations.
    def __await__(self, *args, **kwargs) -> Self:
        return self

    def __aiter__(self, *args, **kwargs) -> Self:
        return self

    async def __anext__(self, *args, **kwargs):
        raise StopAsyncIteration

    # Async context manager.
    async def __aenter__(self, *args, **kwargs) -> Self:
        return self

    async def __aexit__(self, *args, **kwargs) -> Literal[False]:
        return False


NOTHING = Nothing()
