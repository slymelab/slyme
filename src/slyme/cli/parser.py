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

import argparse
import json
import sys
from enum import Enum
from typing import (
    Any,
    Dict,
    Iterable,
    List,
    Literal,
    Optional,
    Type,
    Union,
    get_args,
    get_origin,
)

from slyme.context import Context, Ref, RefLike
from slyme.context.metadata import Arg
from slyme.utils.registry import TypeRegistry

from .resolve import prepare_args

__all__ = [
    "populate_parser",
    "parse_and_inject",
]

ARG_HANDLERS = TypeRegistry("ArgHandlers")


def _string_to_bool(v: Union[str, bool]) -> bool:
    """
    Helper to convert string to boolean for argparse.
    """
    if isinstance(v, bool):
        return v
    v_lower = v.lower()
    if v_lower in ("yes", "true", "t", "y", "1"):
        return True
    elif v_lower in ("no", "false", "f", "n", "0"):
        return False
    else:
        raise argparse.ArgumentTypeError(
            f"Expected boolean value, got {v!r}. (Valid: yes/no, true/false, t/f, y/n, 1/0)"
        )


def _infer_type(arg: Arg) -> Type:
    """
    Infer the type of the argument based on explicit definition or default value.
    """
    if arg.type is not None:
        return arg.type

    default_val = arg.resolve_default()
    if not Arg.is_missing(default_val) and default_val is not None:
        return type(default_val)

    # Fallback: if no type info is available, assume str
    return str


def _json_loader(v: str) -> Any:
    """
    Helper to parse JSON string.
    """
    try:
        return json.loads(v)
    except json.JSONDecodeError as e:
        raise argparse.ArgumentTypeError(f"Invalid JSON: {e}")


@ARG_HANDLERS.register(key=bool)
def _handle_bool(
    parser: argparse.ArgumentParser,
    path: str,
    arg: Arg,
    flags: List[str],
    kwargs: Dict[str, Any],
    default_val: Any,
    arg_type: Type,
    origin: Any,
    type_args: tuple,
):
    kwargs["type"] = _string_to_bool
    kwargs["nargs"] = "?"
    kwargs["const"] = True

    # Handle default values
    if not Arg.is_missing(default_val):
        kwargs["default"] = default_val
    else:
        # Default to False if required=False and no default provided (Standard argparse behavior)
        if not arg.required:
            kwargs["default"] = False

    parser.add_argument(*flags, **kwargs)

    # Add --no-xxx flag if default is True
    if default_val is True:
        no_flags = [f"--no-{f.lstrip('-')}" for f in flags if f.startswith("--")]
        if no_flags:
            parser.add_argument(
                *no_flags,
                action="store_false",
                dest=path,
                help=f"Disable {path}",
            )


@ARG_HANDLERS.register(key=list)
def _handle_list(
    parser: argparse.ArgumentParser,
    path: str,
    arg: Arg,
    flags: List[str],
    kwargs: Dict[str, Any],
    default_val: Any,
    arg_type: Type,
    origin: Any,
    type_args: tuple,
):
    kwargs["nargs"] = "+" if arg.nargs is None else arg.nargs
    # If explicit type is list[int], type_args will be (int,).
    # If origin is list, type_args will be generic args.
    # We prefer type_args from get_args(arg_type) if available.
    inner_type = type_args[0] if type_args else str
    kwargs["type"] = inner_type

    if not Arg.is_missing(default_val):
        kwargs["default"] = default_val
    elif arg.required:
        kwargs["required"] = True
    parser.add_argument(*flags, **kwargs)


@ARG_HANDLERS.register(key=tuple)
def _handle_tuple(
    parser: argparse.ArgumentParser,
    path: str,
    arg: Arg,
    flags: List[str],
    kwargs: Dict[str, Any],
    default_val: Any,
    arg_type: Type,
    origin: Any,
    type_args: tuple,
):
    # Treat tuple similar to list for argparse
    _handle_list(
        parser, path, arg, flags, kwargs, default_val, arg_type, origin, type_args
    )


@ARG_HANDLERS.register(key=dict)
def _handle_dict(
    parser: argparse.ArgumentParser,
    path: str,
    arg: Arg,
    flags: List[str],
    kwargs: Dict[str, Any],
    default_val: Any,
    arg_type: Type,
    origin: Any,
    type_args: tuple,
):
    # JSON support
    kwargs["type"] = _json_loader
    if not Arg.is_missing(default_val):
        kwargs["default"] = default_val
    elif arg.required:
        kwargs["required"] = True

    # dict usually doesn't use choices in CLI unless specific constraint,
    # but argparse choices check against the parsed object (dict), which might be tricky if user passes JSON string.
    # So we omit choices for dict unless we want to support it.

    parser.add_argument(*flags, **kwargs)


def _handle_literal(
    parser: argparse.ArgumentParser,
    path: str,
    arg: Arg,
    flags: List[str],
    kwargs: Dict[str, Any],
    default_val: Any,
    arg_type: Type,
    origin: Any,
    type_args: tuple,
):
    kwargs["choices"] = type_args
    kwargs["type"] = type(type_args[0])
    if not Arg.is_missing(default_val):
        kwargs["default"] = default_val
    elif arg.required:
        kwargs["required"] = True
    parser.add_argument(*flags, **kwargs)


def _handle_enum(
    parser: argparse.ArgumentParser,
    path: str,
    arg: Arg,
    flags: List[str],
    kwargs: Dict[str, Any],
    default_val: Any,
    arg_type: Type,
    origin: Any,
    type_args: tuple,
):
    kwargs["choices"] = [e.value for e in arg_type]
    kwargs["type"] = type(list(kwargs["choices"])[0])
    if not Arg.is_missing(default_val):
        kwargs["default"] = (
            default_val.value if isinstance(default_val, Enum) else default_val
        )
    elif arg.required:
        kwargs["required"] = True
    parser.add_argument(*flags, **kwargs)


def _handle_generic(
    parser: argparse.ArgumentParser,
    path: str,
    arg: Arg,
    flags: List[str],
    kwargs: Dict[str, Any],
    default_val: Any,
    arg_type: Type,
    origin: Any,
    type_args: tuple,
):
    kwargs["type"] = arg_type
    if arg.nargs is not None:
        kwargs["nargs"] = arg.nargs

    if not Arg.is_missing(default_val):
        kwargs["default"] = default_val
    elif arg.required:
        kwargs["required"] = True

    if arg.choices is not None:
        kwargs["choices"] = arg.choices

    parser.add_argument(*flags, **kwargs)


def _add_argument(parser: argparse.ArgumentParser, path: str, arg: Arg):
    """
    Add a single argument to the parser.
    """
    # 1. Determine Flag Names
    # Convert dotted path "model.config.lr" -> "--model.config.lr" and "--model-config-lr"
    flags = [f"--{path}"]
    if "." in path:
        flags.append(f"--{path.replace('.', '-')}")
    if "_" in path:
        flags.append(f"--{path.replace('_', '-')}")

    # Add aliases (e.g., "-lr")
    flags.extend(arg.aliases)

    # Remove duplicates while preserving order
    flags = list(dict.fromkeys(flags))

    # 2. Base kwargs for argparse
    kwargs = {
        "dest": path,  # Keep the dot notation for the destination key
        "help": arg.help,
        "metavar": arg.metavar,
    }

    # 3. Resolve Type and Default
    arg_type = _infer_type(arg)
    default_val = arg.resolve_default()

    # Handle Optional[T] or Union[T, None] (Primitive unpacking)
    origin = get_origin(arg_type)
    type_args = get_args(arg_type)
    if origin is Union and type(None) in type_args:
        # Extract the non-None type
        non_none_args = [t for t in type_args if t is not type(None)]
        if len(non_none_args) == 1:
            arg_type = non_none_args[0]
            origin = get_origin(arg_type)
            type_args = get_args(arg_type)

    # 4. Handle Specific Types using Registry or Dispatch

    # Try direct registry match (safe usage of get)
    handler = ARG_HANDLERS.get(arg_type, default=None)
    if handler:
        handler(
            parser, path, arg, flags, kwargs, default_val, arg_type, origin, type_args
        )
        return

    # Try origin match (for List[int] etc where arg_type is generic alias)
    if origin:
        handler = ARG_HANDLERS.get(origin, default=None)
        if handler:
            handler(
                parser,
                path,
                arg,
                flags,
                kwargs,
                default_val,
                arg_type,
                origin,
                type_args,
            )
            return

    # Fallback / Special Cases
    if origin is Literal:
        _handle_literal(
            parser, path, arg, flags, kwargs, default_val, arg_type, origin, type_args
        )
    elif isinstance(arg_type, type) and issubclass(arg_type, Enum):
        _handle_enum(
            parser, path, arg, flags, kwargs, default_val, arg_type, origin, type_args
        )
    else:
        _handle_generic(
            parser, path, arg, flags, kwargs, default_val, arg_type, origin, type_args
        )


def populate_parser(parser: argparse.ArgumentParser, args_map: Dict[str, Arg]) -> None:
    """
    Populate an existing ArgumentParser with resolved arguments.
    """
    for path, arg in args_map.items():
        _add_argument(parser, path, arg)


def parse_and_inject(
    context: Optional[Context] = None,
    parser: Optional[argparse.ArgumentParser] = None,
    cli_args: Optional[List[str]] = None,
    node: Optional[Union[Any, Iterable[RefLike]]] = None,
    extra_refs: Optional[Iterable[RefLike]] = None,
    extra_args: Optional[Dict[str, Arg]] = None,
) -> Union[Dict[str, Any], Context]:
    """
    High-level entry point to parse arguments and optionally inject them into a Context.

    Args:
        context: Optional Context to inject parsed values into. If provided, returns a new Context.
        parser: Optional existing parser to extend.
        cli_args: Command line arguments to parse (defaults to sys.argv[1:]).
        node: Node element to collect refs from.
        extra_refs: Additional Refs to include.
        extra_args: Additional arguments to add/override.

    Returns:
        If context is provided: A new Context with injected values.
        If context is None: A dictionary of parsed values.
    """
    # 1. Prepare Arguments
    args_map = prepare_args(node=node, extra_refs=extra_refs, extra_args=extra_args)

    # 2. Populate Parser
    if parser is None:
        parser = argparse.ArgumentParser(allow_abbrev=False)

    populate_parser(parser, args_map)

    # 3. Parse
    if cli_args is None:
        cli_args = sys.argv[1:]

    namespace = parser.parse_args(cli_args)
    parsed_values = vars(namespace)

    # 4. Inject or Return
    if context is None:
        return parsed_values

    # Prepare updates for Context.mutate
    # parsed_values is { "path": value, ... }
    # Context.mutate expects { Ref: value }
    updates = {}
    for key, value in parsed_values.items():
        # Only inject if it looks like a path (non-empty string)
        if key:
            updates[Ref(key)] = value

    return context.mutate(updates=updates)
