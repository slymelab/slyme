from slyme.utils.base.scoped import ScopedManager
from slyme.utils.typing.native import TYPE_CHECKING, Any, Generator, TypeVar

if TYPE_CHECKING:
    from . import TempContextABC

_ScopableT = TypeVar("_ScopableT", bound="TempContextABC")


class ContextScopedManagerABC(ScopedManager[_ScopableT, None]):
    pass


class ContextScopedInit(ContextScopedManagerABC["TempContextABC"]):
    """
    Try to call ``initialize__`` when entering or exiting the context.

    ``enter_init`` / ``exit_init``: Whether to call ``initialize__``
    when entering / exiting the context.
    """

    def __init__(self, enter_init: bool = True, exit_init: bool = True) -> None:
        super().__init__()
        self.enter_init = enter_init
        self.exit_init = exit_init

    def scoped_manager_yield(
        self, scoped: "TempContextABC"
    ) -> Generator[None, Any, Any]:
        if self.enter_init:
            # Init at entering.
            scoped.smx_initialize()
        try:
            yield
        finally:
            if self.exit_init:
                # Init at exiting.
                scoped.smx_initialize()
