from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import sys
from enum import Enum
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Literal, Optional

import pytest

from slyme.cli import parse_and_inject, prepare_args, resolve_args_from_refs
from slyme.cli.parser import _json_loader, _string_to_bool, populate_parser
from slyme.context import ARG, HELP, OUTPUT, TYPE, Arg, Context, R, Ref
from slyme.node import Auto, Node, node
from slyme.runner._experimental import (
    EXPERIMENTAL_RUNNER_WARNING,
    warn_experimental_runner,
)
from slyme.runner.boundary import project, project_outputs, resolve_boundary
from slyme.runner.execute import (
    _flatten,
    get_builder,
    load_module,
    seed_inputs,
    source_module,
)
from slyme.runner.io import capture_stdio, emit_envelope
from slyme.runner.protocol import error_payload, make_envelope, protocol_id
from slyme.utils.warning import warning_once


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
        "optional": Arg(type=Optional[int]),
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

    ctx = Context()
    returned = parse_and_inject(
        context=ctx,
        extra_args=args,
        cli_args=["--input.count", "4", "--verbose"],
    )
    assert returned is ctx
    assert ctx.get(R.input.count) == 4
    assert ctx.get(R.verbose) is True

    monkeypatch.setattr(sys, "argv", ["program", "--input.count", "5"])
    assert parse_and_inject(extra_args=args)["input.count"] == 5


def test_resolve_args_merges_metadata_and_rejects_conflicts() -> None:
    base = Arg(required=True)
    refs = [
        Ref("input.value", {ARG: base, HELP: "value help"}),
        Ref("input.value", {ARG: base, TYPE: int}),
    ]
    resolved = resolve_args_from_refs(refs)
    assert resolved["input.value"].help == "value help"
    assert resolved["input.value"].type is int

    with pytest.raises(TypeError, match="Invalid metadata"):
        resolve_args_from_refs([Ref("bad", {ARG: "not-an-arg"})])
    with pytest.raises(ValueError, match="Conflicting Arg"):
        resolve_args_from_refs(
            [
                Ref("same", {ARG: Arg(default=1)}),
                Ref("same", {ARG: Arg(default=2)}),
            ]
        )
    with pytest.raises(ValueError, match="Argument conflict"):
        prepare_args(extra_refs=refs, extra_args={"input.value": Arg(default=1)})


def test_prepare_args_collects_refs_from_node_tree() -> None:
    external = R.input.value(metadata={ARG: Arg(type=int, required=True)})

    @node
    def application(ctx: Context, /, *, value: Auto[int]) -> int:
        return value

    graph = application(value=external)
    args = prepare_args(node=graph)
    assert args == {"input.value": Arg(type=int, required=True)}


def _boundary_graph() -> Node[None]:
    declared_input = R.custom.source(metadata={ARG: Arg(type=int)})
    declared_output = R.custom.result(metadata={OUTPUT: True})

    @node
    def application(
        ctx: Context,
        /,
        *,
        source: Auto[int],
        result: Ref[int],
        internal: Ref[int],
    ) -> None:
        ctx.set(result, source + 1)
        ctx.set(internal, 99)

    return application(
        source=declared_input,
        result=declared_output,
        internal=R.state.internal,
    )


def test_boundary_resolution_and_projection() -> None:
    graph = _boundary_graph()
    boundary = resolve_boundary(graph)
    assert boundary["declaredInputs"] == ["custom.source"]
    assert boundary["declaredOutputs"] == ["custom.result"]
    assert boundary["internal"] == ["state.internal"]

    ctx = graph.run(inputs={R.custom.source: 2})
    assert project(ctx, graph) == 3
    assert project(ctx, graph, "state.internal") == 99
    assert project(ctx, graph, "*")["state"]["internal"] == 99
    assert project(ctx, graph, ["custom.result", "state.internal"]) == {
        "custom": {"result": 3},
        "state": {"internal": 99},
    }
    assert project(ctx, graph, ["*"]) == ctx.to_dict()
    assert project(ctx, graph, object()) == 3
    assert project_outputs(ctx, []) == {}
    assert project_outputs(ctx, ["missing"]) is None


def test_boundary_prefix_fallback_and_nested_projection() -> None:
    @node
    def app(
        ctx: Context,
        /,
        *,
        source: Ref[int],
        first: Ref[int],
        second: Ref[int],
    ) -> None:
        return None

    graph = app(
        source=R.input.value,
        first=R.output.group.first,
        second=R.output.group.second,
    )
    boundary = resolve_boundary(graph)
    assert boundary["inputs"] == ["input.value"]
    assert boundary["outputs"] == ["output.group.first", "output.group.second"]
    ctx = Context()
    ctx.update({R.output.group.first: 1, R.output.group.second: 2})
    assert project_outputs(ctx, boundary["outputs"]) == {"first": 1, "second": 2}


def test_execute_helpers_flatten_seed_load_and_source(tmp_path: Path) -> None:
    assert _flatten({"a": {"b": 1}, "c": 2}) == {"a.b": 1, "c": 2}
    assert _flatten(3, "value") == {"value": 3}
    assert seed_inputs({"input": {"value": 4}}) == {Ref("input.value"): 4}
    assert seed_inputs(5) == {Ref("input"): 5}

    module_path = tmp_path / "sample_pipeline.py"
    module_path.write_text("VALUE = 7\n", encoding="utf-8")
    module = load_module("sample_pipeline", [str(tmp_path), str(tmp_path), ""])
    assert module.VALUE == 7

    with source_module("VALUE = 8") as source:
        assert source.VALUE == 8
        source_file = Path(source.__file__)
        assert source_file.exists()
    assert not source_file.parent.exists()


def test_get_builder_validates_entry_and_result() -> None:
    module = ModuleType("module")
    module.build = lambda: "node"  # type: ignore[attr-defined]
    assert get_builder(module, "build") == "node"
    with pytest.raises(AttributeError, match="no @builder"):
        get_builder(module, "missing")
    module.empty = lambda: None  # type: ignore[attr-defined]
    with pytest.raises(ValueError, match="returned None"):
        get_builder(module, "empty")


def test_capture_stdio_captures_python_and_file_descriptor_output() -> None:
    with capture_stdio() as captured:
        print("python stdout")
        print("python stderr", file=sys.stderr)
        if os.name == "posix":
            os.write(1, b"fd stdout\n")
            os.write(2, b"fd stderr\n")
    assert "python stdout" in captured.stdout
    assert "python stderr" in captured.stderr
    if os.name == "posix":
        assert "fd stdout" in captured.stdout
        assert "fd stderr" in captured.stderr

    with pytest.raises(RuntimeError, match="preserved"):
        with capture_stdio():
            raise RuntimeError("preserved")


def test_envelope_protocol_errors_and_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert protocol_id() == "slyme.runner/1"
    success = make_envelope("test", True, result={"ok": 1}, stdout="out")
    assert success["ok"] is True
    assert success["error"] is None
    assert success["stderr"] == ""
    failure = make_envelope("test", False)
    assert failure["error"]["type"] == "Error"

    try:
        raise ValueError("boom")
    except ValueError as exc:
        payload = error_payload(exc)
    assert payload["type"] == "ValueError"
    assert "boom" in payload["message"]
    assert "Traceback" in payload["traceback"]

    emit_envelope(success)
    assert json.loads(capsys.readouterr().out) == success
    result_file = tmp_path / "result.json"
    emit_envelope(failure, str(result_file))
    assert json.loads(result_file.read_text(encoding="utf-8")) == failure
    assert not Path(str(result_file) + ".tmp").exists()
    with pytest.raises(TypeError):
        emit_envelope({"bad": object()})


PIPELINE_SOURCE = """
from slyme.builder import builder
from slyme.context import ARG, OUTPUT, Arg, Context, R, Ref
from slyme.node import Auto, node

@node
def application(ctx: Context, /, *, value: Auto[int], output: Ref[int]):
    print("pipeline output")
    ctx.set(output, value * 2)

@builder
def build():
    return application(
        value=R.input.value(metadata={ARG: Arg(type=int, required=True)}),
        output=R.output.value(metadata={OUTPUT: True}),
    )
"""


def test_call_command_request_modes_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from slyme.cli.command import call

    request = {
        "mode": "source",
        "source": PIPELINE_SOURCE,
        "input": {"input": {"value": 3}},
    }
    assert call._run(request) == 6
    with pytest.raises(ValueError, match="missing 'source'"):
        call._run({"mode": "source"})
    with pytest.raises(ValueError, match="missing 'module'"):
        call._run({})

    stdin = io.StringIO(json.dumps(request))
    monkeypatch.setattr(sys, "stdin", stdin)
    args = SimpleNamespace(envelope=True)
    assert call._request_from_args(args) == request

    input_file = tmp_path / "input.json"
    input_file.write_text('{"input": {"value": 4}}', encoding="utf-8")

    class TTY(io.StringIO):
        def isatty(self) -> bool:
            return True

    monkeypatch.setattr(sys, "stdin", TTY())
    human_args = SimpleNamespace(
        envelope=False,
        source=PIPELINE_SOURCE,
        module=None,
        entry="build",
        sys_path=None,
        input_file=str(input_file),
        input=None,
        project=["output.value"],
    )
    human = call._request_from_args(human_args)
    assert human["mode"] == "source"
    assert human["input"]["input"]["value"] == 4
    human_args.input_file = None
    human_args.input = '{"input": {"value": 5}}'
    assert call._request_from_args(human_args)["input"]["input"]["value"] == 5
    human_args.input = None
    assert call._request_from_args(human_args)["input"] == {}
    human_args.source = None
    with pytest.raises(ValueError, match="provide --module or --source"):
        call._request_from_args(human_args)


def test_call_command_emits_success_and_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from slyme.cli.command import call

    result_file = tmp_path / "call.json"
    request = {
        "mode": "source",
        "source": PIPELINE_SOURCE,
        "input": {"input": {"value": 3}},
    }
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request)))
    args = SimpleNamespace(envelope=True, result_file=str(result_file))
    assert call.run(args) == 0
    result = json.loads(result_file.read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["result"] == 6
    assert "pipeline output" in result["stdout"]

    monkeypatch.setattr(sys, "stdin", io.StringIO("{"))
    call.run(args)
    assert json.loads(result_file.read_text(encoding="utf-8"))["ok"] is False
    monkeypatch.setattr(sys, "stdin", io.StringIO("{}"))
    call.run(args)
    assert json.loads(result_file.read_text(encoding="utf-8"))["ok"] is False


def test_discover_command_scans_without_importing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from slyme.cli.command import discover

    package = tmp_path / "flows"
    package.mkdir()
    (package / "__init__.py").write_text('"""Flow docs."""\n', encoding="utf-8")
    (package / "pipeline.py").write_text(
        '''"""Pipeline docs."""
from elsewhere import builder

@builder()
def build():
    """Build docs."""
    return (R.input.value, Ref("output.value"))

@decorators.builder
async def async_build():
    return R.other
''',
        encoding="utf-8",
    )
    (package / "broken.py").write_text("not valid python !", encoding="utf-8")
    hidden = package / "_hidden"
    hidden.mkdir()
    (hidden / "ignored.py").write_text("@builder\ndef build(): pass", encoding="utf-8")

    folders, workflows = discover._discover_dir(str(tmp_path))
    assert folders == [{"path": "flows", "doc": "Flow docs."}]
    assert [item["name"] for item in workflows] == [
        "flows.pipeline.build",
        "flows.pipeline.async_build",
    ]
    assert workflows[0]["refs"] == ["input.value", "output.value"]
    assert discover._discover_dir(str(tmp_path / "missing")) == ([], [])
    assert discover._read_doc(str(package / "broken.py")) == ""
    deduped = discover._discover([str(tmp_path), str(tmp_path)])
    assert len(deduped["workflows"]) == 2

    args = SimpleNamespace(path=[str(tmp_path)], paths=[], result_file=None)
    assert discover.run(args) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_nodes_and_info_commands(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from slyme.cli.command import info, nodes

    assert nodes._jsonable((1, object()))[0] == 1
    serialized = nodes._serialize_arg(
        Arg(
            default={"x": object()},
            help="help",
            type=int,
            choices=[1, 2],
            required=True,
            nargs="+",
            aliases=["-x"],
            metavar="X",
        )
    )
    assert serialized["type"] == "int"
    assert serialized["required"] is True

    with source_module(PIPELINE_SOURCE) as module:
        graph = get_builder(module, "build")
        result = nodes._introspect(graph, "build")
    assert result["entry"] == "build"
    assert result["inputs"] == ["input.value"]
    assert result["outputs"] == ["output.value"]
    assert result["nodes"][0]["keyPath"] == "root"

    args = SimpleNamespace(
        source=PIPELINE_SOURCE,
        module=None,
        entry="build",
        sys_path=None,
        result_file=str(tmp_path / "nodes.json"),
    )
    assert nodes.run(args) == 0
    assert json.loads((tmp_path / "nodes.json").read_text())["ok"] is True
    args.source = None
    nodes.run(args)
    assert json.loads((tmp_path / "nodes.json").read_text())["ok"] is False

    info_args = SimpleNamespace(result_file=None)
    assert info.run(info_args) == 0
    info_result = json.loads(capsys.readouterr().out)
    assert info_result["kind"] == "info"
    assert info_result["result"]["importable"] is True


def test_main_dispatch_version_and_last_resort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    main_module = importlib.import_module("slyme.__main__")
    from slyme.cli.command import info

    warning_once.cache_clear()
    with pytest.warns(FutureWarning, match="experimental prototypes"):
        warn_experimental_runner()
    assert EXPERIMENTAL_RUNNER_WARNING.startswith("slyme.runner")

    assert main_module.main(["info"]) == 0
    assert json.loads(capsys.readouterr().out)["kind"] == "info"
    with pytest.raises(SystemExit) as version_exit:
        main_module.main(["--version"])
    assert version_exit.value.code == 0

    monkeypatch.setattr(
        info, "run", lambda _args: (_ for _ in ()).throw(RuntimeError("bad"))
    )
    result_file = tmp_path / "main-error.json"
    assert main_module.main(["info", "--result-file", str(result_file)]) == 0
    assert json.loads(result_file.read_text())["ok"] is False

    monkeypatch.setattr(
        info,
        "run",
        lambda _args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert main_module.main(["info"]) == 130
