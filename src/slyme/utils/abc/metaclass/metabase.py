from abc import ABC
from slyme.utils.typing.native import Iterable, FrozenSet
from .class_attr import ClassAttrComputeABC


class ComputedClassAttrMetabaseABC(ABC):
    smx_class_attr_compute: Iterable[ClassAttrComputeABC]
    smx_class_attr_compute_computed: FrozenSet[ClassAttrComputeABC]
