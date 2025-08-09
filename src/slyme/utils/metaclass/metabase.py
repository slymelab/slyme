"""
``metabase`` defines helper classes with specified metaclasses, allowing 
users to specify metaclasses in their custom classes through inheritance.
"""

from abc import ABCMeta
from slyme.utils.typing.native import (
    FrozenSet,
    Iterable,
)
from slyme.utils.typing.extension import (
    compare_method,
    SINGLETON_INSTANCE_ATTR_NAME,
    SINGLETON_T_LOCK_ATTR_NAME,
)
from slyme.utils.abc.metaclass.metabase import (
    ComputedClassAttrMetabaseABC,
)
from .singleton import SingletonMetaclass
from .adapter import metaclasses
from .class_attr import ComputedClassAttrMetaclass, ClassAttrCompute


class ComputedClassAttrMetabase(
    ComputedClassAttrMetabaseABC,
    metaclass=metaclasses(ComputedClassAttrMetaclass, ABCMeta),
):
    """Dynamically computes class attributes in an inheritance manner.

    NOTE: This metaclass has Pros and Cons compared to a custom mro method chain:

    Pros:
    1. Does not need to repeat the merging operation in each subclass. For Example:
    ```python
    # MRO method
    class Parent:
        @classmethod
        def get_class_attr(cls) -> Set[str]:
            return {"parent"}

    class Child(Parent):
        @classmethod
        def get_class_attr(cls) -> Set[str]:
            # We have to write the merging operation in each subclass method.
            return {
                *super().get_class_attr(),
                "child",
            }

    # ``ComputedClassAttrMetabase``
    class NewParent(ComputedClassAttrMetabase):
        # Define the merging logic here.
        class_attr_compute__ = (
            ClassAttrCompute(
                "class_attr",
                "class_attr_computed",
                # NOTE: Here the ``compute_func`` can be shared across all the subclasses.
                compute_func=lambda attr, base_attrs: frozenset(e for it in (attr,) + base_attrs for e in it),
            )
        )
        class_attr = ("parent",)

    class NewChild(NewParent):
        # Simple and easy
        class_attr = ("child",)
    ```

    2. The subclass definition is simple and clear (especially when there are multiple class attributes
    to be computed). See the above example.
    3. ``ComputedClassAttrMetabase`` saves cached results to all the classes it creates, which is efficient
    when the attribute is frequently accessed.
    4. Instance attributes can override the class attributes for more flexible behaviors.

    Cons:
    1. It introduces metaclass, which should be avoided as much as possible. NOTE: Use ``ComputedClassAttrMixin``
    instead can solve this issue.
    2. The class attributes are computed at class creation, which means they cannot be dynamically updated. However,
    The class attributes are more like a "template" rather than something that can be changed at runtime, so this
    may not be a serious issue.

    NOTE: Avoid using second-order or higher class attr compute (e.g., ``a``->``a_computed``->``a_computed_computed``).
    ``ComputedClassAttr`` supports override in subclasses, so the order of class attr compute can not be guaranteed,
    which means ``a_computed`` dependent by ``a_computed_computed`` may not have been computed when computing the
    attribute ``a_computed_computed``. Furthermore, according to the current ``ComputeClassAttr`` implementation, each
    ``ClassAttrCompute`` cannot access the results of previous updates since the class namespace is only updated after
    all computations complete. More importantly, ``a_computed_computed`` can be computed using custom compute functions
    simply with ``a`` as input, thereby easily avoiding chained dependency behaviors.
    """

    smx_class_attr_compute: Iterable[ClassAttrCompute] = ()
    smx_class_attr_compute_computed: FrozenSet[ClassAttrCompute]


#
# Singleton base class
#


class SingletonMetabase(metaclass=SingletonMetaclass):
    """
    Helper class that creates a Singleton class using inheritance.

    Note that it works for each class (even subclasses) independently.

    Example:
    ```python
    from slyme.utils.metaclass.metabase import SingletonMetabase
    class A(SingletonMetabase): pass

    # B inherits A
    class B(A): pass

    print(A() is A())  # True
    print(B() is B())  # True
    print(A() is B())  # False

    \"""
    These two values are different, because ``SingletonMetaclass`` sets ``__instance``
    separately for each class it creates.
    \"""
    print(A._SingletonMetaclass__instance)
    print(B._SingletonMetaclass__instance)
    ```
    """

    def __new__(cls, /, *args, **kwargs):
        if getattr(cls, SINGLETON_INSTANCE_ATTR_NAME) is None:
            with getattr(cls, SINGLETON_T_LOCK_ATTR_NAME):
                if getattr(cls, SINGLETON_INSTANCE_ATTR_NAME) is None:
                    if compare_method(super().__new__, object.__new__):
                        # FIX: object.__new__() takes exactly one argument
                        # (the type to instantiate)
                        instance = super().__new__(cls)
                    else:
                        instance = super().__new__(cls, *args, **kwargs)
                    setattr(cls, SINGLETON_INSTANCE_ATTR_NAME, instance)
        return getattr(cls, SINGLETON_INSTANCE_ATTR_NAME)
