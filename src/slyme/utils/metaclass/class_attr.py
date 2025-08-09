from slyme.utils.abc.metaclass.class_attr import ClassAttrComputeABC
from slyme.utils.exception import APIMisused
from slyme.utils.typing.extension import MISSING, Missing
from slyme.utils.typing.native import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Iterable,
    Tuple,
    Type,
    Union,
)
from slyme.utils.descriptor import ReadonlyDescriptor

CLASS_ATTR_COMPUTE_ATTR_NAME = "smx_class_attr_compute"
CLASS_ATTR_COMPUTE_COMPUTED_ATTR_NAME = "smx_class_attr_compute_computed"


#
# Computed class attributes.
#


class ClassAttrCompute(ClassAttrComputeABC):
    """Set class attribute computation rules."""

    name = ReadonlyDescriptor()
    computed_name = ReadonlyDescriptor()
    escaped_types = ReadonlyDescriptor()
    _compute_func = ReadonlyDescriptor()

    def __init__(
        self,
        name: str,
        computed_name: str,
        escaped_types: Iterable[Type] = (type("never_type", (object,), {}),),
        compute_func: Union[Callable[[Any, Tuple[Any]], Any], Missing] = MISSING,
    ) -> None:
        """
        NOTE: We set the default value of ``escaped_types`` to a tuple that contains a
        newly created type ``never_type``. Although through tests, we found that an empty
        type tuple can be accepted by ``isinstance``, but we have not found this feature
        explicitly explained in the official Python document. So for compatibility, the
        ``never_type`` tuple is used as the default value.
        """
        # NOTE: The following attributes should NOT be changed after init.
        self.name = name
        self.computed_name = computed_name
        # Set escaped types.
        self.escaped_types = tuple(escaped_types)
        if Missing in self.escaped_types:
            raise APIMisused(
                "``Missing`` type in ``slyme.utils.typing.extension`` is not allowed "
                "in the escaped types."
            )
        self._compute_func = compute_func
        # NOTE: Computed class attr can be overridden among different ``ClassAttrCompute``s
        # with the same ``computed_name``, so we choose it as the hashable object for
        # deduplication.
        self.hashable = computed_name
        self.hash_value = hash(self.hashable)

    @property
    def compute_func(self) -> Callable[[Any, Tuple[Any]], Any]:
        return (
            self.default_compute_func
            if self._compute_func is MISSING
            else self._compute_func
        )

    def __hash__(self) -> int:
        return self.hash_value

    def __eq__(self, other: Union["ClassAttrCompute", Any], /) -> bool:
        return isinstance(other, ClassAttrCompute) and self.hashable == other.hashable

    @staticmethod
    def default_compute_func(
        attr: Union[Iterable, Missing], computed_base_attrs: Tuple[Iterable]
    ) -> FrozenSet:
        """The default compute function"""
        attr = set(attr) if attr is not MISSING else set()
        attr.update(*computed_base_attrs)
        return frozenset(attr)


class ComputedClassAttrMetaclass(type):
    def __new__(
        meta_cls,
        name: str,
        bases: Tuple[Type, ...],
        namespace: Dict[str, Any],
        /,
        **kwargs: Any,
    ):
        updates = ComputeClassAttr.compute(name, bases, namespace)
        namespace.update(updates)
        return super().__new__(meta_cls, name, bases, namespace, **kwargs)


class ComputeClassAttr:
    """Do compute operations."""

    @classmethod
    def compute(
        cls, name: str, bases: Tuple[Type, ...], namespace: Dict[str, Any]
    ) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        # NOTE: Compute ``smx_class_attr_compute`` first.
        if CLASS_ATTR_COMPUTE_ATTR_NAME not in namespace:
            updates[CLASS_ATTR_COMPUTE_ATTR_NAME] = MISSING
            computes = set()
        else:
            computes = set(namespace[CLASS_ATTR_COMPUTE_ATTR_NAME])
        # NOTE: The items with the same hash keys will not be updated, so the items in
        # the subclass will override those in the base classes.
        computes.update(
            *(
                computed_base_attr
                for computed_base_attr in (
                    getattr(base, CLASS_ATTR_COMPUTE_COMPUTED_ATTR_NAME, MISSING)
                    for base in bases
                )
                if computed_base_attr is not MISSING
            )
        )
        computes = frozenset(computes)
        updates[CLASS_ATTR_COMPUTE_COMPUTED_ATTR_NAME] = computes
        # Compute class attributes.
        for compute in computes:
            updates.update(cls.compute_class_attr(compute, name, bases, namespace))
        return updates

    @classmethod
    def compute_class_attr(
        cls,
        compute: "ClassAttrCompute",
        name: str,
        bases: Tuple[Type, ...],
        namespace: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Compute class attribute.
        """
        updates: Dict[str, Any] = {}
        if compute.name not in namespace:
            updates[compute.name] = MISSING
            attr = MISSING
        else:
            attr = namespace[compute.name]

        # Check escaped types.
        # NOTE: ``MISSING`` will never be escaped.
        if attr is not MISSING and isinstance(attr, compute.escaped_types):
            updates[compute.computed_name] = attr
            return updates
        # Check computed base attributes.
        # Remove non-existing and ``MISSING`` attributes.
        computed_base_attrs = tuple(
            (
                computed_base_attr
                for computed_base_attr in (
                    getattr(base, compute.computed_name, MISSING) for base in bases
                )
                if computed_base_attr is not MISSING
            )
        )
        # Remove escaped types.
        computed_base_attrs_non_escaped = tuple(
            (
                computed_base_attr
                for computed_base_attr in computed_base_attrs
                if not isinstance(computed_base_attr, compute.escaped_types)
            )
        )
        # If ``attr`` is ``MISSING``, try to inherit from bases.
        if attr is MISSING:
            if len(computed_base_attrs) == 0:
                # No bases can be inherited.
                raise APIMisused(
                    f"The attribute ``{compute.name}`` in ``{name}`` and the computed attributes "
                    f"``{compute.computed_name}`` in all the base classes are empty. Can not "
                    "automatically resolve the inheritance relationship. You should manually "
                    f"set ``{compute.name}`` for initialization."
                )
            if len(computed_base_attrs_non_escaped) == 0:
                # All the computed base attributes are of escaped types.
                # Select the first non-empty base class for inheritance.
                updates[compute.computed_name] = computed_base_attrs[0]
                return updates
            if len(computed_base_attrs_non_escaped) != len(computed_base_attrs):
                # There are both computed types and escaped types in the base classes.
                raise APIMisused(
                    f"The ``{compute.name}`` in ``{name}`` is empty, and there are both computed "
                    "types and escaped types in the base classes. Can not automatically resolve "
                    f"inheritance relationship. You should manually set ``{compute.name}`` to get "
                    "deterministic behavior."
                )
        # Compute class attrs.
        updates[compute.computed_name] = compute.compute_func(
            attr, computed_base_attrs_non_escaped
        )
        return updates
