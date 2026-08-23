"""Testes de contrato das tools analyze, plan.skeleton, plan.validate (US-003)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from tools import contract as _contract  # noqa: F401 — garante registro
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


# --- descricoes -------------------------------------------------------------

@pytest.mark.parametrize("name", ["analyze", "plan.skeleton", "plan.validate"])
def test_tool_has_prompt_style_description(name: str):
    tool = get(name)
    assert tool is not None
    desc = tool.description
    assert desc.strip(), f"{name} sem descricao"
    # Descricao e PROMPT: nao pode ser a linha unica "analisa um midi".
    assert len(desc) > 80, f"descricao de {name} curta demais para servir de prompt"
    assert "Use" in desc or "use" in desc, (
        f"descricao de {name} nao diz *quando* usar"
    )


# --- analyze ---------------------------------------------------------------

def test_analyze_valid_input_returns_ok_and_full_shape():
    midi = _require_fixture()
    env = call("analyze", {"midi_path": midi})
    assert env["ok"] is True
    d = env["data"]
    assert set(d) >= {
        "midi_path", "sha256", "tempo", "time_signature", "key_root",
        "key_name", "sections", "bars", "tracks", "rhythmic_anchors",
    }
    assert d["time_signature"] == {"numerator": 4, "denominator": 4}
    assert d["key_root"] == 3
    assert d["key_name"] == "D#"
    assert len(d["sections"]) == 10
    assert len(d["bars"]) == 163
    assert d["sections"][0] == {
        "label": "INTRO A", "kind": "intro",
        "start_bar": 4, "end_bar": 14, "source": "marker",
    }
    assert set(d["bars"][0]["register_occupancy"]) == {"sub", "low", "mid", "high"}


def test_analyze_ancora_has_no_inferred_warning():
    midi = _require_fixture()
    env = call("analyze", {"midi_path": midi})
    assert env["ok"] is True
    codes = [w["code"] for w in env["warnings"]]
    assert "W_INFERRED_SECTIONS" not in codes


def test_analyze_inferred_sections_produce_warning(tmp_path: Path):
    """Constroi um MIDI sem markers para forcar inferencia — o warning
    W_INFERRED_SECTIONS precisa aparecer para o agente saber que tem que
    pedir confirmacao."""
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("time_signature", numerator=4, denominator=4, time=0))
    tr.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
    for i in range(8):
        tr.append(mido.Message("note_on", note=60 + i, velocity=90, time=0))
        tr.append(mido.Message("note_off", note=60 + i, velocity=0, time=480))
    mid.tracks.append(tr)
    path = tmp_path / "no_markers.mid"
    mid.save(str(path))

    env = call("analyze", {"midi_path": str(path)})
    assert env["ok"] is True
    codes = [w["code"] for w in env["warnings"]]
    # Se o midi tiver secoes inferidas, precisa avisar. Se o heuristico nao
    # detectar nenhuma secao, o teste nao vale — pula.
    if any(s["source"] == "inferred" for s in env["data"]["sections"]):
        assert "W_INFERRED_SECTIONS" in codes


def test_analyze_missing_file_returns_error_envelope():
    env = call("analyze", {"midi_path": "/definitely/does/not/exist.mid"})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_NOT_FOUND"
    assert env["error"]["path"] == "midi_path"


def test_analyze_not_a_midi_returns_error(tmp_path: Path):
    fake = tmp_path / "not.mid"
    fake.write_text("this is not a midi file", encoding="utf-8")
    env = call("analyze", {"midi_path": str(fake)})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_MIDI_INVALID"


def test_analyze_unknown_field_returns_ok_false_with_path():
    env = call("analyze", {"midi_path": FIXTURE, "extra": True})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "extra"


def test_analyze_missing_midi_path_field_returns_ok_false():
    env = call("analyze", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "midi_path"


# --- plan.skeleton --------------------------------------------------------

def test_plan_skeleton_returns_valid_plan_that_round_trips_through_validate():
    midi = _require_fixture()
    env = call("plan.skeleton", {"midi_path": midi, "seed": 42})
    assert env["ok"] is True
    plan = env["data"]["plan"]
    assert plan["seed"] == 42
    assert plan["route"] == "cinematica_emocional"
    assert plan["elements"] == []
    assert plan["edits"] == []
    assert len(plan["sections"]) == 10

    env2 = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env2["ok"] is True
    assert env2["data"] == {"valid": True, "errors": []}


def test_plan_skeleton_writes_to_output_path_when_given(tmp_path: Path):
    midi = _require_fixture()
    out = tmp_path / "plan.json"
    env = call("plan.skeleton", {"midi_path": midi, "output_path": str(out)})
    assert env["ok"] is True
    assert env["data"]["output_path"] == str(out)
    assert out.exists()
    written = json.loads(out.read_text(encoding="utf-8"))
    assert written == env["data"]["plan"]


def test_plan_skeleton_default_route_when_omitted():
    midi = _require_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["plan"]["route"] == "cinematica_emocional"


def test_plan_skeleton_invalid_route_returns_error():
    midi = _require_fixture()
    env = call("plan.skeleton", {"midi_path": midi, "route": "nao_existe"})
    assert env["ok"] is False
    # o proprio schema rejeita porque route e um enum
    assert env["error"]["code"] == "E_INPUT_SCHEMA"


def test_plan_skeleton_source_midi_carries_sha256_and_bar_count():
    midi = _require_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    assert env["ok"] is True
    src = env["data"]["plan"]["source_midi"]
    assert len(src["sha256"]) == 64
    assert src["bars"] == 163


def test_plan_skeleton_unknown_field_returns_ok_false_with_path():
    midi = _require_fixture()
    env = call("plan.skeleton", {"midi_path": midi, "surprise": True})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "surprise"


# --- plan.validate --------------------------------------------------------

def _valid_plan_from_skeleton() -> dict:
    midi = _require_fixture()
    env = call("plan.skeleton", {"midi_path": midi})
    return env["data"]["plan"]


def test_plan_validate_accepts_skeleton_output():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True
    assert env["data"]["errors"] == []


def test_plan_validate_reports_layers_lt_1_error():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["elements"] = [{
        "id": "bad",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80],
        "layers": 0,  # invalido
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None, "rationale": None, "is_protagonist": False,
    }]
    # schema layers minimum=1 pega antes do dominio.
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.elements[0].layers"


def test_plan_validate_reports_register_out_of_range():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["elements"] = [{
        "id": "bad",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 999],  # 999 > 127
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None, "rationale": None, "is_protagonist": False,
    }]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.elements[0].register[1]"


def test_plan_validate_reports_missing_section_reference_via_domain():
    """Reference a section that doesn't exist: schema passa mas dominio rejeita."""
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["elements"] = [{
        "id": "bad",
        "role": "pad",
        "sections": ["SECAO_INEXISTENTE"],
        "register": [40, 80],
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None, "rationale": None, "is_protagonist": False,
    }]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    assert env["data"]["errors"], "esperava erro apontando a secao invalida"
    err = env["data"]["errors"][0]
    assert "SECAO_INEXISTENTE" in err["message"]
    assert err["path"].startswith("elements[")


def test_plan_validate_reports_multiple_protagonists_in_same_section():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    label = plan["sections"][0]["label"]
    base_el = {
        "role": "pad",
        "sections": [label],
        "register": [40, 80],
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None, "rationale": None, "is_protagonist": True,
    }
    plan["elements"] = [
        {**copy.deepcopy(base_el), "id": "el_a"},
        {**copy.deepcopy(base_el), "id": "el_b"},
    ]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    assert any(
        "protagonist" in e["message"].lower() or "protagonist" in e["path"].lower()
        for e in env["data"]["errors"]
    )


def test_plan_validate_via_plan_path_reads_file(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    pp = tmp_path / "plan.json"
    pp.write_text(json.dumps(plan), encoding="utf-8")
    env = call("plan.validate", {"plan_path": str(pp), "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True


def test_plan_validate_missing_both_plan_and_plan_path_returns_error():
    midi = _require_fixture()
    env = call("plan.validate", {"midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_INPUT"


def test_plan_validate_both_plan_and_plan_path_returns_error(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    pp = tmp_path / "plan.json"
    pp.write_text(json.dumps(plan), encoding="utf-8")
    env = call("plan.validate", {
        "plan": plan, "plan_path": str(pp), "midi_path": midi,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_PLAN_INPUT"


def test_plan_validate_rationale_may_be_null():
    """Skeleton devolve rationale=None em cada elemento (ha zero); validar."""
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["elements"] = [{
        "id": "el_a",
        "role": "pad",
        "sections": [plan["sections"][0]["label"]],
        "register": [40, 80],
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "sustained",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None, "rationale": None, "is_protagonist": False,
    }]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True
