from abc import ABC, abstractmethod
from slyme.utils.typing.native import Iterable, FrozenSet
from slyme.utils.abc.metaclass.class_attr import ClassAttrComputeABC


class ComputedClassAttrMixinABC(ABC):
    smx_class_attr_compute: Iterable[ClassAttrComputeABC]
    smx_class_attr_compute_computed: FrozenSet[ClassAttrComputeABC]

    @abstractmethod
    def __init_subclass__(cls, *args, **kwargs):
        return super().__init_subclass__(*args, **kwargs)
