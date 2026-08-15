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

"""Versioned JSON envelope shared by every ``slyme`` subcommand.

The envelope is the transport-independent contract between the runner and its
callers (the DSH plugin, shell scripts, CI, ...). It never depends on *how* it
is delivered — stdout-inline or a result file (see ``slyme.runner.io``).
"""

import traceback
from typing import Any, Dict, Optional

PROTOCOL = "slyme.runner"
SCHEMA_VERSION = 1

KIND_CALL = "call"
KIND_DISCOVER = "discover"
KIND_NODES = "nodes"
KIND_INFO = "info"


def protocol_id() -> str:
    return f"{PROTOCOL}/{SCHEMA_VERSION}"


def error_payload(exc: BaseException) -> Dict[str, str]:
    """Serialize an exception into the ``error`` object of an envelope."""
    return {
        "type": type(exc).__name__,
        "message": str(exc),
        "traceback": traceback.format_exc(),
    }


def make_envelope(
    kind: str,
    ok: bool,
    result: Any = None,
    error: Optional[Dict[str, str]] = None,
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a response envelope.

    ``stdout``/``stderr`` are the *pipeline's* captured output (``call`` and
    ``nodes`` only); they are folded into the envelope so the transport channel
    itself never mixes machine data with workflow noise.
    """
    env: Dict[str, Any] = {
        "protocol": protocol_id(),
        "kind": kind,
        "ok": bool(ok),
        "schema_version": SCHEMA_VERSION,
    }
    if ok:
        env["result"] = result
        env["error"] = None
    else:
        env["result"] = None
        env["error"] = error or {
            "type": "Error",
            "message": "unknown error",
            "traceback": "",
        }
    if stdout is not None or stderr is not None:
        env["stdout"] = stdout or ""
        env["stderr"] = stderr or ""
    return env
