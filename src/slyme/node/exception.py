# Copyright 2026 The SlymeLab Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.


from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .core import Node, Wrapper


class NodeException(Exception):
    """Base exception class for all exceptions of ``Node``."""

    pass


# Node exception records.
class NodeExceptionRecord(NodeException):
    """Used to record the node exception info."""

    def __init__(self, exception_node: Node[Any]) -> None:
        super().__init__(exception_node)

    @property
    def exception_node(self) -> Node[Any]:
        return self.args[0]

    def __str__(self) -> str:
        return f"exception_node: {self.exception_node}"


class WrapperExceptionRecord(NodeException):
    """Used to record the exception info raised by a ``Wrapper``."""

    def __init__(self, exception_node: Wrapper[Any], wrapped_node: Node[Any]) -> None:
        super().__init__(exception_node, wrapped_node)

    @property
    def exception_node(self) -> Wrapper[Any]:
        return self.args[0]

    @property
    def wrapped_node(self) -> Node[Any]:
        return self.args[1]

    def __str__(self) -> str:
        return f"exception_wrapper: {self.exception_node}, wrapped_node: {self.wrapped_node}"
