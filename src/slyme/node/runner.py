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

"""High-level Node execution helpers used by :meth:`Node.run`."""

from dataclasses import replace
from typing import Any, Mapping, Optional, Sequence

from slyme.cli import parse_and_inject, prepare_args
from slyme.context import Context, Ref, RefLike


def _prepare_context(
    node: Any,
    context: Optional[Context],
    *,
    inputs: Optional[Mapping[RefLike, Any]],
    use_argparse: bool,
    cli_args: Optional[Sequence[str]],
) -> Context:
    if context is None:
        context = Context()
    elif not isinstance(context, Context):
        raise TypeError(
            f"context must be a Context or None, got {type(context).__name__}"
        )

    if inputs:
        context.update(inputs)

    args = prepare_args(node=node)

    if use_argparse:
        # Values supplied through Context or inputs satisfy required arguments and
        # become parser defaults. Explicit CLI values still take precedence.
        effective_args = {}
        for path, arg in args.items():
            ref = Ref(path)
            if context.exists(ref):
                arg = replace(arg, default=context.get(ref), required=False)
            effective_args[path] = arg

        context = parse_and_inject(
            context=context,
            cli_args=list(cli_args) if cli_args is not None else None,
            extra_args=effective_args,
        )
    else:
        if cli_args is not None:
            raise ValueError("cli_args requires use_argparse=True")

        defaults = {}
        for path, arg in args.items():
            ref = Ref(path)
            if context.exists(ref):
                continue
            default = arg.resolve_default()
            if not arg.is_missing(default):
                defaults[ref] = default
        if defaults:
            context.update(defaults)

    missing = [
        path
        for path, arg in args.items()
        if arg.required and not context.exists(Ref(path))
    ]
    if missing:
        formatted = ", ".join(repr(path) for path in missing)
        raise ValueError(f"Missing required Node input(s): {formatted}")

    return context


def _format_result(
    context: Context,
    *,
    outputs: Any,
    return_context: bool,
) -> Any:
    if outputs is None:
        return context

    output = context.extract(outputs)
    if return_context:
        return output, context
    return output


def run_node(
    node: Any,
    context: Optional[Context] = None,
    /,
    *,
    inputs: Optional[Mapping[RefLike, Any]] = None,
    outputs: Any = None,
    return_context: bool = False,
    use_argparse: bool = False,
    cli_args: Optional[Sequence[str]] = None,
) -> Any:
    """Run a synchronous Node executable."""
    context = _prepare_context(
        node,
        context,
        inputs=inputs,
        use_argparse=use_argparse,
        cli_args=cli_args,
    )
    node(context)
    return _format_result(
        context,
        outputs=outputs,
        return_context=return_context,
    )


async def run_async_node(
    node: Any,
    context: Optional[Context] = None,
    /,
    *,
    inputs: Optional[Mapping[RefLike, Any]] = None,
    outputs: Any = None,
    return_context: bool = False,
    use_argparse: bool = False,
    cli_args: Optional[Sequence[str]] = None,
) -> Any:
    """Run an asynchronous Node executable."""
    context = _prepare_context(
        node,
        context,
        inputs=inputs,
        use_argparse=use_argparse,
        cli_args=cli_args,
    )
    await node(context)
    return _format_result(
        context,
        outputs=outputs,
        return_context=return_context,
    )
