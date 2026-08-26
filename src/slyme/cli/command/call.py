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

"""``slyme call`` — run a @builder entry and return its final Context.

Machine contract (also what the DSH plugin uses): the request is a JSON
envelope on stdin::

    {"mode": "module"|"source", "module": "...", "source": "...",
     "entry": "build", "sysPath": [...], "input": {...}}

The response envelope is emitted to ``--result-file`` (atomic) or stdout.
"""

import argparse
import json
import sys
from typing import Any, Dict, Optional

from slyme.runner.boundary import project
from slyme.runner.execute import get_builder, load_module, seed_inputs, source_module
from slyme.runner.io import capture_stdio, emit_envelope
from slyme.runner.protocol import KIND_CALL, error_payload, make_envelope


def _request_from_args(args) -> Dict[str, Any]:
    """Read the request: stdin envelope (machine) or flags (human TTY)."""
    try:
        tty = sys.stdin.isatty()
    except Exception:
        tty = False

    if getattr(args, "envelope", False) or not tty:
        raw = sys.stdin.read()
        return json.loads(raw) if raw.strip() else {}

    req: Dict[str, Any] = {}
    if args.source:
        req["mode"] = "source"
        req["source"] = args.source
    elif args.module:
        req["mode"] = "module"
        req["module"] = args.module
    else:
        raise ValueError(
            "call: provide --module or --source (or pipe an envelope on stdin)"
        )

    if args.entry:
        req["entry"] = args.entry
    if args.sys_path:
        req["sysPath"] = args.sys_path
    if args.input_file:
        with open(args.input_file, "r", encoding="utf-8") as fh:
            req["input"] = json.load(fh)
    elif args.input:
        req["input"] = json.loads(args.input)
    else:
        req["input"] = {}
    if args.project:
        req["project"] = args.project
    return req


def _run(request: Dict[str, Any]) -> Any:
    mode = request.get("mode", "module")
    entry = request.get("entry", "build")
    input_data = request.get("input", {})
    select = request.get("project")

    if mode == "source":
        source = request.get("source", "")
        if not source:
            raise ValueError("missing 'source' in envelope")
        with source_module(source) as mod:
            return _run_module(mod, entry, input_data, select)

    module = request.get("module")
    if not module:
        raise ValueError("missing 'module' in envelope")
    mod = load_module(module, request.get("sysPath", []))
    return _run_module(mod, entry, input_data, select)


def _run_module(mod: Any, entry: str, input_data: Any, select: Any) -> Any:
    node_def = get_builder(mod, entry)
    final_ctx = node_def.run(inputs=seed_inputs(input_data))
    return project(final_ctx, node_def, select)


def run(args) -> int:
    try:
        request = _request_from_args(args)
    except Exception as exc:
        emit_envelope(
            make_envelope(KIND_CALL, False, error=error_payload(exc)),
            result_file=args.result_file,
        )
        return 0

    captured = None
    try:
        with capture_stdio() as cap:
            captured = cap
            result = _run(request)
        envelope = make_envelope(
            KIND_CALL,
            True,
            result=result,
            stdout=captured.stdout,
            stderr=captured.stderr,
        )
        emit_envelope(envelope, result_file=args.result_file)
    except Exception as exc:
        envelope = make_envelope(
            KIND_CALL,
            False,
            error=error_payload(exc),
            stdout=captured.stdout if captured else "",
            stderr=captured.stderr if captured else "",
        )
        emit_envelope(envelope, result_file=args.result_file)
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "call",
        help="Run a @builder entry and return its final Context",
        description=(
            "Run a named (module) or inline (source) pipeline and emit a "
            "versioned envelope."
        ),
    )
    p.add_argument("--module", help="Dotted module name (module mode).")
    p.add_argument(
        "--entry", default="build", help='@builder entry name (default "build").'
    )
    p.add_argument("--source", help="Inline pipeline source (source mode).")
    p.add_argument(
        "--sys-path", action="append", default=None, help="Extra import dir; repeatable."
    )
    p.add_argument(
        "--input",
        help='Inline JSON input (human mode), e.g. \'{"input":{"root":"/x"}}\'.',
    )
    p.add_argument("--input-file", help="Read the request input JSON from a file.")
    p.add_argument(
        "--envelope",
        action="store_true",
        help="Force reading the request envelope from stdin.",
    )
    p.add_argument(
        "--project",
        action="append",
        default=None,
        help='Return-path override: repeatable paths, or "*" for the full Context (default: declared outputs).',
    )
    p.add_argument(
        "--result-file",
        help="Write the response envelope atomically to this path (else stdout).",
    )
    p.set_defaults(handler=run)
