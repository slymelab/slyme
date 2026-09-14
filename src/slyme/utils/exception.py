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

import sys
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar

_T = TypeVar("_T")


@dataclass(frozen=True)
class Result(Generic[_T]):
    """One completed call: a returned value or a raised error.

    Successful values, including None and exception objects, use ``value``.
    Failed calls use ``error`` and leave ``value`` as None.
    """

    value: _T | None = None
    error: BaseException | None = None


class BatchError(Exception):
    """Ordered completed results retained by an evaluation or cleanup failure.

    Each consumer defines which calls are attempted and when errors are reported.
    Nested errors preserve their own results and local indices.
    """

    def __init__(self, results: Iterable[Result[Any]]) -> None:
        self.results = list(results)
        failed = [
            i for i, result in enumerate(self.results) if result.error is not None
        ]
        super().__init__(f"Batch failed at input indices {failed}.")


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
