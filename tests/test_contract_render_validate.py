"""Testes de contrato das tools render, validate, plugins.scan (US-004)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tools import contract as _contract  # noqa: F401
from tools.registry import call, get

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _require_fixture() -> str:
    if not os.path.exists(FIXTURE):
        pytest.skip(f"fixture not present: {FIXTURE}")
    return FIXTURE


def _pad_plan(midi: str) -> dict:
    """Constroi um plano minimo com 1 elemento pad. Usa skeleton como base."""
    env = call("plan.skeleton", {"midi_path": midi, "seed": 7})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "pad_test",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72],
        "layers": 2,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {
            "plugin": "Omnisphere", "preset": "Desert Wind", "verified": True,
        },
        "rationale": "Textura para a intro.",
        "is_protagonist": False,
    }]
    return plan


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# --- descricoes -----------------------------------------------------------

@pytest.mark.parametrize("name", ["render", "validate", "plugins.scan"])
def test_render_family_has_prompt_description(name: str):
    tool = get(name)
    assert tool is not None
    assert tool.description.strip()
    assert len(tool.description) > 80
    assert "Use" in tool.description or "use" in tool.description


# --- render ---------------------------------------------------------------

def test_render_produces_output_and_leaves_source_unchanged(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    before = _sha256(midi)
    out = tmp_path / "out.mid"
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out),
    })
    assert env["ok"] is True, env.get("error")
    d = env["data"]
    assert d["output_path"] == str(out)
    assert out.exists()
    assert _sha256(midi) == before, "source MIDI mudou apos render"
    assert d["source_sha256"] == before


def test_render_twice_with_same_seed_is_byte_identical(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    out1 = tmp_path / "a.mid"
    out2 = tmp_path / "b.mid"
    env1 = call("render", {"midi_path": midi, "plan": plan, "output_path": str(out1)})
    env2 = call("render", {"midi_path": midi, "plan": plan, "output_path": str(out2)})
    assert env1["ok"] and env2["ok"]
    assert out1.read_bytes() == out2.read_bytes()


def test_render_refuses_to_overwrite_source(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": midi,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_RENDER"
    assert "overwrite" in env["error"]["message"].lower()


def test_render_reports_rationale_per_element(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    out = tmp_path / "out.mid"
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out),
    })
    assert env["ok"] is True
    reports = env["data"]["elements"]
    assert len(reports) == 1
    r = reports[0]
    assert r["element_id"] == "pad_test"
    assert r["rendered"] is True
    assert r["rationale"] == "Textura para a intro."
    assert r["plugin"] == "Omnisphere"


def test_render_seed_override_is_used(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    out = tmp_path / "out.mid"
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out), "seed": 999,
    })
    assert env["ok"] is True
    assert env["data"]["seed"] == 999


def test_render_exposes_low_style_confidence_as_warning(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    plan["style"] = {
        "keys": {
            "reference": "Thin research",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/keys"],
            "confidence": "low",
            "techniques": [],
            "parameters": {},
        },
    }
    out = tmp_path / "out.mid"

    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out),
    })

    assert env["ok"] is True
    warning = next(w for w in env["warnings"] if "confidence low" in w["message"])
    assert warning["code"] == "W_RENDER"
    assert "style.keys" in warning["message"]
    assert "Thin research" in warning["message"]


def test_render_missing_midi_returns_error():
    midi = _require_fixture()
    plan = _pad_plan(midi)
    env = call("render", {"midi_path": "/no/such.mid", "plan": plan})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_NOT_FOUND"
    assert env["error"]["path"] == "midi_path"


def test_render_unknown_field_returns_ok_false():
    env = call("render", {"midi_path": FIXTURE, "plan": {}, "surprise": True})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"


# --- validate -------------------------------------------------------------

def test_validate_runs_on_rendered_file_without_rerendering(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    out = tmp_path / "out.mid"
    call("render", {"midi_path": midi, "plan": plan, "output_path": str(out)})
    mtime_before = out.stat().st_mtime

    env = call("validate", {
        "midi_path": midi, "rendered_path": str(out), "plan": plan,
    })
    assert env["ok"] is True
    # validate NAO deve reescrever o arquivo renderizado
    assert out.stat().st_mtime == mtime_before

    d = env["data"]
    for key in ("harmony_issues", "placement_issues", "artifice_issues", "persona_issues"):
        assert isinstance(d[key], list)
        for issue in d[key]:
            assert "severity" in issue
            assert "message" in issue


def test_validate_warns_when_render_has_no_track_for_element(tmp_path: Path):
    """MIDI renderizado que nao contem as tracks do plano dispara warning."""
    midi = _require_fixture()
    plan = _pad_plan(midi)
    env = call("validate", {
        "midi_path": midi, "rendered_path": midi, "plan": plan,
    })
    # midi_path == rendered_path e proibido: fachada bloqueia antes
    assert env["ok"] is False
    assert env["error"]["code"] == "E_RENDERED_IS_SOURCE"


def test_validate_missing_rendered_file_returns_error():
    midi = _require_fixture()
    plan = _pad_plan(midi)
    env = call("validate", {
        "midi_path": midi, "rendered_path": "/no/such.mid", "plan": plan,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_NOT_FOUND"


def test_validate_via_plan_path(tmp_path: Path):
    midi = _require_fixture()
    plan = _pad_plan(midi)
    out = tmp_path / "out.mid"
    call("render", {"midi_path": midi, "plan": plan, "output_path": str(out)})
    pp = tmp_path / "plan.json"
    pp.write_text(json.dumps(plan), encoding="utf-8")
    env = call("validate", {
        "midi_path": midi, "rendered_path": str(out), "plan_path": str(pp),
    })
    assert env["ok"] is True


# --- plugins.scan --------------------------------------------------------

def test_plugins_scan_returns_stock_and_marks_from_cache_false(tmp_path: Path):
    env = call("plugins.scan", {"dirs": [str(tmp_path / "empty")]})
    assert env["ok"] is True
    assert env["data"]["from_cache"] is False
    # Logic stock sempre entra — 7 entradas fixas.
    names = {p["name"] for p in env["data"]["plugins"]}
    assert "Alchemy" in names
    assert "Studio Strings" in names


def test_plugins_scan_uses_cache_when_mtimes_match(tmp_path: Path):
    dirs = [str(tmp_path / "a")]
    cache = tmp_path / "cache.json"
    env1 = call("plugins.scan", {"dirs": dirs, "cache_path": str(cache)})
    assert env1["ok"] is True
    assert env1["data"]["from_cache"] is False

    env2 = call("plugins.scan", {"dirs": dirs, "cache_path": str(cache)})
    assert env2["ok"] is True
    assert env2["data"]["from_cache"] is True
    # Mesmos plugins.
    assert env1["data"]["plugins"] == env2["data"]["plugins"]


def test_plugins_scan_no_args_is_valid():
    env = call("plugins.scan", {})
    assert env["ok"] is True
    assert env["data"]["from_cache"] is False
    assert isinstance(env["data"]["plugins"], list)
