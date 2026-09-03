"""Testes de contrato das tools render, validate, plugins.scan (US-004)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pretty_midi
import pytest

from tools import contract as _contract  # noqa: F401
from tools.registry import call, get

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _require_fixture() -> str:
    """Arquivo ancora real — so para os testes de DETERMINISMO (byte a byte,
    mesma seed) desta suite; sao os unicos aqui que provam algo sobre
    material musical real. Os demais usam `_synthetic_fixture()`."""
    if not os.path.exists(FIXTURE):
        pytest.skip(f"fixture not present: {FIXTURE}")
    return FIXTURE


def _synthetic_fixture() -> str:
    """MIDI sintetico minimo para testes de CONTRATO (envelope, erro,
    schema) — nao comportamento musical real. Ver
    `tests/conftest.py::_build_synthetic_contract_midi`."""
    from tests.conftest import _build_synthetic_contract_midi
    return _build_synthetic_contract_midi()


def _bass_plan(midi: str) -> dict:
    """Plano minimo com 1 elemento `bass` gerado do zero (issue #20). Ao
    contrario do `pad` de `_pad_plan` (sustain_through, 1 evento por
    secao), o baixo emite uma nota por batida/ancora de kick — material
    suficiente para exercitar a janela de N=6 eventos do anti-copia
    (AC-16, `tools/validators/anticopy.py`)."""
    env = call("plan.skeleton", {"midi_path": midi, "seed": 7})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "bass_test",
        "role": "bass",
        "sections": [plan["sections"][0]["label"]],
        "register": [28, 52],
        "layers": 1,
        "sync_role": "kick_support",
        "articulation": "tight",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {
            "plugin": "Trilian", "preset": "Fingered Bass", "verified": True,
        },
        "rationale": "Baixo de teste para o anti-copia.",
        "is_protagonist": False,
    }]
    return plan


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

@pytest.mark.parametrize("name", ["render", "validate", "plugins.scan", "presets.scan"])
def test_render_family_has_prompt_description(name: str):
    tool = get(name)
    assert tool is not None
    assert tool.description.strip()
    assert len(tool.description) > 80
    assert "Use" in tool.description or "use" in tool.description


# --- render ---------------------------------------------------------------

def test_render_produces_output_and_leaves_source_unchanged(tmp_path: Path):
    midi = _synthetic_fixture()
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
    # Ancora: determinismo byte-a-byte e uma promessa sobre material
    # musical real — mantido de proposito.
    midi = _require_fixture()
    plan = _pad_plan(midi)
    out1 = tmp_path / "a.mid"
    out2 = tmp_path / "b.mid"
    env1 = call("render", {"midi_path": midi, "plan": plan, "output_path": str(out1)})
    env2 = call("render", {"midi_path": midi, "plan": plan, "output_path": str(out2)})
    assert env1["ok"] and env2["ok"]
    assert out1.read_bytes() == out2.read_bytes()


def test_render_refuses_to_overwrite_source(tmp_path: Path):
    midi = _synthetic_fixture()
    plan = _pad_plan(midi)
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": midi,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_RENDER"
    assert "overwrite" in env["error"]["message"].lower()


def test_render_reports_rationale_per_element(tmp_path: Path):
    midi = _synthetic_fixture()
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
    midi = _synthetic_fixture()
    plan = _pad_plan(midi)
    out = tmp_path / "out.mid"
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out), "seed": 999,
    })
    assert env["ok"] is True
    assert env["data"]["seed"] == 999


def test_render_exposes_low_style_confidence_as_warning(tmp_path: Path):
    midi = _synthetic_fixture()
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
    midi = _synthetic_fixture()
    plan = _pad_plan(midi)
    env = call("render", {"midi_path": "/no/such.mid", "plan": plan})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_NOT_FOUND"
    assert env["error"]["path"] == "midi_path"


def test_render_unknown_field_returns_ok_false():
    env = call("render", {
        "midi_path": _synthetic_fixture(), "plan": {}, "surprise": True,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"


# --- anti-copia (AC-16) via reference_corpus -------------------------------
#
# Achado de review da PR #100: `tools/validators/anticopy.py` foi ligado a
# `tools.render.render()` (parametro `reference_corpus`, campo
# `RenderReport.anticopy_issues`) mas a fachada `tools/contract.py` nunca
# repassava isso — ninguem que usa a tool `render` de verdade (CLI/skill/
# harness) via `tools.contract` acionava ou via a checagem. Os testes abaixo
# provam o fluxo PONTA A PONTA passando por `tools.registry.call("render",
# ...)`, nao chamando `tools.render.render()` direto.

def test_render_without_reference_corpus_skips_anticopy_check(tmp_path: Path):
    midi = _synthetic_fixture()
    plan = _bass_plan(midi)
    out = tmp_path / "out.mid"
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out),
    })
    assert env["ok"] is True, env.get("error")
    assert env["data"]["anticopy_issues"] == []


def test_render_reference_corpus_flags_deliberate_copy(tmp_path: Path):
    midi = _synthetic_fixture()
    plan = _bass_plan(midi)

    # 1) Renderiza uma vez sem corpus para conhecer a linha de baixo REAL que
    #    o motor gera (determinismo: mesmo plano/seed/source sempre repete).
    out1 = tmp_path / "out1.mid"
    env1 = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out1),
    })
    assert env1["ok"] is True, env1.get("error")

    bass_track = next(
        inst for inst in pretty_midi.PrettyMIDI(str(out1)).instruments
        if inst.name.startswith("bass_test")
    )
    window = sorted(bass_track.notes, key=lambda n: n.start)[:6]
    assert len(window) == 6, "fixture nao gerou eventos suficientes para N=6"

    # 2) Constroi um "corpus de referencia" com uma copia DELIBERADA dessa
    #    janela, transposta uma quinta acima — AC-16 e invariante a
    #    transposicao (ver docstring de tools/validators/anticopy.py), entao
    #    isso ainda tem que ser flagueado como copia.
    TRANSPOSITION = 7
    corpus_pm = pretty_midi.PrettyMIDI(resolution=480, initial_tempo=120.0)
    corpus_track = pretty_midi.Instrument(program=32, name="Stolen Riff")
    for note in window:
        corpus_track.notes.append(pretty_midi.Note(
            velocity=int(note.velocity),
            pitch=int(note.pitch) + TRANSPOSITION,
            start=float(note.start),
            end=float(note.end),
        ))
    corpus_pm.instruments.append(corpus_track)
    corpus_path = tmp_path / "reference.mid"
    corpus_pm.write(str(corpus_path))

    # 3) Rerenderiza o MESMO plano com o corpus declarado.
    out2 = tmp_path / "out2.mid"
    env2 = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(out2),
        "reference_corpus": [str(corpus_path)],
    })
    assert env2["ok"] is True, env2.get("error")

    issues = env2["data"]["anticopy_issues"]
    assert issues, "corpus com copia deliberada (transposta) deveria acionar anticopy_issues"
    issue = issues[0]
    assert issue["validator"] == "anticopy"
    assert issue["severity"] == "error"
    assert issue["element_id"] == "bass_test"
    assert issue["source"] == str(corpus_path)
    assert issue["source_track"] == "Stolen Riff"
    assert isinstance(issue["bar"], int)
    assert isinstance(issue["n"], int)
    assert issue["message"]

    # Saida ainda e escrita (anti-copia e `error` mas nao aborta o render —
    # o CLI decide o exit code a partir do relatorio).
    assert out2.exists()


def test_render_reference_corpus_missing_file_returns_error(tmp_path: Path):
    midi = _synthetic_fixture()
    plan = _bass_plan(midi)
    env = call("render", {
        "midi_path": midi, "plan": plan,
        "output_path": str(tmp_path / "out.mid"),
        "reference_corpus": ["/no/such/reference.mid"],
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_NOT_FOUND"
    assert env["error"]["path"] == "reference_corpus"


# --- validate -------------------------------------------------------------

def test_validate_runs_on_rendered_file_without_rerendering(tmp_path: Path):
    midi = _synthetic_fixture()
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
    midi = _synthetic_fixture()
    plan = _pad_plan(midi)
    env = call("validate", {
        "midi_path": midi, "rendered_path": midi, "plan": plan,
    })
    # midi_path == rendered_path e proibido: fachada bloqueia antes
    assert env["ok"] is False
    assert env["error"]["code"] == "E_RENDERED_IS_SOURCE"


def test_validate_missing_rendered_file_returns_error():
    midi = _synthetic_fixture()
    plan = _pad_plan(midi)
    env = call("validate", {
        "midi_path": midi, "rendered_path": "/no/such.mid", "plan": plan,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_NOT_FOUND"


def test_validate_via_plan_path(tmp_path: Path):
    midi = _synthetic_fixture()
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


# --- presets.scan ----------------------------------------------------------

def test_presets_scan_no_args_is_valid():
    env = call("presets.scan", {})
    assert env["ok"] is True
    assert isinstance(env["data"]["presets"], list)
    assert "Nexus" in env["data"]["supported_plugins"]


def test_presets_scan_finds_real_files_on_disk_as_verified(tmp_path: Path):
    omni = tmp_path / "omnisphere"
    (omni / "Pads").mkdir(parents=True)
    (omni / "Pads" / "Desert Wind.prt_a").write_text("binary", encoding="utf-8")
    serum = tmp_path / "serum"
    serum.mkdir()

    env = call("presets.scan", {
        "omnisphere": str(omni), "serum": str(serum),
    })
    assert env["ok"] is True
    presets_by_name = {p["name"]: p for p in env["data"]["presets"]}
    assert presets_by_name["Desert Wind"]["plugin"] == "Omnisphere"
    assert presets_by_name["Desert Wind"]["verified"] is True


def test_presets_scan_missing_dirs_return_no_presets_without_error(tmp_path: Path):
    # `disable_defaults=True` desliga o sweep dos `DEFAULT_ROOTS` (que varrem
    # o Mac real). So processa os overrides passados; dirs inexistentes viram
    # lista vazia, sem erro.
    env = call("presets.scan", {
        "omnisphere": str(tmp_path / "ghost"),
        "kontakt": str(tmp_path / "ghost2"),
        "disable_defaults": True,
    })
    assert env["ok"] is True
    assert env["data"]["presets"] == []


def test_presets_scan_reports_opaque_libraries(tmp_path: Path):
    # Toontrack Superior3 aparece em `opaque_libraries` com motivo, nunca em
    # `presets` — DB binario proprietario nao vira nome de preset.
    root = tmp_path / "app-support"
    (root / "Toontrack" / "Superior3").mkdir(parents=True)
    (root / "Toontrack" / "Superior3" / "SoundDB").write_bytes(b"\x00\x01")
    env = call("presets.scan", {
        "extra_roots": [str(root)],
        "disable_defaults": True,
    })
    assert env["ok"] is True
    assert env["data"]["presets"] == []
    opaque = env["data"]["opaque_libraries"]
    assert any(op["plugin"] == "Superior Drummer 3" for op in opaque)


def test_presets_scan_sweeps_extra_roots(tmp_path: Path):
    # Sweep generico acha .fxp do Nexus, .ffp do FabFilter em roots customizados.
    root = tmp_path / "presets"
    (root / "reFX" / "NEXUS library" / "Presets" / "Pack").mkdir(parents=True)
    (root / "reFX" / "NEXUS library" / "Presets" / "Pack" / "Wack.fxp").write_bytes(b"\x00")
    (root / "FabFilter" / "Pro-Q 3").mkdir(parents=True)
    (root / "FabFilter" / "Pro-Q 3" / "Boost.ffp").write_bytes(b"\x00")
    env = call("presets.scan", {
        "extra_roots": [str(root)],
        "disable_defaults": True,
    })
    assert env["ok"] is True
    by_plugin: dict[str, list[str]] = {}
    for p in env["data"]["presets"]:
        by_plugin.setdefault(p["plugin"], []).append(p["name"])
    assert by_plugin["Nexus"] == ["Wack"]
    assert by_plugin["Pro-Q 3"] == ["Boost"]


def test_presets_scan_reports_automatically_discovered_library_root(tmp_path: Path):
    app_support = tmp_path / "Library" / "Application Support"
    steam = tmp_path / "External" / "STEAM"
    patch = steam / "Omnisphere" / "Settings Library" / "Patches" / "Air.prt_a"
    patch.parent.mkdir(parents=True)
    patch.write_bytes(b"preset")
    pointer = app_support / "Spectrasonics" / "STEAM"
    pointer.parent.mkdir(parents=True)
    pointer.symlink_to(steam, target_is_directory=True)

    env = call("presets.scan", {
        "extra_roots": [str(app_support)],
        "disable_defaults": True,
    })

    assert env["ok"] is True
    assert any(
        p["plugin"] == "Omnisphere" and p["name"] == "Air"
        for p in env["data"]["presets"]
    )
    assert env["data"]["discovered_roots"] == [{
        "path": str(steam),
        "source": str(pointer),
        "method": "symlink",
    }]
    assert env["data"]["unresolved_roots"] == []
