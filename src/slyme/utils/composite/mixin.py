from abc import ABC, abstractmethod
from collections import deque
from slyme.utils.typing import (
    Any,
    Generic,
    TypeVar,
    Self,
    Union,
    Iterator,
    Iterable,
    Generator,
    Protocol,
    Callable,
)

_ThisT_co = TypeVar("_ThisT_co", covariant=True)
_ChildrenT_co = TypeVar("_ChildrenT_co", covariant=True)
_CompositeMixinT = TypeVar("_CompositeMixinT", bound="CompositeMixin")


class CompositeStructure(Generic[_ThisT_co, _ChildrenT_co]):
    """Data class that contains composite structure."""

    __slots__ = ("this", "children")

    def __init__(self, this: _ThisT_co, children: _ChildrenT_co, /) -> None:
        self.this = this
        self.children = children


class CompositeMixin(ABC, Generic[_CompositeMixinT]):
    __slots__ = ()

    @abstractmethod
    def composite_structure(
        self,
    ) -> CompositeStructure[Union[Self, None], Union[Iterable[_CompositeMixinT], None]]:
        pass


# Composite iter functions.
def _composite_iter_depth_first(
    item: _CompositeMixinT, /
) -> Generator[_CompositeMixinT, Any, None]:
    """Iterate composite in depth-first manner."""
    stack: deque[Iterator[_CompositeMixinT]] = deque([iter([item])])

    while len(stack) > 0:
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        structure = node.composite_structure()
        if structure.this is not None:
            yield structure.this
        if structure.children is not None:
            stack.append(iter(structure.children))


def _composite_iter_breadth_first(
    item: _CompositeMixinT, /
) -> Generator[_CompositeMixinT, Any, None]:
    """Iterate composite in breadth-first manner."""
    queue: deque[Iterator[_CompositeMixinT]] = deque([iter([item])])

    while len(queue) > 0:
        try:
            node = next(queue[0])
        except StopIteration:
            queue.popleft()
            continue
        structure = node.composite_structure()
        if structure.this is not None:
            yield structure.this
        if structure.children is not None:
            queue.append(iter(structure.children))


class _CompositeIterFunc(Protocol):
    def __call__(
        self, item: _CompositeMixinT
    ) -> Generator[_CompositeMixinT, Any, None]: ...


_COMPOSITE_ITER_REGISTRY: dict[str, _CompositeIterFunc] = {
    "depth": _composite_iter_depth_first,
    "breadth": _composite_iter_breadth_first,
}


def composite_iter(
    item: _CompositeMixinT, /, *, strategy: str = "depth"
) -> Generator[_CompositeMixinT, Any, None]:
    yield from _COMPOSITE_ITER_REGISTRY[strategy](item)


def composite_filter(
    func: Callable[[_CompositeMixinT], bool],
    item: _CompositeMixinT,
    /,
    *,
    strategy: str = "depth",
) -> Generator[_CompositeMixinT, Any, None]:
    for it in composite_iter(item, strategy=strategy):
        if func(it):
            yield it
