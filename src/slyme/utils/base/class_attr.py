from slyme.utils.typing.native import Iterable, FrozenSet
from slyme.utils.typing.extension import resolve_name, resolve_bases
from slyme.utils.abc.base.class_attr import ComputedClassAttrMixinABC
from slyme.utils.metaclass.class_attr import ComputeClassAttr, ClassAttrCompute
from slyme.utils.base import BaseObjectInitSubclass


class ComputedClassAttrMixin(ComputedClassAttrMixinABC, BaseObjectInitSubclass):
    """Dynamically computes class attributes in an inheritance manner.

    A more compatible class implemented using ``__init_subclass__`` rather than metaclass.

    NOTE: See ``ComputedClassAttrMetabase`` for more details.
    """

    # NOTE: The two properties should both set here (unlike ``ComputedClassAttrMetabase``),
    # because ``__init_subclass__`` only works for the subclasses, not for the class itself.
    smx_class_attr_compute: Iterable[ClassAttrCompute] = ()
    smx_class_attr_compute_computed: FrozenSet[ClassAttrCompute] = frozenset()

    def __init_subclass__(cls, *args, **kwargs):
        super().__init_subclass__(*args, **kwargs)
        updates = ComputeClassAttr.compute(
            resolve_name(cls), resolve_bases(cls), cls.__dict__
        )
        for key, value in updates.items():
            setattr(cls, key, value)
