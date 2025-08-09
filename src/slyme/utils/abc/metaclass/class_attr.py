from abc import ABC, abstractmethod
from slyme.utils.abc.descriptor import DescriptorProtocol
from slyme.utils.typing.extension import Missing
from slyme.utils.typing.native import (
    Any,
    Callable,
    FrozenSet,
    Iterable,
    Tuple,
    Type,
    Union,
)


class ClassAttrComputeABC(ABC):
    """Set class attribute computation rules.

    NOTE: Computed class attr can be overridden among different ``ClassAttrCompute``s with the
    same ``computed_name``, so ``computed_name`` should the hashable object for deduplication
    in ``__hash__`` and ``__eq__``.
    """

    # The following attributes should all be readonly for safety.
    name: DescriptorProtocol[str]
    computed_name: DescriptorProtocol[str]
    escaped_types: DescriptorProtocol[Tuple[Type, ...]]
    _compute_func: DescriptorProtocol[Callable[[Any, Tuple[Any]], Any]]

    @property
    @abstractmethod
    def compute_func(self) -> Callable[[Any, Tuple[Any]], Any]:
        """
        Get the compute func. Return ``default_compute_func`` if the compute func
        is not specified.
        """
        pass

    @abstractmethod
    def __hash__(self) -> int:
        """
        ``__hash__`` should be implemented by subclasses.
        """
        return super().__hash__()

    @abstractmethod
    def __eq__(self, __other: Union["ClassAttrComputeABC", Any]) -> bool:
        """
        ``__eq__`` should be implemented by subclasses.
        """
        return super().__eq__(__other)

    @staticmethod
    @abstractmethod
    def default_compute_func(
        attr: Union[Iterable, Missing], computed_base_attrs: Tuple[Iterable]
    ) -> FrozenSet:
        """
        The default compute function used when the compute func is not specified.
        """
        pass
