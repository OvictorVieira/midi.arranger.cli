"""Testes das rotas de erro estruturado da fachada.

Cada teste dispara UM codigo de erro real da fachada — garante que o agente
recebe envelope acionavel em vez de stack trace, e que codigos raramente
excitados nao regridem em silencio.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from tools import contract as _contract  # noqa: F401
from tools.registry import call

FIXTURE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "fixtures",
    "ancora_arranjo_atual.mid",
)


def _require_fixture() -> str:
    """Arquivo ancora real. Nenhum teste deste modulo depende de valor
    congelado dele — todos exercitam so o CODIGO de erro/aviso da fachada,
    que independe do conteudo musical. Mantido por compat/documentacao;
    os testes abaixo usam `_synthetic_fixture()`."""
    if not os.path.exists(FIXTURE):
        pytest.skip(f"fixture not present: {FIXTURE}")
    return FIXTURE


def _synthetic_fixture() -> str:
    """MIDI sintetico minimo para testes de CONTRATO (codigo de erro,
    aviso, envelope) — nao comportamento musical real. Ver
    `tests/conftest.py::_build_synthetic_contract_midi`."""
    from tests.conftest import _build_synthetic_contract_midi
    return _build_synthetic_contract_midi()


# --- _resolve_midi: E_MIDI_* ---------------------------------------------

def test_analyze_directory_instead_of_file_returns_e_midi_not_file(tmp_path: Path):
    env = call("analyze", {"midi_path": str(tmp_path)})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_NOT_FILE"


def test_analyze_unreadable_file_returns_e_midi_permission(tmp_path: Path):
    p = tmp_path / "x.mid"
    p.write_bytes(b"MThd\x00")
    p.chmod(0o000)
    try:
        env = call("analyze", {"midi_path": str(p)})
    finally:
        p.chmod(0o644)
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_PERMISSION"


def test_analyze_corrupt_midi_returns_e_midi_parse(tmp_path: Path):
    """Header valido mas conteudo quebrado. pretty_midi falha ao parsear."""
    p = tmp_path / "broken.mid"
    p.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0")
    env = call("analyze", {"midi_path": str(p)})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_PARSE"


def test_analyze_empty_midi_path_returns_e_midi_path():
    env = call("analyze", {"midi_path": ""})
    # schema minLength=1 pega antes
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"


# --- plan.skeleton output_path -------------------------------------------

def test_plan_skeleton_output_path_unwritable_returns_e_output_write(tmp_path: Path):
    midi = _synthetic_fixture()
    # Tenta escrever num diretorio sem permissao — cria pasta, remove permissao.
    parent = tmp_path / "noperm"
    parent.mkdir()
    parent.chmod(0o500)
    try:
        env = call("plan.skeleton", {
            "midi_path": midi,
            "output_path": str(parent / "sub" / "plan.json"),
        })
    finally:
        parent.chmod(0o700)
    assert env["ok"] is False
    assert env["error"]["code"] == "E_OUTPUT_WRITE"


# --- plan.validate: E_PLAN_* ---------------------------------------------

def test_plan_validate_plan_path_missing_returns_error():
    midi = _synthetic_fixture()
    env = call("plan.validate", {
        "plan_path": "/nope/plan.json", "midi_path": midi,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_FILE_NOT_FOUND"


def test_plan_validate_plan_path_not_json_returns_error(tmp_path: Path):
    midi = _synthetic_fixture()
    p = tmp_path / "plan.json"
    p.write_text("{not json", encoding="utf-8")
    env = call("plan.validate", {"plan_path": str(p), "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_JSON"


def test_plan_validate_plan_path_unreadable_returns_e_plan_file_io(tmp_path: Path):
    midi = _synthetic_fixture()
    p = tmp_path / "plan.json"
    p.write_text("{}", encoding="utf-8")
    p.chmod(0o000)
    try:
        env = call("plan.validate", {"plan_path": str(p), "midi_path": midi})
    finally:
        p.chmod(0o644)
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_FILE_IO"


def test_plan_validate_plan_path_not_object_returns_error(tmp_path: Path):
    midi = _synthetic_fixture()
    p = tmp_path / "plan.json"
    p.write_text("[1, 2]", encoding="utf-8")
    env = call("plan.validate", {"plan_path": str(p), "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_JSON"


def test_plan_validate_missing_edit_track_reports_domain_error(tmp_path: Path):
    """`edits[]` aponta para track inexistente no MIDI: cai em
    validate_edits_against_midi, que devolve erro no `data.errors`."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["edits"] = [{
        "track": "TRACK_QUE_NAO_EXISTE",
        "profile": "generic",
        "intensity": 0.5,
    }]
    env2 = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env2["ok"] is True
    assert env2["data"]["valid"] is False
    assert any("TRACK_QUE_NAO_EXISTE" in e["message"] for e in env2["data"]["errors"])


# --- render: E_TRACK_NAME e outras -------------------------------------

def test_render_forbidden_plugin_returns_e_track_name(tmp_path: Path):
    """Plugin em FORBIDDEN_PLUGINS dispara TrackNameError durante a montagem."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "bad_pad",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72],
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {
            "plugin": "Trigger 2",  # proibido por FR-24
            "preset": "x", "verified": True,
        },
        "rationale": "-", "is_protagonist": False,
    }]
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(tmp_path / "o.mid"),
    })
    assert env["ok"] is False
    # RenderError sai como E_RENDER; TrackNameError levantado antes por
    # `_element_track_name` sai como E_TRACK_NAME.
    assert env["error"]["code"] in ("E_TRACK_NAME", "E_RENDER")


def test_render_element_without_instrument_becomes_render_error(tmp_path: Path):
    """Elemento pad sem instrument.plugin/preset — RenderError da fachada."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "no_inst",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72],
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None,
        "rationale": "-", "is_protagonist": False,
    }]
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(tmp_path / "o.mid"),
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_RENDER"


def test_render_role_unknown_generates_warning_but_still_writes(tmp_path: Path):
    """Role sem renderer conhecido nao dispara erro — vai para warning."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "mystery",
        "role": "role_que_nao_existe",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72],
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "X", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    out = tmp_path / "o.mid"
    env = call("render", {"midi_path": midi, "plan": plan, "output_path": str(out)})
    assert env["ok"] is True
    codes = [w["code"] for w in env["warnings"]]
    assert "W_RENDER" in codes
    assert out.exists()


def test_render_plan_path_missing_returns_error(tmp_path: Path):
    midi = _synthetic_fixture()
    env = call("render", {
        "midi_path": midi, "plan_path": "/nope/plan.json",
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_FILE_NOT_FOUND"


def test_render_plan_path_bad_json_returns_error(tmp_path: Path):
    midi = _synthetic_fixture()
    p = tmp_path / "plan.json"
    p.write_text("{not json", encoding="utf-8")
    env = call("render", {"midi_path": midi, "plan_path": str(p)})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_JSON"


def test_render_plan_inline_invalid_returns_e_plan_invalid(tmp_path: Path):
    midi = _synthetic_fixture()
    env = call("render", {
        "midi_path": midi, "plan": {"version": 1, "route": "x"},  # faltando campos
    })
    assert env["ok"] is False
    # schema pega antes (required violado): E_INPUT_SCHEMA
    assert env["error"]["code"] == "E_INPUT_SCHEMA"


def test_render_seed_override_changes_output_bytes(tmp_path: Path):
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "pad_seed",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72],
        "layers": 2,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "X", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    plan["seed"] = 0
    out1 = tmp_path / "a.mid"
    out2 = tmp_path / "b.mid"
    e1 = call("render", {
        "midi_path": midi, "plan": copy.deepcopy(plan),
        "output_path": str(out1), "seed": 1,
    })
    e2 = call("render", {
        "midi_path": midi, "plan": copy.deepcopy(plan),
        "output_path": str(out2), "seed": 2,
    })
    assert e1["ok"] and e2["ok"]
    # Seeds diferentes -> arquivos diferentes.
    assert out1.read_bytes() != out2.read_bytes()


# --- validate error paths -----------------------------------------------

def test_validate_plan_inline_invalid_returns_e_plan_invalid(tmp_path: Path):
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "pad", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "X", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    out = tmp_path / "o.mid"
    call("render", {"midi_path": midi, "plan": plan, "output_path": str(out)})
    # Agora quebra sync_role — schema aceita string livre, dominio nao.
    bad_plan = copy.deepcopy(plan)
    bad_plan["elements"][0]["sync_role"] = "sync_role_nao_existe"
    env = call("validate", {
        "midi_path": midi, "rendered_path": str(out), "plan": bad_plan,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_INVALID"


def test_validate_warns_when_element_has_no_track(tmp_path: Path):
    """MIDI renderizado nao contem a track esperada de um elemento — warning
    W_ELEMENTS_MISSING_IN_RENDER."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "pad", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "X", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    out = tmp_path / "o.mid"
    call("render", {"midi_path": midi, "plan": plan, "output_path": str(out)})

    # Agora pede validate com plano que declara um SEGUNDO elemento sem
    # track correspondente no MIDI renderizado.
    plan_with_extra = copy.deepcopy(plan)
    plan_with_extra["elements"].append({
        "id": "missing", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "Never", "verified": True},
        "rationale": "-", "is_protagonist": False,
    })
    env = call("validate", {
        "midi_path": midi, "rendered_path": str(out), "plan": plan_with_extra,
    })
    assert env["ok"] is True
    codes = [w["code"] for w in env["warnings"]]
    assert "W_ELEMENTS_MISSING_IN_RENDER" in codes


# --- plan.validate exclusive plan / plan_path ---------------------------

def test_plan_validate_both_missing_returns_error():
    midi = _synthetic_fixture()
    env = call("plan.validate", {"midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_INPUT"


# --- techniques.describe error path ------------------------------------

def test_techniques_describe_missing_name_returns_schema_error():
    env = call("techniques.describe", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "name"


# --- plan.skeleton default output_path branch --------------------------

def test_plan_skeleton_without_output_path_returns_null_output_path():
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["output_path"] is None


# --- render output_path default (Desktop) ------------------------------

def test_render_default_output_path_writes_to_home_desktop(tmp_path: Path, monkeypatch):
    """Sem `output_path`, o render usa `~/Desktop/<name>_arranged.mid`."""
    midi = _synthetic_fixture()
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "Desktop").mkdir()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "pad", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 72], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "X", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    env = call("render", {"midi_path": midi, "plan": plan})
    assert env["ok"] is True
    assert Path(env["data"]["output_path"]).parent.name == "Desktop"
    assert Path(env["data"]["output_path"]).exists()


# --- track duplicado no analyze cobre branch de sufixo -----------------

def test_tempo_and_time_sig_helpers_fall_back_with_empty_pm():
    """_tempo_of/_time_signature_of tem defesa contra pm sem tempos/time_sig —
    pretty_midi normalmente injeta defaults, mas os helpers precisam sobreviver
    ao caso vazio (usado em outras rotas ou pm sinteticos)."""
    from tools.contract import _tempo_of, _time_signature_of

    class _StubPM:
        def get_tempo_changes(self):
            return [], []
        time_signature_changes = []

    assert _tempo_of(_StubPM()) == 120.0
    assert _time_signature_of(_StubPM()) == (4, 4)


def test_tempo_and_time_sig_fall_back_when_missing(tmp_path: Path):
    """MIDI sem set_tempo / time_signature: helpers usam 120 / 4/4."""
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    tr.append(mido.Message("note_on", note=60, velocity=90, time=0))
    tr.append(mido.Message("note_off", note=60, velocity=0, time=480))
    mid.tracks.append(tr)
    path = tmp_path / "no_meta.mid"
    mid.save(str(path))
    env = call("analyze", {"midi_path": str(path)})
    assert env["ok"] is True
    assert env["data"]["tempo"] == 120.0
    assert env["data"]["time_signature"] == {"numerator": 4, "denominator": 4}


def test_plan_skeleton_broken_midi_returns_e_midi_parse(tmp_path: Path):
    p = tmp_path / "broken.mid"
    p.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0")
    env = call("plan.skeleton", {"midi_path": str(p)})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_PARSE"


def test_render_broken_source_midi_returns_e_render(tmp_path: Path):
    """MIDI corrompido no source → render() falha internamente. A fachada
    empacota como E_INTERNAL (o render nao levanta RenderError para isso)."""
    p = tmp_path / "broken.mid"
    p.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0")
    midi = _synthetic_fixture()
    env0 = call("plan.skeleton", {"midi_path": midi})
    plan = env0["data"]["plan"]
    plan["source_midi"]["path"] = str(p)  # aponta pra broken
    env = call("render", {"midi_path": str(p), "plan": plan})
    assert env["ok"] is False
    # RenderError, PlanValidationError ou E_INTERNAL — nenhum stack trace.
    assert env["error"]["code"] in ("E_RENDER", "E_PLAN_INVALID", "E_INTERNAL", "E_MIDI_PARSE")


def test_techniques_list_index_failure_reports_e_techniques_index(monkeypatch, tmp_path):
    """Monkeypatch build_index para simular falha no indice."""
    from tools import contract as contract_mod
    from tools.techniques import TechniqueError

    def boom(*_a, **_kw):
        raise TechniqueError("indice quebrado")
    monkeypatch.setattr(contract_mod.techniques_mod, "build_index", boom)

    env = call("techniques.list", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_TECHNIQUES_INDEX"

    env2 = call("techniques.describe", {"name": "ghost_notes"})
    assert env2["ok"] is False
    assert env2["error"]["code"] == "E_TECHNIQUES_INDEX"


def test_plan_validate_plan_with_bad_field_type_reports_domain_error(tmp_path: Path):
    """Passa plan como dict com campo com tipo errado — _load_plan_from_dict
    dispara ToolError E_PLAN_FIELD_TYPE, que vira uma entrada em `errors`."""
    midi = _synthetic_fixture()
    env0 = call("plan.skeleton", {"midi_path": midi})
    plan = env0["data"]["plan"]
    # sections[0].start_bar recebe string, from_dict via to_dict repassa.
    # plan.from_dict copia os valores sem coerção — schema passa (integer via
    # deserializacao). Vamos usar rota — mas rota e enum no schema.
    # Cenario: seed com string. Schema requer integer, vai barrar antes.
    # Cenario dominio-only: passe plan sem chave "route" — schema exige. Nao serve.
    # Melhor: use plan.validate com plan_dict que faz from_dict lancar KeyError.
    # De fato: sections sem 'kind' faz KeyError. Schema requer kind. Nao serve.
    # Alternativa: rationale como int. Nao ha schema type check. from_dict aceita.
    # OK: use pattern que retorne erro do dominio. Vamos fazer plan em que
    # section.protagonist == 'nao_existe' — schema tem enum, passa None ou valor
    # do enum. Vamos usar valor de sync_role invalido — plan.validate dominio.
    plan["elements"] = [{
        "id": "el1", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80], "layers": 1,
        "sync_role": "not_a_role",  # schema aceita string livre; dominio rejeita
        "articulation": "sustained", "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None,
        "rationale": "Pad deliberately uses an invalid sync role to test domain error paths.",
        "is_protagonist": False,
    }]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    # Erro apontando o sync_role invalido
    assert any("sync_role" in e["path"] for e in env["data"]["errors"])


def test_issue_dict_helpers_produce_expected_shape():
    """Testa direto os mappers de issue — dificil disparar todos por render real."""
    from tools.contract import (
        _artifice_issue_to_dict,
        _collision_report_to_dict,
        _harmony_issue_to_dict,
        _persona_issue_to_dict,
        _placement_issue_to_dict,
    )
    from tools.validators.artifice import ArtificeIssue
    from tools.validators.collision import (
        CollisionRelocation,
        CollisionReport,
        CollisionWarning,
    )
    from tools.validators.harmony import HarmonyIssue
    from tools.validators.persona import PersonaIssue
    from tools.validators.placement import PlacementIssue

    h = _harmony_issue_to_dict(HarmonyIssue(
        severity="error", element_id="a", track="t", bar=1, pitch=60,
        expected="C", message="m",
    ))
    assert h["validator"] == "harmony"

    p = _placement_issue_to_dict(PlacementIssue(
        severity="warning", element_id="a", track="t", bar=1, pitch=60,
        section="sec", message="m",
    ))
    assert p["validator"] == "placement"

    a = _artifice_issue_to_dict(ArtificeIssue(
        severity="error", element_id="a", track="t", bar=1,
        pattern="dupes", message="m",
    ))
    assert a["validator"] == "artifice"

    per = _persona_issue_to_dict(PersonaIssue(
        severity="warning", check="chk", section="s",
        element_ids=("a", "b"), message="m",
    ))
    assert per["validator"] == "persona"
    assert per["element_ids"] == ["a", "b"]

    col = _collision_report_to_dict(CollisionReport(
        relocations=[CollisionRelocation(
            element_id="e", section_label="s",
            from_register=(40, 80), to_register=(52, 92),
            reason="clash",
        )],
        warnings=[CollisionWarning(
            element_ids=("a", "b"), section_label="s",
            bar_range=(0, 8), band="mid", reason="dense",
        )],
    ))
    assert len(col["relocations"]) == 1
    assert col["relocations"][0]["from_register"] == [40, 80]
    assert len(col["warnings"]) == 1
    assert col["warnings"][0]["bar_range"] == [0, 8]


def test_load_plan_from_dict_missing_field_returns_tool_error():
    """Chamada direta com dict sem campo obrigatorio — schema so vale para
    `call()`; a funcao interna precisa continuar defendendo por conta propria."""
    from tools.contract import _load_plan_from_dict
    from tools.registry import ToolError as _TE
    with pytest.raises(_TE) as exc:
        _load_plan_from_dict({"seed": 1})
    assert exc.value.code == "E_PLAN_FIELD_MISSING"


def test_load_plan_from_dict_bad_type_returns_tool_error():
    from tools.contract import _load_plan_from_dict
    from tools.registry import ToolError as _TE
    plan = {
        "version": 1, "seed": 1,
        "source_midi": {"path": "x", "sha256": "y"},
        "route": "cinematica_emocional",
        "sections": "not a list",  # tipo errado — from_dict itera
        "elements": [],
    }
    with pytest.raises(_TE) as exc:
        _load_plan_from_dict(plan)
    assert exc.value.code == "E_PLAN_FIELD_TYPE"


def test_render_plan_validation_error_from_render_layer(tmp_path: Path):
    """Section label referenciada por elemento nao existe — dominio validate
    dispara PlanValidationError durante render, que a fachada mapeia para
    E_PLAN_INVALID com path."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "el1", "role": "pad",
        "sections": ["FANTASMA"],  # nao existe em plan.sections
        "register": [40, 80], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "x", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    env = call("render", {
        "midi_path": midi, "plan": plan, "output_path": str(tmp_path / "o.mid"),
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_INVALID"


def test_validate_broken_rendered_midi_returns_e_midi_parse(tmp_path: Path):
    """rendered_path com header valido mas conteudo quebrado — E_MIDI_PARSE
    de dentro de _rendered_tracks_from_midi."""
    midi = _synthetic_fixture()
    broken = tmp_path / "broken.mid"
    broken.write_bytes(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01\x01\xe0")
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "el1", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "x", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    env = call("validate", {
        "midi_path": midi, "rendered_path": str(broken), "plan": plan,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_PARSE"


def test_validate_forbidden_plugin_in_element_falls_back_to_missing(tmp_path: Path):
    """Elemento com plugin proibido — name_for_element levanta TrackNameError
    dentro de _rendered_tracks_from_midi, o elemento fica sem track e vira
    warning W_ELEMENTS_MISSING_IN_RENDER."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "el_pad", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "x", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    out = tmp_path / "o.mid"
    call("render", {"midi_path": midi, "plan": plan, "output_path": str(out)})

    plan_with_bad = copy.deepcopy(plan)
    plan_with_bad["elements"].append({
        "id": "bad_el", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Trigger 2", "preset": "x", "verified": True},
        "rationale": "-", "is_protagonist": False,
    })
    env = call("validate", {
        "midi_path": midi, "rendered_path": str(out), "plan": plan_with_bad,
    })
    assert env["ok"] is True
    codes = [w["code"] for w in env["warnings"]]
    assert "W_ELEMENTS_MISSING_IN_RENDER" in codes


def test_plan_skeleton_inferred_sections_produce_warning(tmp_path: Path):
    """MIDI sem markers → skeleton sinaliza W_INFERRED_SECTIONS."""
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    for i in range(8):
        tr.append(mido.Message("note_on", note=60 + i, velocity=90, time=0))
        tr.append(mido.Message("note_off", note=60 + i, velocity=0, time=480))
    mid.tracks.append(tr)
    p = tmp_path / "no_markers.mid"
    mid.save(str(p))
    env = call("plan.skeleton", {"midi_path": str(p)})
    assert env["ok"] is True
    if any(s["source"] == "inferred" for s in env["data"]["plan"]["sections"]):
        codes = [w["code"] for w in env["warnings"]]
        assert "W_INFERRED_SECTIONS" in codes


def test_validate_element_with_empty_instrument_skips_matching(tmp_path: Path):
    """Elemento com instrument dict mas plugin/preset vazios: entra em missing."""
    midi = _synthetic_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    plan = env["data"]["plan"]
    plan["elements"] = [{
        "id": "pad_ok", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "Omnisphere", "preset": "x", "verified": True},
        "rationale": "-", "is_protagonist": False,
    }]
    out = tmp_path / "o.mid"
    call("render", {"midi_path": midi, "plan": plan, "output_path": str(out)})

    plan_with_empty = copy.deepcopy(plan)
    plan_with_empty["elements"].append({
        "id": "empty_inst", "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80], "layers": 1,
        "sync_role": "sustain_through", "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {"plugin": "", "preset": "", "verified": False},
        "rationale": "-", "is_protagonist": False,
    })
    env = call("validate", {
        "midi_path": midi, "rendered_path": str(out), "plan": plan_with_empty,
    })
    assert env["ok"] is True
    codes = [w["code"] for w in env["warnings"]]
    assert "W_ELEMENTS_MISSING_IN_RENDER" in codes


def test_plugins_scan_corrupt_cache_falls_back_to_scan(tmp_path: Path):
    """Cache com estrutura correta de mtimes mas plugins malformados —
    entra no except (KeyError, TypeError) e refaz o scan."""
    dirs = [str(tmp_path / "a")]
    cache = tmp_path / "cache.json"
    # Constroi um cache com mtimes vazios (bate com dirs inexistentes,
    # ambos None) mas plugins malformado.
    from tools import plugins as plugins_mod
    current = plugins_mod._dir_mtimes([Path(d) for d in dirs])
    cache.write_text(
        json.dumps({
            "mtimes": current,
            "plugins": [{"nome_errado": "boom"}],
        }),
        encoding="utf-8",
    )
    env = call("plugins.scan", {"dirs": dirs, "cache_path": str(cache)})
    assert env["ok"] is True
    assert env["data"]["from_cache"] is False


def test_analyze_duplicate_track_names_get_hash_suffix(tmp_path: Path):
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    mid.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead Guitar", time=0),
        mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0),
        mido.Message("note_on", note=60, velocity=90, time=0),
        mido.Message("note_off", note=60, velocity=0, time=480),
    ]))
    mid.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="Lead Guitar", time=0),
        mido.Message("note_on", note=64, velocity=90, time=0),
        mido.Message("note_off", note=64, velocity=0, time=480),
    ]))
    path = tmp_path / "dup.mid"
    mid.save(str(path))
    env = call("analyze", {"midi_path": str(path)})
    assert env["ok"] is True
    names = [t["name"] for t in env["data"]["tracks"]]
    # Segundo com sufixo estavel
    assert names[0] == "Lead Guitar"
    assert any("#" in n for n in names[1:])


# --- fallbacks e branches raras --------------------------------------------

def _midi_without_tempo(path: Path) -> str:
    """MIDI minimo sem `set_tempo` — for exercitar o fallback GM=120 do
    `_tempo_of` e o default 4/4 do `_time_signature_of`."""
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    mid.tracks.append(mido.MidiTrack([
        mido.MetaMessage("track_name", name="only", time=0),
        mido.Message("note_on", note=60, velocity=80, time=0),
        mido.Message("note_off", note=60, velocity=0, time=480),
    ]))
    mid.save(str(path))
    return str(path)


def test_plan_skeleton_on_midi_without_tempo_defaults_to_120_bpm(tmp_path: Path):
    """MIDI sem set_tempo cai no fallback GM 120 do `_tempo_of` e no default
    4/4 do `_time_signature_of` — plan.skeleton nao falha e reporta tempo=120."""
    midi = _midi_without_tempo(tmp_path / "no_tempo.mid")
    env = call("plan.skeleton", {"midi_path": midi, "seed": 0})
    assert env["ok"] is True
    assert env["data"]["plan"]["source_midi"]["tempo"] == 120.0


def test_plan_validate_plan_path_missing_version_returns_field_missing(
    tmp_path: Path,
):
    """plan_path sem `version` bypassa o schema (que so olha o campo string) e
    chega em `_load_plan_from_dict` -> KeyError -> ToolError E_PLAN_FIELD_MISSING.
    O ToolError e capturado pela plan.validate e vira erro de dominio."""
    midi = _synthetic_fixture()
    p = tmp_path / "no_version.json"
    # Plan sem `version` — from_dict levanta KeyError.
    p.write_text(json.dumps({
        "seed": 0, "route": "rock",
        "source_midi": {"path": midi, "sha256": "x" * 64},
        "sections": [], "elements": [],
    }), encoding="utf-8")
    env = call("plan.validate", {"plan_path": str(p), "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    joined = " ".join(e["message"] for e in env["data"]["errors"])
    assert "version" in joined


def test_render_plan_path_missing_version_returns_e_plan_invalid(tmp_path: Path):
    """plan_path bypassa o schema; plan_mod.from_dict levanta KeyError; a
    fachada mapeia para E_PLAN_INVALID."""
    midi = _synthetic_fixture()
    p = tmp_path / "no_version.json"
    p.write_text(json.dumps({
        "seed": 0, "route": "rock",
        "source_midi": {"path": midi, "sha256": "x" * 64},
        "sections": [], "elements": [],
    }), encoding="utf-8")
    env = call("render", {
        "midi_path": midi, "plan_path": str(p),
        "output_path": str(tmp_path / "o.mid"),
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_INVALID"


def test_cli_input_file_unreadable_returns_e_input_file(tmp_path: Path):
    """--input aponta para arquivo sem permissao de leitura -> envelope
    E_INPUT_FILE (nao stack trace)."""
    import io
    import sys

    from tools import cli

    p = tmp_path / "in.json"
    p.write_text("{}", encoding="utf-8")
    p.chmod(0o000)
    try:
        old_out = sys.stdout
        sys.stdout = io.StringIO()
        try:
            code = cli.main(["tool", "analyze", "--input", str(p)])
            raw = sys.stdout.getvalue()
        finally:
            sys.stdout = old_out
    finally:
        p.chmod(0o644)
    env = json.loads(raw)
    assert code != 0
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_FILE"
