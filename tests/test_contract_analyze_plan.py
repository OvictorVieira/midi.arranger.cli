"""Testes de contrato das tools analyze, plan.skeleton, plan.validate (US-003)."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from tools import contract as _contract  # noqa: F401 — garante registro
from tools.registry import call, get
from tools.techniques import SUPPORTED_TECHNIQUES

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
    assert env2["data"]["valid"] is True
    assert env2["data"]["errors"] == []
    assert env2["data"]["normalized_plan"] == plan


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


def _attach_brief_authorizing_all(plan: dict, tmp_path: Path) -> None:
    """Anexa `brief_ref` autorizando todas as tecnicas em `plan['style']`.

    Depois de US-003, plan sem brief_ref com techniques nao vazia falha na
    validacao. Este helper e o atalho para os testes de fachada que
    exercitam outras regras (parametro, apelido, mensagem de erro).
    """
    from tools.brief_ref import brief_sha256

    authorized: dict[str, dict[str, list[str]]] = {}
    for family, entry in (plan.get("style") or {}).items():
        names = [t["name"] for t in entry.get("techniques") or []]
        if names:
            authorized[family] = {"authorized_techniques": names}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps({"style": authorized}), encoding="utf-8")
    plan["brief_ref"] = {
        "path": str(brief_path),
        "sha256": brief_sha256(brief_path),
    }


def test_plan_validate_accepts_skeleton_output():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True
    assert env["data"]["errors"] == []


def test_plan_validate_returns_normalized_style_default_for_used_family():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["assumptions"] = []
    plan["elements"] = [{
        "id": "piano_default",
        "role": "piano",
        "sections": [plan["sections"][0]["label"]],
        "register": [48, 80],
        "layers": 1,
        "sync_role": "sustain_through",
        "articulation": "tight",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": None,
        "rationale": "Piano sustenta a familia keys para exercitar default de estilo.",
        "is_protagonist": False,
    }]

    env = call("plan.validate", {"plan": plan, "midi_path": midi})

    assert env["ok"] is True
    assert env["data"]["valid"] is True
    normalized = env["data"]["normalized_plan"]
    assert normalized["style"]["keys"]["confidence"] == "default"
    assert any(
        "keys" in assumption and "persona base" in assumption
        for assumption in normalized["assumptions"]
    )
    assert "style" not in plan
    assert plan["assumptions"] == []


def _complete_style_dict() -> dict:
    return {
        "bass": {
            "reference": "James Jamerson",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/bass"],
            "confidence": "high",
            "techniques": [],
            "parameters": {"ghost_note_velocity": 35},
        },
        "drums": {
            "reference": "Steve Jordan",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/drums"],
            "confidence": "medium",
            "techniques": [{"name": "drums.ghost_notes", "rationale": "Caixa seca."}],
            "parameters": {"swing": 0.12},
        },
        "guitar": {
            "reference": "The Edge",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/guitar"],
            "confidence": "low",
            "techniques": [],
            "parameters": {"delay_feedback": 0.35},
        },
        "keys": {
            "reference": "Nigel Godrich",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/keys"],
            "confidence": "default",
            "techniques": [],
            "parameters": {"voicing_openness": 0.6},
        },
    }


def test_plan_validate_accepts_complete_style_in_all_four_families(tmp_path: Path):
    from tools.brief_ref import brief_sha256

    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps(
            {
                "style": {
                    family: {
                        "authorized_techniques": [
                            t["name"] for t in entry["techniques"]
                        ],
                    }
                    for family, entry in plan["style"].items()
                }
            }
        ),
        encoding="utf-8",
    )
    plan["brief_ref"] = {
        "path": str(brief_path),
        "sha256": brief_sha256(brief_path),
    }
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True


def test_plan_validate_accepts_valid_brief_ref(tmp_path: Path):
    from tools.brief_ref import brief_sha256

    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({"style": {"drums": {"authorized_techniques": []}}}),
        encoding="utf-8",
    )
    plan["brief_ref"] = {
        "path": str(brief_path),
        "sha256": brief_sha256(brief_path),
    }

    env = call("plan.validate", {"plan": plan, "midi_path": midi})

    assert env["ok"] is True
    assert env["data"]["valid"] is True
    assert env["data"]["normalized_plan"]["brief_ref"] == plan["brief_ref"]


def test_plan_validate_rejects_malformed_brief_ref_sha256_in_schema():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["brief_ref"] = {
        "path": "arrangement-brief.json",
        "sha256": "A" * 64,
    }

    env = call("plan.validate", {"plan": plan, "midi_path": midi})

    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.brief_ref.sha256"


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("path", "plan.brief_ref.path"),
        ("sha256", "plan.brief_ref.sha256"),
    ],
)
def test_plan_validate_rejects_partial_brief_ref_in_schema(field: str, path: str):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["brief_ref"] = {
        "path": "arrangement-brief.json",
        "sha256": "0" * 64,
    }
    del plan["brief_ref"][field]

    env = call("plan.validate", {"plan": plan, "midi_path": midi})

    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == path


def test_plan_validate_resolves_simple_style_technique_name_by_family(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["drums"]["techniques"] = [{"name": "ghost_notes"}]
    _attach_brief_authorizing_all(plan, tmp_path)
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True


def test_plan_validate_rejects_unknown_style_technique_with_exact_path(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["drums"]["techniques"] = [{"name": "flanm"}]
    _attach_brief_authorizing_all(plan, tmp_path)
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    err = env["data"]["errors"][0]
    assert err["path"] == "style.drums.techniques[0].name"
    assert "drums.flam" in err["message"]


def test_plan_validate_rejects_documented_but_unimplemented_style_technique(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["keys"]["techniques"] = [{"name": "keys.hand_asynchrony"}]
    _attach_brief_authorizing_all(plan, tmp_path)
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    err = env["data"]["errors"][0]
    assert err["path"] == "style.keys.techniques[0].name"
    assert "not implemented by the engine" in err["message"]
    assert "drums.ghost_notes" in err["message"]


def test_plan_validate_rejects_style_technique_from_other_family(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["bass"]["techniques"] = [{"name": "drums.flam"}]
    _attach_brief_authorizing_all(plan, tmp_path)
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    err = env["data"]["errors"][0]
    assert err["path"] == "style.bass.techniques[0].name"
    assert "drums.flam" in err["message"]


def test_plan_validate_rejects_unknown_style_family_in_schema():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["vocals"] = {
        "reference": "cantor",
        "researched_at": "2026-08-24",
        "sources": ["https://example.test/vocals"],
        "confidence": "medium",
        "techniques": [],
        "parameters": {},
    }
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.style.vocals"


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("reference", "plan.style.bass.reference"),
        ("researched_at", "plan.style.bass.researched_at"),
        ("sources", "plan.style.bass.sources"),
        ("confidence", "plan.style.bass.confidence"),
        ("techniques", "plan.style.bass.techniques"),
        ("parameters", "plan.style.bass.parameters"),
    ],
)
def test_plan_validate_rejects_missing_style_field_in_schema(field: str, path: str):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    del plan["style"]["bass"][field]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == path


def test_plan_validate_rejects_invalid_style_confidence_in_schema():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["drums"]["confidence"] = "bastante"
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.style.drums.confidence"


def test_plan_validate_rejects_musical_content_key_inside_style_schema():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["bass"]["notes"] = [40, 42, 45]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.style.bass.notes"
    assert "nunca conteudo musical" in env["error"]["message"]


def test_plan_validate_accepts_style_parameter_range_pair_schema(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["drums"]["parameters"] = {"velocity": [20, 45]}
    _attach_brief_authorizing_all(plan, tmp_path)
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True


def test_plan_validate_rejects_style_parameter_outside_manual_range(tmp_path: Path):
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["drums"]["techniques"] = [{"name": "ghost_notes"}]
    plan["style"]["drums"]["parameters"] = {"velocity": 46}
    _attach_brief_authorizing_all(plan, tmp_path)
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is False
    err = env["data"]["errors"][0]
    assert err["path"] == "style.drums.parameters.velocity"
    assert "46" in err["message"]
    assert "[20, 45]" in err["message"]


def test_plan_validate_warns_for_style_parameter_source_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(
        "tools.techniques.SUPPORTED_TECHNIQUES",
        (*SUPPORTED_TECHNIQUES, "guitar.palm_mute"),
    )
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["guitar"]["techniques"] = [{"name": "palm_mute"}]
    plan["style"]["guitar"]["parameters"] = {"gate_absoluto_ms": 999}
    _attach_brief_authorizing_all(plan, tmp_path)
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is True
    assert env["data"]["valid"] is True
    warning = env["warnings"][0]
    assert warning["code"] == "W_PLAN"
    assert "style.guitar.parameters.gate_absoluto_ms" in warning["message"]
    assert "source gap" in warning["message"]


def test_plan_validate_rejects_midi_integer_sequence_under_innocent_style_parameter_schema():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["bass"]["parameters"] = {"accent_shape": [40, 42, 45]}
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.style.bass.parameters.accent_shape"
    assert "nunca conteudo musical" in env["error"]["message"]


def test_plan_validate_rejects_pitch_time_event_array_inside_style_schema():
    midi = _require_fixture()
    plan = _valid_plan_from_skeleton()
    plan["style"] = _complete_style_dict()
    plan["style"]["drums"]["parameters"] = {
        "accent_map": [{"pitch": 38, "time": 0.0}],
    }
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"
    assert env["error"]["path"] == "plan.style.drums.parameters.accent_map"
    assert "altura e tempo" in env["error"]["message"]


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
        "instrument": None,
        "rationale": "Pad usa layers invalido para testar erro de schema.",
        "is_protagonist": False,
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
        "instrument": None,
        "rationale": "Pad usa registro invalido para testar erro de schema.",
        "is_protagonist": False,
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
        "instrument": None,
        "rationale": "Pad referencia uma secao inexistente para testar erro de dominio.",
        "is_protagonist": False,
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
        "instrument": None,
        "rationale": "Pad disputa protagonismo para testar conflito de secao.",
        "is_protagonist": True,
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


def test_plan_validate_rejects_null_rationale():
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
    assert env["ok"] is False
    assert env["error"]["path"] == "plan.elements[0].rationale"


def test_plan_validate_rejects_missing_rationale():
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
        "instrument": None, "is_protagonist": False,
    }]
    env = call("plan.validate", {"plan": plan, "midi_path": midi})
    assert env["ok"] is False
    assert env["error"]["path"] == "plan.elements[0].rationale"


def _write_drums_midi(path: Path) -> None:
    """Bateria com backbeats de caixa no canal 9, suficiente para ornamentar."""
    import mido

    mid = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    previous = 0
    for tick in (480, 1440, 2400, 3360, 4320, 5280):
        track.append(mido.Message(
            "note_on", note=38, velocity=100, channel=9, time=tick - previous,
        ))
        track.append(mido.Message(
            "note_off", note=38, velocity=0, channel=9, time=60,
        ))
        previous = tick + 60
    mid.tracks.append(track)
    mid.save(str(path))


def test_render_resolve_brief_relativo_contra_o_diretorio_do_plano(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    """Achado do review com o Codex no PR #52.

    A fachada `render` lia o plano com `_read_plan_dict` e chamava
    `render_mod.render(plan_obj, ...)` sem informar de onde o plano veio.
    `brief_ref.path` relativo resolvia contra o CWD de quem chamou a tool,
    nao contra o diretorio do plano — entao o brief ao lado do plano nao era
    encontrado e o render falhava, mesmo com tudo autorizado corretamente.

    O teste roda de um CWD deliberadamente diferente.
    """
    from tools.brief_ref import brief_sha256

    proj = tmp_path / "proj"
    proj.mkdir()
    src = proj / "src.mid"
    _write_drums_midi(src)

    brief_path = proj / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({
            "style": {
                fam: {
                    "authorized_techniques": (
                        ["drums.ghost_notes"] if fam == "drums" else []
                    ),
                }
                for fam in ("bass", "drums", "guitar", "keys")
            },
        }),
        encoding="utf-8",
    )

    plan_path = proj / "arrangement-plan.json"
    plan_path.write_text(
        json.dumps({
            "version": 1,
            "seed": 1,
            "source_midi": {"path": str(src), "sha256": "0" * 64},
            "route": "cinematica_emocional",
            "sections": [],
            "elements": [],
            # RELATIVO de proposito — e disso que trata o achado
            "brief_ref": {
                "path": "arrangement-brief.json",
                "sha256": brief_sha256(brief_path),
            },
            "edits": [
                {"track": "Drums", "profile": "drums", "intensity": 0.0},
            ],
            "style": {
                "drums": {
                    "reference": "X",
                    "researched_at": "2026-08-26",
                    "sources": ["https://example.test/x"],
                    "confidence": "high",
                    "techniques": [{"name": "drums.ghost_notes"}],
                    "parameters": {},
                },
            },
        }),
        encoding="utf-8",
    )

    outside = tmp_path / "outro_cwd"
    outside.mkdir()
    monkeypatch.chdir(outside)

    env = call("render", {
        "plan_path": str(plan_path),
        "midi_path": str(src),
        "output_path": str(proj / "out.mid"),
    })
    assert env["ok"] is True, env
    assert (proj / "out.mid").exists()
