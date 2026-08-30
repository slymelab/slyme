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

"""``slyme nodes`` — introspect one @builder's node tree (import, no execution)."""

from typing import Any, Dict, List

from slyme.cli.resolve import prepare_args
from slyme.context.metadata import Arg
from slyme.node import NodeElement
from slyme.runner.boundary import resolve_boundary
from slyme.runner.execute import get_builder, load_module, source_module
from slyme.runner.io import capture_stdio, emit_envelope
from slyme.runner.protocol import KIND_NODES, error_payload, make_envelope


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


def _serialize_arg(arg: Arg) -> Dict[str, Any]:
    d: Dict[str, Any] = {}
    if not Arg.is_missing(arg.default):
        d["default"] = _jsonable(arg.default)
    if arg.help:
        d["help"] = arg.help
    if arg.type is not None:
        d["type"] = getattr(arg.type, "__name__", str(arg.type))
    if arg.choices is not None:
        d["choices"] = [_jsonable(c) for c in arg.choices]
    d["required"] = arg.required
    if arg.nargs is not None:
        d["nargs"] = arg.nargs
    if arg.aliases:
        d["aliases"] = list(arg.aliases)
    if arg.metavar:
        d["metavar"] = arg.metavar
    return d


def _walk_nodes(node_def: Any) -> List[Dict[str, Any]]:
    nodes: List[Dict[str, Any]] = []

    def walk(x: Any, path: str) -> None:
        if isinstance(x, NodeElement):
            nodes.append(
                {
                    "keyPath": path or "root",
                    "type": x.__class__.__name__,
                    "func": getattr(x.func, "__name__", repr(x.func)),
                }
            )
            for w in getattr(x, "wrappers", []):
                walk(w, path + ".wrappers")
            for name in x.specs:
                walk(getattr(x, name), f"{path}.{name}" if path else name)
        elif isinstance(x, (list, tuple)):
            for i, v in enumerate(x):
                walk(v, f"{path}[{i}]")
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(v, f"{path}.{k}" if path else str(k))

    walk(node_def, "")
    return nodes


def _introspect(node_def: Any, name: str) -> Dict[str, Any]:
    boundary = resolve_boundary(node_def)
    args_map = prepare_args(node=node_def)
    return {
        "entry": name,
        "refs": boundary["refs"],
        "declaredInputs": boundary["declaredInputs"],
        "declaredOutputs": boundary["declaredOutputs"],
        "inputs": boundary["inputs"],
        "outputs": boundary["outputs"],
        "internal": boundary["internal"],
        "args": {path: _serialize_arg(arg) for path, arg in args_map.items()},
        "nodes": _walk_nodes(node_def),
    }


def _load(args):
    entry = args.entry or "build"
    if args.source:
        with source_module(args.source) as mod:
            return get_builder(mod, entry), entry
    if not args.module:
        raise ValueError("nodes: provide --module or --source")
    mod = load_module(args.module, args.sys_path)
    return get_builder(mod, entry), f"{args.module}.{entry}"


def run(args) -> int:
    captured = None
    try:
        with capture_stdio() as cap:
            captured = cap
            node_def, name = _load(args)
            result = _introspect(node_def, name)
        assert captured is not None
        envelope = make_envelope(
            KIND_NODES,
            True,
            result=result,
            stdout=captured.stdout,
            stderr=captured.stderr,
        )
    except Exception as exc:
        envelope = make_envelope(
            KIND_NODES,
            False,
            error=error_payload(exc),
            stdout=captured.stdout if captured else "",
            stderr=captured.stderr if captured else "",
        )
    emit_envelope(envelope, result_file=args.result_file)
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "nodes",
        help="Introspect a @builder's node tree (no execution)",
        description="Import a pipeline and report its refs and node tree without executing it.",
    )
    p.add_argument("--module", help="Dotted module name.")
    p.add_argument(
        "--entry", default="build", help='@builder entry name (default "build").'
    )
    p.add_argument("--source", help="Inline pipeline source (alternative to --module).")
    p.add_argument(
        "--sys-path",
        action="append",
        default=None,
        help="Extra import dir; repeatable.",
    )
    p.add_argument(
        "--result-file",
        help="Write the response envelope atomically to this path (else stdout).",
    )
    p.set_defaults(handler=run)
