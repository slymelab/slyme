from abc import ABC, abstractmethod
from slyme.utils.typing.native import TypeVar, Union, Generator, Generic
from . import HandlerContainerABC

_HandlerT = TypeVar("_HandlerT")
_HandlerContainerT = TypeVar("_HandlerContainerT")
_HandlerWrapperT = TypeVar("_HandlerWrapperT")
_HandlerWrapperContainerT = TypeVar("_HandlerWrapperContainerT")
_ContextT = TypeVar("_ContextT")


class HandlerWrapperABC(
    HandlerContainerABC[
        _HandlerT,
        _HandlerContainerT,
        _HandlerWrapperT,
        _HandlerWrapperContainerT,
        _ContextT,
    ],
    ABC,
    Generic[
        _HandlerT,
        _HandlerContainerT,
        _HandlerWrapperT,
        _HandlerWrapperContainerT,
        _ContextT,
    ],
):
    @abstractmethod
    def handle(self, ctx: _ContextT) -> None:
        """
        NOTE: This method should be overridden.
        """
        pass

    @abstractmethod
    def handle_yield(
        self, ctx: _ContextT, wrapped: Union[_HandlerT, _HandlerWrapperT]
    ) -> Generator:
        """
        Core handler wrapper API for custom operations.
        """
        yield

    @abstractmethod
    def call_handle_yield(self, ctx: _ContextT, wrapped: _HandlerT) -> Generator:
        """
        A mixin method that wraps the generator returned by ``handle_yield``.
        """
        yield


class HandlerWrapperContainerABC(
    HandlerContainerABC[
        _HandlerT,
        _HandlerContainerT,
        _HandlerWrapperT,
        _HandlerWrapperContainerT,
        _ContextT,
    ],
    ABC,
    Generic[
        _HandlerT,
        _HandlerContainerT,
        _HandlerWrapperT,
        _HandlerWrapperContainerT,
        _ContextT,
    ],
):
    @abstractmethod
    def handle(self, ctx: _ContextT, wrapped: _HandlerT) -> None:
        """
        NOTE: This method should be overridden.
        """
        pass
