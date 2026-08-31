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

"""Execution helpers: load a pipeline module and run a @builder entry."""

import contextlib
import importlib
import importlib.util
import os
import shutil
import sys
import tempfile
from collections.abc import Iterator
from typing import Any

from slyme.context import Ref


def _flatten(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            path = f"{prefix}.{k}" if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flatten(v, path))
            else:
                out[path] = v
    else:
        out[prefix] = obj
    return out


def seed_inputs(input_data: Any) -> dict[Ref, Any]:
    """Flatten nested envelope data into a ``{Ref(path): value}`` input mapping.

    The JSON envelope nests values (e.g. ``{"input": {"root": "/x"}}``) while the
    Context is a flat dotted-path namespace; this returns
    ``{Ref("input.root"): "/x"}`` for ``Node.run(inputs=...)``.
    """
    flat = (
        _flatten(input_data) if isinstance(input_data, dict) else {"input": input_data}
    )
    return {Ref(k): v for k, v in flat.items()}


def load_module(module: str, sys_path: list[str] | None) -> Any:
    for p in reversed(sys_path or []):
        if p and p not in sys.path:
            sys.path.insert(0, p)
    return importlib.import_module(module)


@contextlib.contextmanager
def source_module(source: str) -> Iterator[Any]:
    """Import ``source`` as a throwaway module, cleaned up on exit."""
    tmpdir = tempfile.mkdtemp(prefix="slyme-run-")
    mod_path = os.path.join(tmpdir, "pipeline.py")
    try:
        with open(mod_path, "w", encoding="utf-8") as fh:
            fh.write(source)
        spec = importlib.util.spec_from_file_location("pipeline", mod_path)
        if spec is None or spec.loader is None:
            raise ImportError("could not create a module spec for inline source")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        yield mod
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def get_builder(mod: Any, entry: str) -> Any:
    fn = getattr(mod, entry, None)
    if fn is None:
        raise AttributeError(f"module has no @builder entry named '{entry}'")
    node_def = fn()
    if node_def is None:
        raise ValueError(f"builder '{entry}' returned None")
    return node_def
