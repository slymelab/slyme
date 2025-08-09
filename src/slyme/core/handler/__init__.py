from abc import ABC, abstractmethod
from slyme.utils.abc.base.composite import (
    ComponentABC,
    ComponentContainerABC,
)
from slyme.utils.common import Count
from slyme.utils.decorator import not_implemented
from slyme.utils.typing.native import (
    Iterable,
    Callable,
    TypeVar,
    Generic,
    Union,
    Any,
    Tuple,
    Dict,
    List,
    Type,
)
from slyme.utils.typing.extension import EmptyFlag, Nothing

_ContextT = TypeVar("_ContextT")
_HandlerT = TypeVar("_HandlerT")
_HandlerContainerT = TypeVar("_HandlerContainerT")
_HandlerWrapperT = TypeVar("_HandlerWrapperT")
_HandlerWrapperContainerT = TypeVar("_HandlerWrapperContainerT")


class HandlerABC(
    ComponentABC[_HandlerT, _HandlerContainerT],
    ABC,
    Generic[
        _HandlerT,
        _HandlerContainerT,
        _HandlerWrapperT,
        _HandlerWrapperContainerT,
        _ContextT,
    ],
):
    """
    ABC for all handlers.

    Generics:

    ```Python
    HandlerABC[
        _HandlerT: HandlerABC,
        _HandlerContainerT: HandlerContainerABC,
        _HandlerWrapperT: HandlerWrapperABC,
        _HandlerWrapperContainerT: HandlerWrapperContainerABC,
        _ContextT
    ]
    ```
    """

    # For generating unique handler ids.
    gen_handler_id__: Count

    #
    # Core APIs.
    #

    @abstractmethod
    def handle(self, ctx: _ContextT) -> None:
        """
        Custom handling operations.
        """
        pass

    @abstractmethod
    def __call__(self, ctx: _ContextT) -> None:
        """
        Wrapper method for calling ``handle``. Implements enhanced operations (e.g.,
        exception handling, etc.)
        """
        pass

    #
    # Handler search operations.
    #

    @not_implemented
    def get_by_id(self, __id: str) -> _HandlerT:
        """
        Get the handler by handler id.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @not_implemented
    def get_by_class(self, __class: Union[Type, Tuple[Type, ...]]) -> List[_HandlerT]:
        """
        Get the handlers that are instances of ``__class``.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @not_implemented
    def get_by_filter(self, __func: Callable[[_HandlerT], bool]) -> List[_HandlerT]:
        """
        Get the handlers by the given filter function.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    #
    # Handler display APIs.
    #

    @not_implemented
    def display(self, *args, **kwargs) -> None:
        """
        Display the handler structure.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @not_implemented
    def get_display_attr_dict(self) -> Dict[str, Any]:
        """
        Return the names and values of attributes to be displayed.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @not_implemented
    def get_classname(self) -> str:
        """
        Get the class name of the handler (for display).

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    #
    # Handler attribute operations.
    #

    @abstractmethod
    def get_id(self) -> str:
        """
        Get the id of the handler.
        """
        pass

    @abstractmethod
    def set_id(self, __id: Union[str, EmptyFlag]) -> None:
        """
        Set id of the handler. If the given ``__id`` is ``EmptyFlag``, then set the value
        of ``gen_handler_id__`` to the handler.
        """
        pass

    @not_implemented
    def get_wrappers(self) -> Union[_HandlerWrapperContainerT, Nothing]:
        """
        Get the handler wrappers of the handler (if any).

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @not_implemented
    def set_wrappers(
        self, __wrappers: Union[Iterable[_HandlerWrapperT], EmptyFlag]
    ) -> None:
        """
        Set the handler wrappers. If ``__wrappers`` is ``EmptyFlag``, then set wrappers
        to ``NOTHING``.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @not_implemented
    def get_lifecycle(self, *args, **kwargs):
        """
        TODO: The lifecycle feature is to be implemented.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass

    @not_implemented
    def set_lifecycle(self, *args, **kwargs):
        """
        TODO: The lifecycle feature is to be implemented.

        NOTE: This method is optionally implemented, and the template function will
        be removed at runtime.
        """
        pass


class HandlerContainerABC(
    HandlerABC[
        _HandlerT,
        _HandlerContainerT,
        _HandlerWrapperT,
        _HandlerWrapperContainerT,
        _ContextT,
    ],
    ComponentContainerABC[_HandlerT],
    ABC,
    Generic[
        _HandlerT,
        _HandlerContainerT,
        _HandlerWrapperT,
        _HandlerWrapperContainerT,
        _ContextT,
    ],
):
    """
    ABC for HandlerContainers.
    """

    pass
