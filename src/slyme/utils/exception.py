"""
Custom exceptions in ``slyme``.
"""

#
# API Misused
#


class APIMisused(Exception):

    def __init__(self, msg: str) -> None:
        super().__init__()
        self.msg = msg

    def __str__(self) -> str:
        return f"{self.msg}"


from .typing.extension import NOTHING

#
# Base Exception class.
#


class HandlerBaseException(Exception):
    """
    Base exception class for all exceptions of ``Handler``.
    """

    pass


#
# Handler Interrupt exceptions.
#


class HandlerInterrupt(HandlerBaseException):
    """
    ``HandlerInterrupt`` is used to interrupt handler execution.
    """

    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class HandlerBreak(HandlerInterrupt):
    """
    Break the ``HandlerContainer`` execution.
    """

    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class HandlerContinue(HandlerInterrupt):
    """
    Similar to continue, skip the remaining handlers, and proceed to
    the next iteration (if any).
    """

    def __init__(self, *args: object) -> None:
        super().__init__(*args)


class HandlerTerminate(HandlerInterrupt):
    """
    Terminate the whole handler execution.
    """

    def __init__(self, msg: str, raise_handler=NOTHING) -> None:
        super().__init__()
        self.msg = msg
        self.raise_handler = raise_handler

    def __str__(self) -> str:
        return f"raise_handler: {str(self.raise_handler)}, msg: {self.msg}"


#
# Handler Exception
#


class HandlerException(HandlerBaseException):
    """
    Used to record the exception info.
    """

    def __init__(self, exception_handler, exception: Exception) -> None:
        super().__init__()
        self.exception_handler = exception_handler
        self.exception = exception

    def __str__(self) -> str:
        return f"exception_handler: {str(self.exception_handler)}"


class HandlerWrapperException(HandlerException):
    """
    Used to record the exception info raised by a ``HandlerWrapper``.
    """

    def __str__(self) -> str:
        return f"exception_wrapper: {str(self.exception_handler)}"
