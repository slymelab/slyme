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

"""``slyme info`` — version + environment self-check."""

import argparse
import sys
from typing import Any, Dict

from slyme.runner.io import emit_envelope
from slyme.runner.protocol import KIND_INFO, SCHEMA_VERSION, error_payload, make_envelope


def _info() -> Dict[str, Any]:
    try:
        import slyme

        version = getattr(slyme, "__version__", None)
    except Exception:
        version = None
    return {
        "slymeVersion": version,
        "schemaVersion": SCHEMA_VERSION,
        "python": sys.version.split()[0],
        "executable": sys.executable,
        "importable": version is not None,
    }


def run(args) -> int:
    try:
        envelope = make_envelope(KIND_INFO, True, result=_info())
    except Exception as exc:
        envelope = make_envelope(KIND_INFO, False, error=error_payload(exc))
    emit_envelope(envelope, result_file=args.result_file)
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "info",
        help="Report Slyme version and environment",
        description="Emit a version + environment self-check envelope.",
    )
    p.add_argument(
        "--result-file",
        help="Write the response envelope atomically to this path (else stdout).",
    )
    p.set_defaults(handler=run)
