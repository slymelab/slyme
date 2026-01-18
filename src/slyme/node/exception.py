class NodeException(Exception):
    """Base exception class for all exceptions of ``Node``."""

    pass


# Node interrupts.
class NodeInterrupt(NodeException):
    """Used to interrupt all node executions."""

    pass


class NodeTerminate(NodeInterrupt):
    """Terminate the whole node execution."""

    def __init__(self, msg: str = "Terminate.", source_node=None) -> None:
        super().__init__()
        self.msg = msg
        self.source_node = source_node

    def __str__(self) -> str:
        return f"source_node: {self.source_node}, msg: {self.msg}"


# Node exception records.
class NodeExceptionRecord(NodeException):
    """Used to record the node exception info."""

    def __init__(self, exception_node, exception: Exception) -> None:
        super().__init__()
        self.exception_node = exception_node
        self.exception = exception

    def __str__(self) -> str:
        return f"exception_node: {self.exception_node}"


class NodeWrapperExceptionRecord(NodeExceptionRecord):
    """Used to record the exception info raised by a ``NodeWrapper``."""

    def __init__(self, exception_node, wrapped_node, exception: Exception) -> None:
        super().__init__(exception_node, exception)
        self.wrapped_node = wrapped_node

    def __str__(self) -> str:
        return f"exception_wrapper: {self.exception_node}, wrapped_node: {self.wrapped_node}"


class NodeExpressionExceptionRecord(NodeExceptionRecord):
    """Used to record the exception info raised by a ``NodeWrapper``."""

    def __init__(self, exception_node, exception: Exception, source_node=None) -> None:
        super().__init__(exception_node, exception)
        self.source_node = source_node

    def __str__(self) -> str:
        return (
            f"exception_wrapper: {self.exception_node}, source_node: {self.source_node}"
        )
