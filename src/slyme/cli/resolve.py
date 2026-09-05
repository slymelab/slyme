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

from collections.abc import Iterable
from dataclasses import replace
from typing import (
    Any,
)

from slyme.context import Ref, RefLike, Schema, to_ref
from slyme.context.metadata import ARG, HELP, TYPE, Arg
from slyme.node.core import NODE_ENGINE

__all__ = ["collect_refs", "resolve_args_from_refs", "prepare_args"]


def collect_refs(element: Any) -> list[Ref]:
    """
    Collect all Ref objects from a Node structure using NODE_ENGINE.
    """
    refs: list[Ref] = []

    def is_leaf(node: Any, _) -> bool:
        return isinstance(node, (Ref, Schema))

    # We iterate using NODE_ENGINE which knows how to traverse Node structures
    for _, leaf in NODE_ENGINE.iter_with_key_path(element, is_leaf=is_leaf):
        if isinstance(leaf, (Ref, Schema)):
            refs.append(to_ref(leaf))

    return refs


def resolve_args_from_refs(refs: Iterable[RefLike]) -> dict[str, Arg]:
    """
    Resolve references into a mapping of {path: Arg}.

    Handles conflicts:
    - If multiple references point to the same path, they must have compatible Arg definitions.
    - Enforces strict equality for Arg definitions if they exist.

    Handles merging:
    - If Arg is missing help/type, tries to fill it from metadata[HELP] / metadata[TYPE].
    """
    path_to_args: dict[str, list[Arg]] = {}
    path_to_help: dict[str, list[str]] = {}
    path_to_type: dict[str, list[Any]] = {}

    for ref_like in refs:
        ref = to_ref(ref_like)
        path = ref.bound_path
        if ARG in ref.metadata:
            arg_def = ref.metadata[ARG]
            if not isinstance(arg_def, Arg):
                raise TypeError(
                    f"Invalid metadata for {ARG} in Ref '{path}'. Expected Arg, got {type(arg_def)}."
                )
            path_to_args.setdefault(path, []).append(arg_def)

        if HELP in ref.metadata:
            path_to_help.setdefault(path, []).append(ref.metadata[HELP])

        if TYPE in ref.metadata:
            path_to_type.setdefault(path, []).append(ref.metadata[TYPE])

    resolved: dict[str, Arg] = {}

    for path, args in path_to_args.items():
        # 1. Resolve Arg Conflict (Strict Equality)
        base_arg = args[0]
        for other in args[1:]:
            if base_arg != other:
                raise ValueError(
                    f"Conflicting Arg definitions for path '{path}':\n"
                    f"1. {base_arg}\n"
                    f"2. {other}\n"
                    "Ensure all references for the same path use the same Arg definition."
                )

        # 2. Merge with HELP/TYPE (if needed)
        # Handle immutable Arg by collecting changes first
        changes: dict[str, Any] = {}
        if base_arg.help is None and path in path_to_help:
            # Use the first available help string
            changes["help"] = path_to_help[path][0]

        if base_arg.type is None and path in path_to_type:
            # Use the first available type
            changes["type"] = path_to_type[path][0]

        if changes:
            final_arg = replace(base_arg, **changes)
        else:
            final_arg = base_arg

        resolved[path] = final_arg

    return resolved


def prepare_args(
    node: Any | Iterable[RefLike] | None = None,
    extra_refs: Iterable[RefLike] | None = None,
    extra_args: dict[str, Arg] | None = None,
) -> dict[str, Arg]:
    """
    Prepare arguments by collecting refs from node/extra_refs and merging with extra_args.

    Args:
        node: Node element to collect refs from.
        extra_refs: Additional references to include.
        extra_args: Additional arguments to add/override.

    Returns:
        A dictionary mapping argument paths to Arg definitions.

    Raises:
        ValueError: If there are conflicting argument definitions.
    """
    # 1. Collect references
    refs: list[RefLike] = []
    if node is not None:
        refs.extend(collect_refs(node))
    if extra_refs:
        refs.extend(extra_refs)
    # 2. Resolve Args from references
    args_map = resolve_args_from_refs(refs)
    # 3. Merge Extra Args with Conflict Check
    if extra_args:
        for path, arg in extra_args.items():
            if path in args_map and args_map[path] != arg:
                raise ValueError(
                    f"Argument conflict: '{path}' is defined in both resolved refs and extra_args.\n"
                    f"Resolved: {args_map[path]}\n"
                    f"Extra: {arg}\n"
                    "Please resolve the conflict or remove the duplicate definition."
                )
            args_map[path] = arg
    return args_map
