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

"""Boundary resolution: which Context paths are inputs, outputs, or internal.

The Context is a general key-value namespace; the input/output boundary is not
a structure or a naming convention — it is an explicitly declared subset of
paths (via Ref metadata), with a prefix convention as a fallback for
undeclared workflows.
"""

from typing import Any

from slyme.cli.resolve import collect_refs
from slyme.context import Ref
from slyme.context.metadata import ARG, OUTPUT


def resolve_boundary(node_def: Any) -> dict[str, list[str]]:
    """Resolve the effective input/output boundary of a node tree.

    - Declared inputs are refs carrying ``ARG`` metadata.
    - Declared outputs are refs carrying ``OUTPUT`` metadata.
    - When nothing is declared, the ``input.``/``output.`` prefix convention
      is used as a fallback so undeclared workflows keep working.
    - Every other ref is internal and never crosses the boundary.
    """
    refs = collect_refs(node_def)
    all_paths = sorted({r.bound_path for r in refs})
    declared_inputs = sorted({r.bound_path for r in refs if ARG in r.metadata})
    declared_outputs = sorted({r.bound_path for r in refs if OUTPUT in r.metadata})

    inputs = declared_inputs or [p for p in all_paths if p.startswith("input.")]
    outputs = declared_outputs or [p for p in all_paths if p.startswith("output.")]
    internal = sorted(set(all_paths) - set(inputs) - set(outputs))

    return {
        "refs": all_paths,
        "declaredInputs": declared_inputs,
        "declaredOutputs": declared_outputs,
        "inputs": inputs,
        "outputs": outputs,
        "internal": internal,
    }


def _common_prefix(paths: list[str]) -> list[str]:
    if not paths:
        return []
    split = [p.split(".") for p in paths]
    prefix = list(split[0])
    for s in split[1:]:
        i = 0
        while i < len(prefix) and i < len(s) and prefix[i] == s[i]:
            i += 1
        prefix = prefix[:i]
    return prefix


def project_outputs(ctx: Any, output_paths: list[str]) -> Any:
    """Project a final Context onto the given output paths.

    A single output is returned as its raw value; multiple outputs are
    returned as a dict keyed by their namespace-stripped paths (a common
    prefix such as ``output.`` is stripped). Internal and input values are
    never included, which keeps non-JSON Python objects out of the result.
    """
    if not output_paths:
        return {}
    if len(output_paths) == 1:
        return ctx.get(Ref(output_paths[0]), None)
    prefix = _common_prefix(output_paths)
    out: dict[str, Any] = {}
    for path in output_paths:
        parts = path.split(".")
        if prefix:
            parts = parts[len(prefix) :]
        if not parts:
            continue  # contradictory declaration (a leaf and its child); skip parent
        node = out
        for seg in parts[:-1]:
            node = node.setdefault(seg, {})
        node[parts[-1]] = ctx.get(Ref(path), None)
    return out


def project(ctx: Any, node_def: Any, select: Any = None) -> Any:
    """Project a final Context for return, honoring a call-time override.

    ``select`` is the extension point over the builder's static ``OUTPUT``
    declaration:

    - ``None``         -> default (declared outputs, else ``output.*`` prefix)
    - ``"*"``          -> the full Context (debug escape hatch)
    - ``"a.b"``        -> the raw value at that single ref
    - ``["a.b", ...]`` -> a bundle projected onto those refs

    The builder's ``OUTPUT`` metadata is therefore a *default + documentation*,
    never a hard limit on what a caller may retrieve.
    """
    if select is None:
        return project_outputs(ctx, resolve_boundary(node_def)["outputs"])
    if select == "*":
        return ctx.to_dict()
    if isinstance(select, str):
        return project_outputs(ctx, [select])
    if isinstance(select, (list, tuple)):
        paths = [str(p) for p in select]
        if "*" in paths:
            return ctx.to_dict()
        return project_outputs(ctx, paths)
    return project_outputs(ctx, resolve_boundary(node_def)["outputs"])
