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

"""Transport + output capture for the Slyme runner.

Two concerns live here, kept independent of any Slyme semantics:

1. ``capture_stdio`` — capture everything the *pipeline* prints while leaving
   the runner as the sole writer of the real stdout. It layers ``contextlib``
   (portable, catches ``sys.stdout``/``sys.stderr``) on top of fd-level
   ``dup2`` (POSIX, also catches ``os.write(1, ...)`` and unredirected
   subprocess output). Non-POSIX platforms keep the portable layer only.

2. ``emit_envelope`` — write the envelope either atomically to a result file
   (default for machines) or inline to stdout (handy for humans on POSIX).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
from types import TracebackType
from typing import Any, Dict, Literal, Optional


class Captured:
    """Mutable holder populated after a :class:`capture_stdio` block exits."""

    def __init__(self) -> None:
        self.stdout = ""
        self.stderr = ""


class capture_stdio:
    """Capture pipeline stdout/stderr during a ``with`` block.

    Usage::

        with capture_stdio() as cap:
            run_pipeline()
        print(cap.stdout)   # populated after the block
    """

    def __enter__(self) -> "Captured":
        self._cap = Captured()
        # Drain any pre-capture buffered text to the real stream first.
        sys.stdout.flush()
        sys.stderr.flush()

        self._is_posix = os.name == "posix"
        self._real_out: Optional[int] = None
        self._real_err: Optional[int] = None
        self._cap_out = None
        self._cap_err = None

        if self._is_posix:
            # fd-level: also catches os.write(1, ...) and inherited subprocess fds.
            self._real_out = os.dup(1)
            self._real_err = os.dup(2)
            self._cap_out = tempfile.TemporaryFile()
            self._cap_err = tempfile.TemporaryFile()
            os.dup2(self._cap_out.fileno(), 1)
            os.dup2(self._cap_err.fileno(), 2)

        # Portable Python-level: catches print/warnings/traceback.
        self._py_out = io.StringIO()
        self._py_err = io.StringIO()
        self._stack = contextlib.ExitStack()
        self._stack.enter_context(contextlib.redirect_stdout(self._py_out))
        self._stack.enter_context(contextlib.redirect_stderr(self._py_err))
        return self._cap

    def __exit__(
        self,
        exc_type: Optional[type[BaseException]],
        exc: Optional[BaseException],
        tb: Optional[TracebackType],
    ) -> Literal[False]:
        self._stack.close()  # restore sys.stdout/sys.stderr

        if self._is_posix:
            assert self._real_out is not None
            assert self._real_err is not None
            assert self._cap_out is not None
            assert self._cap_err is not None
            os.dup2(self._real_out, 1)
            os.dup2(self._real_err, 2)
            self._cap_out.seek(0)
            self._cap_err.seek(0)
            fd_out = self._cap_out.read().decode("utf-8", "replace")
            fd_err = self._cap_err.read().decode("utf-8", "replace")
            os.close(self._real_out)
            os.close(self._real_err)
            self._cap_out.close()
            self._cap_err.close()
        else:
            fd_out = ""
            fd_err = ""

        self._cap.stdout = self._py_out.getvalue() + fd_out
        self._cap.stderr = self._py_err.getvalue() + fd_err
        return False  # never suppress an exception


def emit_envelope(
    envelope: Dict[str, Any],
    result_file: Optional[str] = None,
) -> None:
    """Emit ``envelope`` to a result file (atomic) or to stdout.

    ``result_file`` takes precedence over stdout. The JSON is strict: no
    ``default=str`` fallback, so a non-serializable value fails loudly instead
    of silently corrupting the contract.
    """
    payload = json.dumps(envelope, ensure_ascii=False)

    if result_file:
        tmp = result_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, result_file)
        return

    sys.stdout.write(payload + "\n")
    sys.stdout.flush()
