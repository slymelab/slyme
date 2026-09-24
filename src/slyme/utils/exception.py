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

import sys
from collections.abc import Sequence

__all__ = [
    "BaseExceptionGroup",
    "ExceptionGroup",
    "exception_group",
    "enrich_exception",
]


if sys.version_info >= (3, 11):
    from builtins import BaseExceptionGroup, ExceptionGroup
else:

    class BaseExceptionGroup(BaseException):
        """Minimal pre-3.11 group; construct through exception_group()."""

        def __init__(self, message: str, excs: Sequence[BaseException], /) -> None:
            if not excs:
                raise ValueError("An exception group requires a non-empty sequence.")
            self.message = message
            self.exceptions = tuple(excs)
            super().__init__(message, excs)

        def __str__(self) -> str:
            count = len(self.exceptions)
            suffix = "" if count == 1 else "s"
            return f"{self.message} ({count} sub-exception{suffix})"

    class ExceptionGroup(BaseExceptionGroup, Exception):
        pass


def exception_group(
    message: str, excs: Sequence[BaseException], /
) -> BaseExceptionGroup:
    """Group errors, using an Exception subclass only when every member is one.

    Python 3.11+ returns a native BaseExceptionGroup or ExceptionGroup. Earlier
    versions preserve message, ordered exceptions, and catch semantics, but do
    not provide except*, subgroup(), split(), or grouped traceback rendering.
    Empty sequences raise ValueError.
    """
    if sys.version_info >= (3, 11):
        return BaseExceptionGroup(message, excs)
    else:
        group = (
            ExceptionGroup
            if all(isinstance(error, Exception) for error in excs)
            else BaseExceptionGroup
        )
        return group(message, excs)


def enrich_exception(error: Exception, info: str) -> None:
    """Append context to an existing exception without raising it.

    Python 3.11+ uses exception notes. Older versions update the message in
    ``args``. Call from an exception handler and use bare ``raise`` to rethrow.
    """
    if sys.version_info >= (3, 11):
        error.add_note(info)
    elif error.args and isinstance(error.args[0], str):
        error.args = (f"{error.args[0]} ({info})", *error.args[1:])
    else:
        error.args = (*error.args, f"({info})")
