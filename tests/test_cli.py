"""Testes da CLI de invocacao de tools (US-002)."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from tools import cli, registry
from tools.registry import Tool


def _echo_tool() -> Tool:
    return Tool(
        name="sample.echo",
        description="Ecoa {value} de volta. Existe para teste do CLI.",
        input_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
        },
        func=lambda p: {"value": p["value"]},
    )


@pytest.fixture(autouse=True)
def clean_registry():
    snap = registry.snapshot()
    registry._REGISTRY.clear()
    try:
        yield
    finally:
        registry.restore(snap)


def _run(argv: list[str], stdin: str = "") -> tuple[int, dict, str]:
    """Roda `cli.main(argv)` capturando stdout e stdin. Devolve (exit, json_out, raw_out)."""
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(stdin)
    sys.stdout = io.StringIO()
    try:
        exit_code = cli.main(argv)
        raw = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {}
    return exit_code, parsed, raw


# --- --list ----------------------------------------------------------------

def test_list_prints_each_registered_tool_with_description(capsys):
    registry.register(_echo_tool())
    exit_code = cli.main(["--list"])
    out = capsys.readouterr().out
    assert exit_code == 0
    assert "sample.echo" in out
    assert "Ecoa" in out


# --- --schema --------------------------------------------------------------

def test_schema_prints_input_and_output_schemas():
    registry.register(_echo_tool())
    exit_code, env, _ = _run(["--schema", "sample.echo"])
    assert exit_code == 0
    assert env["ok"] is True
    assert env["data"]["name"] == "sample.echo"
    assert env["data"]["input_schema"]["required"] == ["value"]
    assert env["data"]["output_schema"]["required"] == ["value"]


def test_schema_on_unknown_tool_returns_error_envelope():
    exit_code, env, _ = _run(["--schema", "nope.tool"])
    assert exit_code == 2
    assert env["ok"] is False
    assert env["error"]["code"] == "E_TOOL_NOT_FOUND"


# --- tool <nome> --input arquivo ------------------------------------------

def test_tool_call_from_file_returns_ok_and_exit_zero(tmp_path: Path):
    registry.register(_echo_tool())
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"value": 7}), encoding="utf-8")
    exit_code, env, _ = _run(["tool", "sample.echo", "--input", str(inp)])
    assert exit_code == 0
    assert env == {"ok": True, "data": {"value": 7}, "warnings": []}


def test_tool_call_from_stdin_works():
    registry.register(_echo_tool())
    exit_code, env, _ = _run(
        ["tool", "sample.echo", "--input", "-"],
        stdin=json.dumps({"value": 3}),
    )
    assert exit_code == 0
    assert env["data"]["value"] == 3


def test_tool_input_file_missing_returns_error_envelope_no_stack(tmp_path: Path):
    exit_code, env, _ = _run(
        ["tool", "sample.echo", "--input", str(tmp_path / "nope.json")],
    )
    assert exit_code == 2
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_FILE"
    assert "nao encontrado" in env["error"]["message"]


def test_tool_input_file_without_permission_returns_error(tmp_path: Path):
    registry.register(_echo_tool())
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"value": 1}), encoding="utf-8")
    inp.chmod(0o000)
    try:
        exit_code, env, _ = _run(["tool", "sample.echo", "--input", str(inp)])
    finally:
        inp.chmod(0o644)
    assert exit_code == 2
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_FILE"


def test_tool_input_not_json_returns_error_envelope_no_stack(tmp_path: Path):
    registry.register(_echo_tool())
    inp = tmp_path / "in.txt"
    inp.write_text("not json {", encoding="utf-8")
    exit_code, env, _ = _run(["tool", "sample.echo", "--input", str(inp)])
    assert exit_code == 2
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_JSON"


def test_tool_input_json_root_is_not_object_returns_error(tmp_path: Path):
    registry.register(_echo_tool())
    inp = tmp_path / "in.json"
    inp.write_text("[1, 2, 3]", encoding="utf-8")
    exit_code, env, _ = _run(["tool", "sample.echo", "--input", str(inp)])
    assert exit_code == 2
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_JSON"


def test_tool_unknown_field_returns_ok_false_with_path(tmp_path: Path):
    registry.register(_echo_tool())
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"value": 1, "surprise": True}), encoding="utf-8")
    exit_code, env, _ = _run(["tool", "sample.echo", "--input", str(inp)])
    assert exit_code == 1
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "surprise"


def test_tool_call_returns_exit_1_when_tool_returns_ok_false(tmp_path: Path):
    from tools.registry import ToolError

    def raiser(_payload):
        raise ToolError("E_CUSTOM", "falhou", path="value")
    tool = _echo_tool()
    registry.register(Tool(
        name=tool.name, description=tool.description,
        input_schema=tool.input_schema, output_schema=tool.output_schema,
        func=raiser,
    ))
    inp = tmp_path / "in.json"
    inp.write_text(json.dumps({"value": 1}), encoding="utf-8")
    exit_code, env, _ = _run(["tool", "sample.echo", "--input", str(inp)])
    assert exit_code == 1
    assert env["ok"] is False
    assert env["error"]["code"] == "E_CUSTOM"


def test_tool_unknown_returns_error_envelope_exit_1(tmp_path: Path):
    inp = tmp_path / "in.json"
    inp.write_text("{}", encoding="utf-8")
    exit_code, env, _ = _run(["tool", "nope.tool", "--input", str(inp)])
    assert exit_code == 1
    assert env["ok"] is False
    assert env["error"]["code"] == "E_TOOL_NOT_FOUND"


def test_tool_input_path_is_directory_returns_error(tmp_path: Path):
    registry.register(_echo_tool())
    exit_code, env, _ = _run(["tool", "sample.echo", "--input", str(tmp_path)])
    assert exit_code == 2
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_FILE"


def test_missing_input_flag_for_tool_subcommand_calls_parser_error():
    registry.register(_echo_tool())
    with pytest.raises(SystemExit):
        cli.main(["tool", "sample.echo"])


def test_tool_subcommand_without_name_calls_parser_error():
    with pytest.raises(SystemExit):
        cli.main(["tool"])


def test_empty_stdin_becomes_empty_payload():
    def zero_arg(_payload):
        return {"greeting": "oi"}
    registry.register(Tool(
        name="sample.hello",
        description="Retorna oi. Payload vazio, sem entrada.",
        input_schema={"type": "object", "properties": {}, "required": []},
        output_schema={
            "type": "object",
            "properties": {"greeting": {"type": "string"}},
            "required": ["greeting"],
        },
        func=zero_arg,
    ))
    exit_code, env, _ = _run(["tool", "sample.hello", "--input", "-"], stdin="")
    assert exit_code == 0
    assert env["data"]["greeting"] == "oi"
