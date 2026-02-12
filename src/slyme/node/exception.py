class NodeException(Exception):
    """Base exception class for all exceptions of ``Node``."""

    pass


class NodeTerminate(NodeException):
    """Terminate the whole node execution."""

    def __init__(self, msg: str = "Terminate.", source_node=None) -> None:
        super().__init__(msg, source_node)

    @property
    def msg(self):
        return self.args[0]

    @msg.setter
    def msg(self, value):
        self.args = (value, self.args[1])

    @property
    def source_node(self):
        return self.args[1]

    @source_node.setter
    def source_node(self, value):
        self.args = (self.args[0], value)

    def __str__(self) -> str:
        return f"source_node: {self.source_node}, msg: {self.msg}"


# Node exception records.
class NodeExceptionRecord(NodeException):
    """Used to record the node exception info."""

    def __init__(self, exception_node, exception: Exception) -> None:
        super().__init__(exception_node, exception)

    @property
    def exception_node(self):
        return self.args[0]

    @property
    def exception(self):
        return self.args[1]

    def __str__(self) -> str:
        return f"exception_node: {self.exception_node}"


class WrapperExceptionRecord(NodeException):
    """Used to record the exception info raised by a ``Wrapper``."""

    def __init__(self, exception_node, wrapped_node, exception: Exception) -> None:
        super().__init__(exception_node, wrapped_node, exception)

    @property
    def exception_node(self):
        return self.args[0]

    @property
    def wrapped_node(self):
        return self.args[1]

    @property
    def exception(self):
        return self.args[2]

    def __str__(self) -> str:
        return f"exception_wrapper: {self.exception_node}, wrapped_node: {self.wrapped_node}"


class ExpressionExceptionRecord(NodeException):
    """Used to record the exception info raised by a ``Expression``."""

    def __init__(self, exception_node, exception: Exception, source_node=None) -> None:
        super().__init__(exception_node, exception, source_node)

    @property
    def exception_node(self):
        return self.args[0]

    @property
    def exception(self):
        return self.args[1]

    @property
    def source_node(self):
        return self.args[2]

    @source_node.setter
    def source_node(self, value):
        self.args = (self.args[0], self.args[1], value)

    def __str__(self) -> str:
        return (
            f"exception_expression: {self.exception_node}, source_node: {self.source_node}"
        )
