from __future__ import annotations

import argparse
import sys
from enum import Enum
from typing import Any, Literal

import pytest

from slyme.cli import parse_and_inject, prepare_args, resolve_args_from_refs
from slyme.cli.parser import _json_loader, _string_to_bool, populate_parser
from slyme.context import ARG, HELP, TYPE, Arg, Context, Schema, ref
from slyme.node import Auto, node

R = Schema({"input": {"count": ref(), "value": ref()}, "verbose": ref()})


class Color(Enum):
    RED = "red"
    BLUE = "blue"


def test_populate_parser_supports_all_argument_shapes() -> None:
    args_map = {
        "feature.enabled": Arg(type=bool, default=True),
        "feature.optional": Arg(type=bool),
        "items": Arg(type=list[int], required=True),
        "coords": Arg(type=tuple[float, ...], nargs=2),
        "config": Arg(type=dict[str, Any], required=True),
        "mode": Arg(type=Literal["fast", "safe"], default="safe"),
        "color": Arg(type=Color, default=Color.RED),
        "level_value": Arg(
            default=2,
            choices=[1, 2, 3],
            aliases=["-l"],
            help="level",
            metavar="N",
        ),
        "optional": Arg(type=int | None),
        "fallback": Arg(),
    }
    parser = argparse.ArgumentParser()
    populate_parser(parser, args_map)
    parsed = vars(
        parser.parse_args(
            [
                "--no-feature-enabled",
                "--feature.optional",
                "false",
                "--items",
                "1",
                "2",
                "--coords",
                "1.5",
                "2.5",
                "--config",
                '{"answer": 42}',
                "--mode",
                "fast",
                "--color",
                "blue",
                "-l",
                "3",
                "--optional",
                "9",
                "--fallback",
                "text",
            ]
        )
    )

    assert parsed == {
        "feature.enabled": False,
        "feature.optional": False,
        "items": [1, 2],
        "coords": [1.5, 2.5],
        "config": {"answer": 42},
        "mode": "fast",
        "color": "blue",
        "level_value": 3,
        "optional": 9,
        "fallback": "text",
    }


def test_parser_helpers_validate_boolean_and_json() -> None:
    for value in (True, "yes", "TRUE", "t", "y", "1"):
        assert _string_to_bool(value)
    for value in (False, "no", "FALSE", "f", "n", "0"):
        assert not _string_to_bool(value)
    with pytest.raises(argparse.ArgumentTypeError, match="Expected boolean"):
        _string_to_bool("maybe")
    assert _json_loader("[1, 2]") == [1, 2]
    with pytest.raises(argparse.ArgumentTypeError, match="Invalid JSON"):
        _json_loader("{")


def test_parse_and_inject_returns_dict_or_updates_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = {
        "input.count": Arg(type=int, required=True),
        "verbose": Arg(type=bool),
    }
    assert parse_and_inject(extra_args=args, cli_args=["--input-count", "3"]) == {
        "input.count": 3,
        "verbose": False,
    }

    ctx = Context(schema=R)
    returned = parse_and_inject(
        context=ctx,
        extra_args=args,
        cli_args=["--input.count", "4", "--verbose"],
    )
    assert returned is ctx
    assert ctx.get(R.resolve("input.count")) == 4
    assert ctx.get(R.resolve("verbose")) is True

    monkeypatch.setattr(sys, "argv", ["program", "--input.count", "5"])
    assert parse_and_inject(extra_args=args)["input.count"] == 5


def test_resolve_args_merges_metadata_and_rejects_conflicts() -> None:
    base = Arg(required=True)
    refs = [
        Schema(
            {"input": {"value": ref(metadata={ARG: base, HELP: "value help"})}}
        ).resolve("input.value"),
        Schema({"input": {"value": ref(metadata={ARG: base, TYPE: int})}}).resolve(
            "input.value"
        ),
    ]
    resolved = resolve_args_from_refs(refs)
    assert resolved["input.value"].help == "value help"
    assert resolved["input.value"].type is int

    with pytest.raises(TypeError, match="Invalid metadata"):
        resolve_args_from_refs(
            [Schema({"bad": ref(metadata={ARG: "not-an-arg"})}).resolve("bad")]
        )
    with pytest.raises(ValueError, match="Conflicting Arg"):
        resolve_args_from_refs(
            [
                Schema({"same": ref(metadata={ARG: Arg(default=1)})}).resolve("same"),
                Schema({"same": ref(metadata={ARG: Arg(default=2)})}).resolve("same"),
            ]
        )
    with pytest.raises(ValueError, match="Argument conflict"):
        prepare_args(extra_refs=refs, extra_args={"input.value": Arg(default=1)})


def test_prepare_args_collects_refs_from_node_tree() -> None:
    schema = Schema(
        {
            "input": {
                "value": ref(int, metadata={ARG: Arg(required=True)}),
            }
        }
    )

    @node
    def application(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    graph = application(value=schema.resolve("input.value"))
    args = prepare_args(node=graph)
    assert args == {"input.value": Arg(type=int, required=True)}
