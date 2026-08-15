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

"""Command main entry: ``python -m slyme`` and the ``slyme`` console script."""

import argparse
import sys
from typing import List, Optional

import slyme
from slyme.cli.commands import call, discover, info, nodes
from slyme.runner.io import emit_envelope
from slyme.runner.protocol import error_payload, make_envelope

__version__ = getattr(slyme, "__version__", "unknown")

_SUBCOMMANDS = [call, discover, nodes, info]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="slyme",
        description="Slyme runner CLI: execute, discover, and introspect workflows.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command", required=True, metavar="command")
    for mod in _SUBCOMMANDS:
        mod.register(sub)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = getattr(args, "handler", None)
    if handler is None:
        parser.error("missing command")
    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130
    except Exception as exc:  # last resort: emit an envelope instead of a bare crash
        emit_envelope(
            make_envelope("runner", False, error=error_payload(exc)),
            result_file=getattr(args, "result_file", None),
        )
        return 0


if __name__ == "__main__":
    sys.exit(main())
