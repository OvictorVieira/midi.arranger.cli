"""Testes do schema/validador do arrangement-plan.json (US-009)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.plan import (
    ArrangementPlan,
    Element,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    PlanValidationError,
    SourceMidi,
    StyleTechnique,
    Transition,
    dump,
    from_dict,
    load,
    to_dict,
    validate,
    validate_edits_against_midi,
)

# --- fixture ----------------------------------------------------------------

def _valid_plan() -> ArrangementPlan:
    """Plano minimo mas completo — reflete a estrutura da secao 5 do spec."""
    return ArrangementPlan(
        version=1,
        seed=20260820,
        source_midi=SourceMidi(
            path="~/Desktop/musica.mid",
            sha256="a" * 64,
            tempo=174.0,
            key="A",
            bars=96,
        ),
        route="hook_eletronico_pesado",
        assumptions=["Tonalidade inferida do MIDI; usuario nao confirmou."],
        sections=[
            PlanSection(
                label="INTRO",
                kind="intro",
                start_bar=0,
                end_bar=8,
                source="marker",
                protagonist="texture",
                energy={"densidade": 2, "impacto": 1, "largura": 3, "altura": 2, "instabilidade": 1},
            ),
            PlanSection(
                label="CHORUS 1",
                kind="chorus",
                start_bar=25,
                end_bar=40,
                source="marker",
                protagonist="vocal_hook",
                energy={"densidade": 8, "impacto": 7, "largura": 9, "altura": 8, "instabilidade": 3},
            ),
        ],
        elements=[
            Element(
                id="pad_intro",
                role="pad",
                sections=["INTRO"],
                register=[48, 72],
                layers=2,
                sync_role="sustain_through",
                articulation="sustained",
                harmony="follow_chords",
                pattern=None,
                degrees=None,
                dynamics={"entry": "fade_in_2bars", "shape": "hold"},
                instrument={"plugin": "Omnisphere", "preset": "Desert Wind", "verified": True},
                rationale="Sustenta o ar antes do primeiro riff.",
            ),
            Element(
                id="arp_chorus",
                role="arp",
                sections=["CHORUS 1"],
                register=[72, 84],
                layers=1,
                sync_role="kick_support",
                articulation="tight",
                harmony="follow_chords",
                pattern={"subdivision": "1/16", "steps": "x.xx.x.x", "gap_at": 6},
                degrees=[1, 5, 8, 10],
                dynamics={"entry": "fade_in_2bars", "shape": "rise"},
                instrument={"plugin": "Serum", "preset": "What The Pluck", "verified": True},
                rationale="Motor de 16avos para vender o refrao.",
            ),
        ],
        transitions=[
            Transition(
                at_bar=25,
                from_section="INTRO",
                to_section="CHORUS 1",
                dimensions_changed=["densidade", "altura"],
                elements=["arp_chorus"],
                technique="entrada de arp no downbeat",
            )
        ],
    )


# --- happy path -------------------------------------------------------------

def test_validate_accepts_valid_plan():
    """AC: 'Teste com plano valido passa'."""
    validate(_valid_plan())  # nao levanta


def test_round_trip_writes_and_reads_identical_object(tmp_path: Path):
    """AC: 'round-trip: escrever plano, ler plano e obter objeto identico'."""
    plan = _valid_plan()
    path = tmp_path / "plan.json"
    dump(plan, path)
    reloaded = load(path)
    assert reloaded == plan


def test_dict_round_trip_is_identity():
    """to_dict e from_dict compoem para identidade — util para debug e edicao
    programatica sem passar por disco."""
    plan = _valid_plan()
    assert from_dict(to_dict(plan)) == plan


def test_load_reads_hand_written_json(tmp_path: Path):
    """Usuario editando o JSON a mao deve conseguir carregar sem drama."""
    data = to_dict(_valid_plan())
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    plan = load(path)
    assert plan.route == "hook_eletronico_pesado"


def _complete_style() -> dict[str, FamilyStyle]:
    return {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[StyleTechnique(name="ghost_notes", density=0.2)],
            parameters={"ghost_note_velocity": 35.0},
        ),
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="drums.ghost_notes", rationale="Caixa seca no pulso.")],
            parameters={"swing": 0.12},
        ),
        "guitar": FamilyStyle(
            reference="The Edge",
            researched_at="2026-08-24",
            sources=["https://example.test/guitar"],
            confidence="low",
            techniques=[],
            parameters={"delay_feedback": 0.35},
        ),
        "keys": FamilyStyle(
            reference="Nigel Godrich",
            researched_at="2026-08-24",
            sources=["https://example.test/keys"],
            confidence="default",
            techniques=[],
            parameters={"voicing_openness": 0.6},
        ),
    }


def test_validate_accepts_complete_style_for_all_four_families():
    plan = _valid_plan()
    plan.style = _complete_style()
    validate(plan)  # nao levanta


def test_validate_resolves_simple_style_technique_name_by_family():
    plan = _valid_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="ghost_notes")],
            parameters={},
        )
    }
    validate(plan)  # `ghost_notes` existe em drums e bass; o path desambigua.


def test_validate_accepts_canonical_style_technique_name():
    plan = _valid_plan()
    plan.style = {
        "guitar": FamilyStyle(
            reference="Tom Morello",
            researched_at="2026-08-24",
            sources=["https://example.test/guitar"],
            confidence="high",
            techniques=[StyleTechnique(name="guitar.palm_mute")],
            parameters={},
        )
    }
    validate(plan)  # nao levanta


def test_validate_rejects_unknown_style_technique_with_exact_path_and_candidates():
    plan = _valid_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="flanm")],
            parameters={},
        )
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].name"
    assert "drums.flam" in exc.value.message


def test_validate_rejects_style_technique_from_other_family():
    plan = _valid_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.flam")],
            parameters={},
        )
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.bass.techniques[0].name"
    assert "drums.flam" in exc.value.message
    assert "bass" in exc.value.message


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("reference", "style.bass.reference"),
        ("researched_at", "style.bass.researched_at"),
        ("sources", "style.bass.sources"),
        ("confidence", "style.bass.confidence"),
        ("techniques", "style.bass.techniques"),
        ("parameters", "style.bass.parameters"),
    ],
)
def test_from_dict_rejects_missing_required_style_family_field(field: str, path: str):
    data = to_dict(_valid_plan())
    data["style"] = {
        family: {
            "reference": entry.reference,
            "researched_at": entry.researched_at,
            "sources": entry.sources,
            "confidence": entry.confidence,
            "techniques": [_style_technique_to_dict_for_test(t) for t in entry.techniques],
            "parameters": entry.parameters,
        }
        for family, entry in _complete_style().items()
    }
    del data["style"]["bass"][field]
    with pytest.raises(PlanValidationError) as exc:
        from_dict(data)
    assert exc.value.path == path


def _style_technique_to_dict_for_test(technique: StyleTechnique) -> dict[str, object]:
    data: dict[str, object] = {"name": technique.name}
    if technique.density is not None:
        data["density"] = technique.density
    if technique.rationale is not None:
        data["rationale"] = technique.rationale
    return data


def test_validate_rejects_invalid_style_confidence():
    plan = _valid_plan()
    plan.style = _complete_style()
    plan.style["drums"].confidence = "bastante"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.confidence"


def test_validate_rejects_unknown_style_family_with_exact_path():
    plan = _valid_plan()
    plan.style = {
        "vocals": FamilyStyle(
            reference="cantor",
            researched_at="2026-08-24",
            sources=["https://example.test/vocals"],
            confidence="medium",
            techniques=[],
            parameters={},
        )
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.vocals"


def test_from_dict_rejects_extra_style_family_field():
    data = to_dict(_valid_plan())
    data["style"] = {
        "bass": {
            "reference": "James Jamerson",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/bass"],
            "confidence": "high",
            "techniques": [],
            "parameters": {},
            "extra": True,
        }
    }
    with pytest.raises(PlanValidationError) as exc:
        from_dict(data)
    assert exc.value.path == "style.bass.extra"


def test_style_survives_dict_round_trip():
    plan = _valid_plan()
    plan.style = _complete_style()
    assert from_dict(to_dict(plan)) == plan


def test_dump_writes_indented_json_that_parses(tmp_path: Path):
    """Serializacao gera JSON legivel (indent=2) — usuario edita a mao."""
    path = tmp_path / "plan.json"
    dump(_valid_plan(), path)
    text = path.read_text(encoding="utf-8")
    assert "\n" in text  # indentado
    data = json.loads(text)
    assert data["version"] == 1


# --- rejeicoes: mensagens carregam path exato ------------------------------

def test_rejects_unknown_sync_role_with_element_path():
    """AC: 'rejeita sync_role desconhecido' + path exato aponta o elemento."""
    plan = _valid_plan()
    plan.elements[1].sync_role = "nao_existe"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[1].sync_role"
    assert "nao_existe" in exc.value.message


def test_rejects_register_out_of_midi_range_with_indexed_path():
    """AC: 'rejeita registro fora de 0-127' + path como
    'elements[3].register[1]' (exemplo literal do PRD)."""
    plan = _valid_plan()
    plan.elements[0].register = [48, 200]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].register[1]"
    assert "0-127" in exc.value.message


def test_rejects_negative_register_index_zero():
    """Path aponta o indice do valor invalido, nao 'register' inteiro."""
    plan = _valid_plan()
    plan.elements[0].register = [-1, 60]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].register[0]"


def test_rejects_low_greater_than_high_at_register_root():
    """Quando ambos valores estao no range mas a ordem esta errada, o erro
    vai em `register`, nao num indice — nenhum item isolado e invalido."""
    plan = _valid_plan()
    plan.elements[0].register = [80, 40]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].register"
    assert "<=" in exc.value.message


def test_rejects_section_reference_not_declared():
    """AC: 'rejeita secao que nao existe' — path aponta o slot do elemento."""
    plan = _valid_plan()
    plan.elements[1].sections = ["CHORUS 1", "SECAO_INEXISTENTE"]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[1].sections[1]"
    assert "SECAO_INEXISTENTE" in exc.value.message


def test_rejects_unknown_articulation():
    """AC: 'rejeita articulacao desconhecida'."""
    plan = _valid_plan()
    plan.elements[0].articulation = "picado"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].articulation"
    assert "picado" in exc.value.message


def test_rejects_element_with_empty_role():
    """AC: 'rejeita elemento sem role'."""
    plan = _valid_plan()
    plan.elements[0].role = ""
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].role"


def test_rejects_layers_zero():
    """AC: 'rejeita layers menor que 1' — 0 nao gera track nenhuma."""
    plan = _valid_plan()
    plan.elements[0].layers = 0
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].layers"
    assert ">= 1" in exc.value.message


def test_rejects_layers_negative():
    plan = _valid_plan()
    plan.elements[0].layers = -3
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].layers"


def test_rejects_unknown_route():
    plan = _valid_plan()
    plan.route = "trap"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "route"


def test_rejects_unknown_section_kind():
    plan = _valid_plan()
    plan.sections[0].kind = "drop"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].kind"


def test_rejects_unknown_section_source():
    plan = _valid_plan()
    plan.sections[0].source = "guessed"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[0].source"


def test_rejects_unknown_protagonist():
    plan = _valid_plan()
    plan.sections[1].protagonist = "brass_hook"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "sections[1].protagonist"


def test_rejects_unknown_harmony_mode():
    plan = _valid_plan()
    plan.elements[0].harmony = "atonal"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].harmony"


def test_rejects_wrong_schema_version():
    plan = _valid_plan()
    plan.version = 2
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "version"


def test_rejects_non_int_seed():
    plan = _valid_plan()
    plan.seed = "20260820"  # type: ignore[assignment]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "seed"


# --- source_midi ------------------------------------------------------------

def test_plan_carries_global_seed_and_source_midi():
    """AC: 'plano carrega seed global e source_midi com caminho e
    hash sha256 do arquivo de origem'."""
    plan = _valid_plan()
    assert isinstance(plan.seed, int)
    assert plan.source_midi.path
    assert plan.source_midi.sha256 and len(plan.source_midi.sha256) == 64


def test_rejects_empty_source_midi_path():
    plan = _valid_plan()
    plan.source_midi.path = ""
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "source_midi.path"


def test_rejects_empty_source_midi_sha256():
    plan = _valid_plan()
    plan.source_midi.sha256 = ""
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "source_midi.sha256"


# --- garantia de que validate() nao muta o plano ---------------------------

def test_validate_does_not_mutate_plan():
    """Validador e read-only — importante porque quem chama pode continuar
    editando o plano em memoria depois de validar."""
    plan = _valid_plan()
    snapshot = copy.deepcopy(plan)
    validate(plan)
    assert plan == snapshot


# --- edits (US-011 / FR-28) -------------------------------------------------

def test_plan_default_edits_is_empty_list():
    """AC: 'edits' default e lista vazia — plano sem humanizacao de tracks."""
    plan = _valid_plan()
    assert plan.edits == []


def test_edits_round_trip_via_dict():
    plan = _valid_plan()
    plan.edits = [
        PlanEdit(track="Bass", profile="bass", intensity=0.75),
        PlanEdit(track="Drums", profile="drums", intensity=1.0),
    ]
    assert from_dict(to_dict(plan)) == plan


def test_validate_rejects_unknown_edit_profile():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="sax", intensity=0.5)]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].profile"


def test_validate_rejects_intensity_out_of_range():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=1.5)]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].intensity"


def test_validate_rejects_negative_intensity():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=-0.1)]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].intensity"


def test_validate_rejects_non_numeric_intensity():
    plan = _valid_plan()
    plan.edits = [
        PlanEdit(track="Bass", profile="bass", intensity="strong"),  # type: ignore[arg-type]
    ]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].intensity"


def test_validate_rejects_boolean_intensity():
    """Boolean e subtipo de int em Python — precisa ser rejeitado
    explicitamente para nao passar como 0 ou 1."""
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=True)]  # type: ignore[arg-type]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].intensity"


def test_validate_rejects_empty_track_name():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="", profile="bass", intensity=0.5)]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].track"


def test_validate_rejects_duplicate_edit_for_same_track():
    plan = _valid_plan()
    plan.edits = [
        PlanEdit(track="Bass", profile="bass", intensity=0.5),
        PlanEdit(track="Bass", profile="generic", intensity=0.5),
    ]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert "duplicate" in exc.value.message


def test_validate_accepts_integer_intensity():
    """int e valido como intensity (0 e 1) — nao forcar float no autor."""
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=1)]
    validate(plan)  # nao levanta


def test_edits_survives_dump_load_round_trip(tmp_path: Path):
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5)]
    path = tmp_path / "plan.json"
    dump(plan, path)
    reloaded = load(path)
    assert reloaded.edits == plan.edits


def test_validate_edits_against_midi_suggests_closest_track():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bas", profile="bass", intensity=0.5)]
    with pytest.raises(PlanValidationError) as exc:
        validate_edits_against_midi(plan, ["Piano", "Bass", "Drums"])
    assert exc.value.path == "edits[0].track"
    assert "Bass" in exc.value.message


def test_validate_edits_against_midi_passes_when_track_exists():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5)]
    validate_edits_against_midi(plan, ["Piano", "Bass"])  # nao levanta


# --- load falha rejeitando antes de devolver plano invalido ----------------

def test_load_raises_on_invalid_plan(tmp_path: Path):
    """load() valida — arquivo invalido no disco NAO devolve um objeto
    silenciosamente quebrado."""
    plan = _valid_plan()
    data = to_dict(plan)
    data["elements"][0]["register"] = [48, 999]
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(PlanValidationError) as exc:
        load(path)
    assert exc.value.path == "elements[0].register[1]"
