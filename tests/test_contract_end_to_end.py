"""US-007: ponta-a-ponta pela CLI de tool, sem LLM nenhum.

Prova de que o maquinario inteiro roda como comandos determinsticos,
respeitando o contrato de envelope, sobre a fixture versionada.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

import pytest

from tools import cli  # noqa: F401
from tools.registry import list_tools, validate_output

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _require_fixture() -> str:
    if not os.path.exists(FIXTURE):
        pytest.skip(f"fixture not present: {FIXTURE}")
    return FIXTURE


def _run_cli(argv: list[str], stdin: str = "") -> tuple[int, dict]:
    old_stdin, old_stdout = sys.stdin, sys.stdout
    sys.stdin = io.StringIO(stdin)
    sys.stdout = io.StringIO()
    try:
        exit_code = cli.main(argv)
        raw = sys.stdout.getvalue()
    finally:
        sys.stdin, sys.stdout = old_stdin, old_stdout
    return exit_code, json.loads(raw) if raw.strip() else {}


def _tool_input(tmp_path: Path, name: str, payload: dict) -> Path:
    p = tmp_path / f"{name}.in.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _pad_plan(midi: str) -> dict:
    """Plano minimo com um elemento pad — mesmo helper do teste de render."""
    from tools.registry import call
    env = call("plan.skeleton", {"midi_path": midi, "seed": 12})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "pad_e2e",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72],
        "layers": 2,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "Desert Wind", "verified": True},
        "rationale": "textura",
        "is_protagonist": False,
    }]
    return plan


def test_end_to_end_pipeline_via_cli(tmp_path: Path):
    """Pipeline completo pela CLI: analyze -> plan.skeleton -> plan.validate ->
    render -> validate. Envelope de cada passo precisa dar ok=true."""
    midi = _require_fixture()

    # 1. analyze
    inp = _tool_input(tmp_path, "analyze", {"midi_path": midi})
    exit_code, env = _run_cli(["tool", "analyze", "--input", str(inp)])
    assert exit_code == 0
    assert env["ok"] is True

    # 2. plan.skeleton (grava o plano)
    plan_out = tmp_path / "plan.json"
    inp = _tool_input(tmp_path, "plan.skeleton", {
        "midi_path": midi, "output_path": str(plan_out), "seed": 42,
    })
    exit_code, env = _run_cli(["tool", "plan.skeleton", "--input", str(inp)])
    assert exit_code == 0
    assert env["ok"] is True
    assert plan_out.exists()

    # 3. sobrescreve o plano com um elemento pad — para o render ter o que fazer
    plan_full = _pad_plan(midi)
    plan_out.write_text(json.dumps(plan_full), encoding="utf-8")

    # 4. plan.validate — le do arquivo
    inp = _tool_input(tmp_path, "plan.validate", {
        "plan_path": str(plan_out), "midi_path": midi,
    })
    exit_code, env = _run_cli(["tool", "plan.validate", "--input", str(inp)])
    assert exit_code == 0
    assert env["ok"] is True
    assert env["data"]["valid"] is True

    # 5. render — le o plano do arquivo, escreve o MIDI resultante
    rendered_out = tmp_path / "out.mid"
    inp = _tool_input(tmp_path, "render", {
        "midi_path": midi,
        "plan_path": str(plan_out),
        "output_path": str(rendered_out),
    })
    exit_code, env = _run_cli(["tool", "render", "--input", str(inp)])
    assert exit_code == 0
    assert env["ok"] is True
    assert rendered_out.exists()

    # 6. validate — auditar o renderizado
    inp = _tool_input(tmp_path, "validate", {
        "midi_path": midi,
        "rendered_path": str(rendered_out),
        "plan_path": str(plan_out),
    })
    exit_code, env = _run_cli(["tool", "validate", "--input", str(inp)])
    assert exit_code == 0
    assert env["ok"] is True


def test_every_registered_tool_has_valid_declaration():
    """Cada tool: descricao nao vazia, schemas dict, e passa uma validacao
    trivial contra si mesma quando aplicavel."""
    decls = list_tools()
    assert decls, "registry vazio — bootstrap nao rodou"
    for d in decls:
        assert d["description"].strip(), f"{d['name']}: descricao vazia"
        assert isinstance(d["input_schema"], dict), f"{d['name']}: input_schema nao e dict"
        assert isinstance(d["output_schema"], dict), f"{d['name']}: output_schema nao e dict"


def test_every_tool_declaration_output_shape_matches_registry_shape():
    """Sanity: cada declaracao devolvida pelo registry tem sempre as mesmas
    chaves — protege consumidores que iteram sobre list_tools()."""
    for d in list_tools():
        assert set(d) == {"name", "description", "input_schema", "output_schema"}


def test_list_tools_cli_lists_every_registered_tool(capsys):
    """`--list` mostra o nome de cada tool registrada."""
    exit_code = cli.main(["--list"])
    assert exit_code == 0
    out = capsys.readouterr().out
    for d in list_tools():
        assert d["name"] in out


def test_schema_cli_prints_valid_schemas_for_each_tool():
    for d in list_tools():
        exit_code, env = _run_cli(["--schema", d["name"]])
        assert exit_code == 0
        assert env["ok"] is True
        assert env["data"]["name"] == d["name"]
        assert env["data"]["input_schema"] == d["input_schema"]
        assert env["data"]["output_schema"] == d["output_schema"]


def test_output_of_analyze_validates_against_its_own_schema():
    """A validacao ja roda no registry.call, mas explicitamos para o teste
    documentar a promessa do contrato."""
    from tools.registry import call, get
    midi = _require_fixture()
    env = call("analyze", {"midi_path": midi})
    assert env["ok"] is True
    tool = get("analyze")
    validate_output(env["data"], tool.output_schema)
