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

"""``slyme discover`` — AST-scan directories for @builder entries (no execution)."""

import ast
import os
from typing import Any, Dict, List, Optional

from slyme.runner.io import emit_envelope
from slyme.runner.protocol import KIND_DISCOVER, error_payload, make_envelope


def _dec_name(dec) -> Optional[str]:
    if isinstance(dec, ast.Name):
        return dec.id
    if isinstance(dec, ast.Call):
        return _dec_name(dec.func)
    if isinstance(dec, ast.Attribute):
        return dec.attr
    return None


def _is_builder(fn) -> bool:
    return any(_dec_name(dec) == "builder" for dec in fn.decorator_list)


def _collect_refs(node) -> List[str]:
    refs = set()
    parents = {
        child: parent
        for parent in ast.walk(node)
        for child in ast.iter_child_nodes(parent)
    }
    for sub in ast.walk(node):
        if isinstance(sub, ast.Attribute):
            # Only inspect the outermost attribute in a chain. ``ast.walk`` also
            # yields the intermediate ``R.input`` node for ``R.input.value``;
            # reporting both creates a boundary that the source never declared.
            parent = parents.get(sub)
            if isinstance(parent, ast.Attribute) and parent.value is sub:
                continue
            chain = []
            cur: ast.expr = sub
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            if isinstance(cur, ast.Name) and cur.id == "R":
                refs.add(".".join(reversed(chain)))
        elif isinstance(sub, ast.Call):
            fn = sub.func
            if (
                isinstance(fn, ast.Name)
                and fn.id == "Ref"
                and sub.args
                and isinstance(sub.args[0], ast.Constant)
                and isinstance(sub.args[0].value, str)
            ):
                refs.add(sub.args[0].value)
    return sorted(refs)


def _read_doc(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return ast.get_docstring(ast.parse(fh.read(), filename=path)) or ""
    except Exception:
        return ""


def _discover_dir(workflow_dir: str):
    """Walk a directory tree into ``folders`` and ``workflows``.

    A folder (a directory holding an ``__init__.py``) is a workflow set whose
    description is that file's docstring. A regular ``.py`` file is also a
    workflow set whose description is its top docstring; its top-level
    ``@builder`` functions are the executable workflows. Nothing is imported
    or executed.
    """
    folders: List[Dict[str, Any]] = []
    workflows: List[Dict[str, Any]] = []
    if not os.path.isdir(workflow_dir):
        return folders, workflows
    for dirpath, dirnames, filenames in os.walk(workflow_dir):
        # Skip hidden and underscore-prefixed trees (e.g. `__pycache__`).
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith("_") and not d.startswith(".")
        )
        rel = os.path.relpath(dirpath, workflow_dir)
        pkg = rel.replace(os.sep, ".") if rel != "." else ""
        if "__init__.py" in filenames and pkg:
            folders.append(
                {"path": pkg, "doc": _read_doc(os.path.join(dirpath, "__init__.py"))}
            )
        for fname in sorted(filenames):
            if not fname.endswith(".py") or fname.startswith("_"):
                continue
            stem = fname[:-3]
            module = f"{pkg}.{stem}" if pkg else stem
            path = os.path.join(dirpath, fname)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    tree = ast.parse(fh.read(), filename=path)
            except Exception:
                continue
            module_doc = ast.get_docstring(tree) or ""
            for node in tree.body:
                if isinstance(
                    node, (ast.FunctionDef, ast.AsyncFunctionDef)
                ) and _is_builder(node):
                    workflows.append(
                        {
                            "name": f"{module}.{node.name}",
                            "module": module,
                            "entry": node.name,
                            "doc": ast.get_docstring(node) or "",
                            "moduleDoc": module_doc,
                            "refs": _collect_refs(node),
                        }
                    )
    return folders, workflows


def _discover(paths: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    folders: List[Dict[str, Any]] = []
    workflows: List[Dict[str, Any]] = []
    seen_folders = set()
    seen_workflows = set()
    for p in paths:
        f, w = _discover_dir(p)
        for item in f:
            if item["path"] not in seen_folders:
                seen_folders.add(item["path"])
                folders.append(item)
        for item in w:
            if item["name"] not in seen_workflows:
                seen_workflows.add(item["name"])
                workflows.append(item)
    folders.sort(key=lambda item: item["path"])
    workflows.sort(key=lambda item: item["name"])
    return {"folders": folders, "workflows": workflows}


def run(args) -> int:
    try:
        paths = list(args.path or []) + list(args.paths or [])
        result = _discover(paths)
        envelope = make_envelope(KIND_DISCOVER, True, result=result)
    except Exception as exc:
        envelope = make_envelope(KIND_DISCOVER, False, error=error_payload(exc))
    emit_envelope(envelope, result_file=args.result_file)
    return 0


def register(subparsers) -> None:
    p = subparsers.add_parser(
        "discover",
        help="AST-scan directories for @builder entries (no execution)",
        description="Scan workflow directories and emit a versioned index of @builder entries.",
    )
    p.add_argument(
        "--path", action="append", default=None, help="Directory to scan; repeatable."
    )
    p.add_argument(
        "paths",
        nargs="*",
        help="Directories to scan (positional alternative to --path).",
    )
    p.add_argument(
        "--result-file",
        help="Write the response envelope atomically to this path (else stdout).",
    )
    p.set_defaults(handler=run)
