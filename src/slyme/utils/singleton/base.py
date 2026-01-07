from slyme.utils.typing import Self, ClassVar, Union
from slyme.utils.inspect import compare_method
from .meta import SingletonMeta


class Singleton(metaclass=SingletonMeta):
    """
    Helper class that creates a Singleton class using inheritance.

    Note that it works for each class (even subclasses) independently.

    Example:
    ```python
    from slyme.utils.singleton import Singleton
    class A(Singleton): pass

    # B inherits A
    class B(A): pass

    print(A() is A())  # True
    print(B() is B())  # True
    print(A() is B())  # False
    ```
    """

    _singleton_instance: ClassVar[Union[Self, None]]

    def __new__(cls, /, *args, **kwargs) -> Self:
        if cls._singleton_instance is None:
            with cls._singleton_t_lock:
                if cls._singleton_instance is None:
                    if compare_method(super().__new__, object.__new__):
                        # FIX: object.__new__() takes exactly one argument
                        # (the type to instantiate)
                        instance = super().__new__(cls)
                    else:
                        instance = super().__new__(cls, *args, **kwargs)
                    cls._singleton_instance = instance
        return cls._singleton_instance
