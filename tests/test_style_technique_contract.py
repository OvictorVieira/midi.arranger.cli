"""Testes do contrato de tecnica com parametros por tecnica (issue #72).

`StyleTechnique` ganhou `parameters` (nivel de tecnica), `intensity` e
`evidence_refs`. Este arquivo cobre exatamente os cenarios pedidos pela
issue: migracao do contrato legado, anticopia no novo campo, validacao de
range por tecnica (nao por familia), duas tecnicas com nomes de parametro
sobrepostos sem colisao, e precedencia com warning quando os dois niveis
declaram o mesmo nome.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
    FamilyStyle,
    PlanValidationError,
    SourceMidi,
    StyleTechnique,
    from_dict,
    to_dict,
    validate,
)


def _minimal_plan() -> ArrangementPlan:
    return ArrangementPlan(
        version=1,
        seed=1,
        source_midi=SourceMidi(path="song.mid", sha256="a" * 64),
        route="cinematica_emocional",
        sections=[],
        elements=[],
    )


def _write_brief(tmp_path: Path, authorized: dict[str, list[str]]) -> BriefRef:
    style_dict = {
        family: {"authorized_techniques": list(names)}
        for family, names in authorized.items()
    }
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps({"style": style_dict}, indent=2), encoding="utf-8")
    return BriefRef(path=str(brief_path), sha256=brief_sha256(brief_path))


# --- migracao: contrato v1 (so parametros de familia) continua valido ------


def test_v1_plan_with_only_family_level_parameters_still_validates():
    """Plano que so usa o contrato legado (sem `parameters` por tecnica,
    sem `intensity`, sem `evidence_refs`) continua validando sem erro — a
    issue #72 e estritamente aditiva."""
    plan = _minimal_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[],
            parameters={"velocity": [20, 45]},
        )
    }
    validate(plan)  # nao levanta


def test_v1_technique_dict_round_trips_without_gaining_new_keys():
    """Uma tecnica serializada no formato v1 (so `name`) nao ganha `parameters`,
    `intensity` ou `evidence_refs` no dict de saida — round-trip byte-a-byte
    do JSON antigo continua identico."""
    from tools.plan import _style_technique_from_dict, _style_technique_to_dict

    legacy = {"name": "drums.ghost_notes"}
    technique = StyleTechnique(name="drums.ghost_notes")

    assert _style_technique_from_dict(legacy, "style.drums.techniques[0]") == technique
    assert _style_technique_to_dict(technique) == legacy


def test_v1_plan_with_legacy_family_parameters_renders_and_validates_identically(
    tmp_path: Path,
):
    """Plano v1 completo (tecnica so com `name`, parametros so no nivel de
    familia) continua validando E carregando o mesmo objeto no round-trip
    via disco — a mesma garantia que `plan.dump`/`plan.load` ja davam antes
    da issue #72."""
    plan = _minimal_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={"velocity": 35},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"drums": ["drums.ghost_notes"]})

    warnings = validate(plan)
    assert warnings == []
    assert from_dict(to_dict(plan)) == plan


# --- anticopia no nivel de tecnica ------------------------------------------


def test_technique_level_parameters_reject_musical_content(tmp_path: Path):
    """A mesma barreira anticopia de `style.<familia>.parameters` vale para
    `StyleTechnique.parameters` — sequencia de inteiros em faixa MIDI e erro."""
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[
                StyleTechnique(
                    name="bass.ghost_notes",
                    parameters={"accent_shape": [40, 42, 45]},
                )
            ],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"bass": ["bass.ghost_notes"]})
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert "style.bass.techniques[0].parameters" in exc.value.path
    assert "nunca conteudo musical" in exc.value.message


def test_technique_level_parameters_reject_note_name_sequence(tmp_path: Path):
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="medium",
            techniques=[
                StyleTechnique(
                    name="bass.ghost_notes",
                    parameters={},
                    evidence_refs=[],
                )
            ],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"bass": ["bass.ghost_notes"]})
    # Injeta conteudo musical via from_dict, o caminho real de entrada
    # externa (JSON escrito por quem preenche o brief/plano).
    data = to_dict(plan)
    data["style"]["bass"]["techniques"][0]["parameters"] = {
        "riff_like": ["C4", "D4", "E4"],
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(from_dict(data))
    assert "style.bass.techniques[0].parameters" in exc.value.path


# --- validacao de range por tecnica, nao por familia ------------------------


def test_technique_level_range_validated_against_own_technique_only(tmp_path: Path):
    """`bass.ghost_notes.velocity` aceita [25, 50]; `bass.palm_mute.velocity`
    aceita [60, 100]. Um valor valido para `palm_mute` (70) mas invalido
    para `ghost_notes` tem que ser rejeitado quando declarado NA tecnica
    `ghost_notes` — prova que a validacao usa a receita da PROPRIA tecnica,
    nao a familia inteira (o contrato legado validava contra TODAS as
    tecnicas declaradas na familia)."""
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[
                StyleTechnique(name="bass.ghost_notes", parameters={"velocity": 70}),
                StyleTechnique(name="bass.palm_mute"),
            ],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(
        tmp_path, {"bass": ["bass.ghost_notes", "bass.palm_mute"]},
    )
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.bass.techniques[0].parameters.velocity"
    assert "70" in exc.value.message
    assert "[25, 50]" in exc.value.message
    assert "bass.ghost_notes.velocity" in exc.value.message


def test_technique_level_range_accepts_value_valid_for_own_technique(tmp_path: Path):
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[
                StyleTechnique(name="bass.ghost_notes", parameters={"velocity": 30}),
            ],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"bass": ["bass.ghost_notes"]})
    validate(plan)  # 30 esta dentro de [25, 50] — nao levanta


# --- duas tecnicas da mesma familia com nomes de parametro sobrepostos ------


def test_two_techniques_same_family_overlapping_parameter_names_do_not_collide(
    tmp_path: Path,
):
    """`ghost_notes` e `palm_mute` (familia bass) declaram os dois um
    parametro `velocity`, com faixas diferentes e disjuntas. Um valor
    valido para cada tecnica na SUA propria faixa passa nas duas — prova
    que a validacao por tecnica nao cruza os namespaces."""
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[
                StyleTechnique(name="bass.ghost_notes", parameters={"velocity": 30}),
                StyleTechnique(name="bass.palm_mute", parameters={"velocity": 80}),
            ],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(
        tmp_path, {"bass": ["bass.ghost_notes", "bass.palm_mute"]},
    )
    validate(plan)  # nao levanta: 30 valido p/ ghost_notes, 80 valido p/ palm_mute


def test_two_techniques_same_family_overlapping_names_still_validate_independently(
    tmp_path: Path,
):
    """Mesmo cenario acima, mas com os valores TROCADOS entre as tecnicas —
    30 (valido para ghost_notes) e invalido para palm_mute (espera 60-100),
    entao a segunda tecnica tem que falhar mesmo com a primeira valida."""
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[
                StyleTechnique(name="bass.ghost_notes", parameters={"velocity": 30}),
                StyleTechnique(name="bass.palm_mute", parameters={"velocity": 30}),
            ],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(
        tmp_path, {"bass": ["bass.ghost_notes", "bass.palm_mute"]},
    )
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.bass.techniques[1].parameters.velocity"
    assert "bass.palm_mute.velocity" in exc.value.message


# --- precedencia + warning de conflito entre nivel de tecnica e de familia --


def test_technique_level_parameter_conflict_with_family_level_warns(tmp_path: Path):
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[
                StyleTechnique(name="bass.ghost_notes", parameters={"velocity": 45}),
            ],
            parameters={"velocity": 30},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"bass": ["bass.ghost_notes"]})
    warnings = validate(plan)
    assert any(
        "style.bass.techniques[0].parameters.velocity" in w
        and "style.bass.parameters.velocity=30" in w
        and "technique-scoped value 45" in w
        for w in warnings
    ), warnings


def test_no_conflict_warning_when_parameter_names_differ(tmp_path: Path):
    plan = _minimal_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[
                StyleTechnique(name="bass.ghost_notes", parameters={"velocity": 30}),
            ],
            parameters={"gate_pct": 15},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"bass": ["bass.ghost_notes"]})
    warnings = validate(plan)
    assert not any("overrides legacy" in w for w in warnings)


# --- intensity e evidence_refs: forma valida --------------------------------


def test_intensity_out_of_range_is_rejected(tmp_path: Path):
    plan = _minimal_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="drums.flam", intensity=1.5)],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"drums": ["drums.flam"]})
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].intensity"


def test_evidence_refs_must_be_non_empty_strings(tmp_path: Path):
    plan = _minimal_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="drums.flam", evidence_refs=[""])],
            parameters={},
        )
    }
    plan.brief_ref = _write_brief(tmp_path, {"drums": ["drums.flam"]})
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].evidence_refs[0]"


def test_evidence_refs_and_intensity_survive_round_trip():
    plan = _minimal_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[
                StyleTechnique(
                    name="drums.flam",
                    intensity=0.6,
                    evidence_refs=["f_drums_flam_1"],
                    parameters={"grace_velocity_ratio": 0.7},
                )
            ],
            parameters={},
        )
    }
    assert from_dict(to_dict(plan)) == plan
