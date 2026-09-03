"""Testes do schema/validador do arrangement-plan.json (US-009)."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
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
    load_brief_instrument_tuning,
    normalize_style_defaults,
    to_dict,
    validate,
    validate_edits_against_midi,
)
from tools.techniques import SUPPORTED_TECHNIQUES, build_index

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


def test_brief_sha256_hashes_exact_file_bytes(tmp_path: Path):
    brief = tmp_path / "arrangement-brief.json"
    content = b'{"input_midi":"song.mid"}\n'
    brief.write_bytes(content)

    assert brief_sha256(brief) == hashlib.sha256(content).hexdigest()


def _attach_authorized_brief(plan: ArrangementPlan, tmp_path: Path) -> None:
    """Anexa `plan.brief_ref` autorizando exatamente as tecnicas em `plan.style`.

    Depois de US-003, plano sem `brief_ref` e com `style.<fam>.techniques[]`
    nao vazia e erro de validacao. Este helper e o atalho para os testes que
    declaram tecnicas para exercitar OUTRA regra (parametro, apelido,
    idempotencia, ...) e nao a autorizacao em si.
    """
    authorized: dict[str, list[str]] = {}
    if isinstance(plan.style, dict):
        for family, entry in plan.style.items():
            if not isinstance(entry, FamilyStyle):
                continue
            names = [
                t.name for t in entry.techniques if isinstance(t, StyleTechnique)
            ]
            if names:
                authorized[family] = names
    brief_path, sha = _write_brief(tmp_path, authorized)
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)


def _write_brief(
    tmp_path: Path,
    authorized: dict[str, list[str]] | None = None,
) -> tuple[Path, str]:
    """Grava um brief minimo em `tmp_path/arrangement-brief.json`.

    O plan.validate so olha `brief.style.<familia>.authorized_techniques`;
    o resto do brief nao entra na conta. A fixture segue essa fronteira.
    """
    authorized = authorized or {}
    style_dict = {
        family: {"authorized_techniques": list(authorized.get(family, []))}
        for family in ("bass", "drums", "guitar", "keys")
    }
    brief = {"style": style_dict}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return brief_path, brief_sha256(brief_path)


def test_plan_accepts_valid_brief_ref(tmp_path: Path):
    plan = _valid_plan()
    brief_path, sha = _write_brief(tmp_path)
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    validate(plan)
    assert from_dict(to_dict(plan)) == plan


def test_validate_rejects_malformed_brief_ref_sha256():
    plan = _valid_plan()
    plan.brief_ref = BriefRef(
        path="arrangement-brief.json",
        sha256="A" * 64,
    )

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "brief_ref.sha256"
    assert "64 lowercase hexadecimal" in exc.value.message


def test_validate_rejects_missing_brief_file(tmp_path: Path):
    plan = _valid_plan()
    missing = tmp_path / "nao-existe.json"
    plan.brief_ref = BriefRef(path=str(missing), sha256="0" * 64)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "brief_ref.path"
    assert "not found" in exc.value.message


def test_validate_rejects_brief_sha256_mismatch(tmp_path: Path):
    plan = _valid_plan()
    brief_path, _sha = _write_brief(tmp_path)
    plan.brief_ref = BriefRef(path=str(brief_path), sha256="0" * 64)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "brief_ref.sha256"
    assert "mismatch" in exc.value.message


# --- issue #44 / PR #64 (achado P1) — instruments alimenta o pipeline ------

def _write_brief_with_instruments(
    tmp_path: Path, instruments: dict,
) -> tuple[Path, str]:
    style_dict = {
        family: {"authorized_techniques": []}
        for family in ("bass", "drums", "guitar", "keys")
    }
    brief = {"style": style_dict, "instruments": instruments}
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return brief_path, brief_sha256(brief_path)


def test_load_brief_instrument_tuning_reads_declared_notes(tmp_path: Path):
    """A declaracao de `instruments.guitar.tuning.notes` (issue #44) tem que
    chegar ao chamador do render — antes desta correcao, `instruments` era
    validado e ignorado (achado P1 do PR #64)."""
    plan = _valid_plan()
    instruments = {
        "guitar": {
            "known": True,
            "strings": 7,
            "tuning": {"name": None, "notes": [32, 37, 42, 47, 51, 56, 61]},
        },
    }
    brief_path, sha = _write_brief_with_instruments(tmp_path, instruments)
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    tuning = load_brief_instrument_tuning(plan, tmp_path)

    assert tuning == {"guitar": (32, 37, 42, 47, 51, 56, 61)}


def test_load_brief_instrument_tuning_resolves_name_when_notes_absent(
    tmp_path: Path,
):
    plan = _valid_plan()
    instruments = {
        "guitar": {
            "known": True,
            "strings": 6,
            "tuning": {"name": "E padrao", "notes": []},
        },
    }
    brief_path, sha = _write_brief_with_instruments(tmp_path, instruments)
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    tuning = load_brief_instrument_tuning(plan, tmp_path)

    assert tuning.get("guitar") == (40, 45, 50, 55, 59, 64)


def test_load_brief_instrument_tuning_skips_unknown_family(tmp_path: Path):
    plan = _valid_plan()
    instruments = {
        "bass": {
            "known": False,
            "strings": None,
            "tuning": None,
            "playing_style": None,
            "notation": None,
        },
    }
    brief_path, sha = _write_brief_with_instruments(tmp_path, instruments)
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    assert load_brief_instrument_tuning(plan, tmp_path) == {}


def test_load_brief_instrument_tuning_empty_without_brief_ref():
    plan = _valid_plan()
    assert load_brief_instrument_tuning(plan, None) == {}


def test_validate_accepts_authorized_style_technique(tmp_path: Path):
    plan = _valid_plan()
    brief_path, sha = _write_brief(tmp_path, {"drums": ["drums.ghost_notes"]})
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "drums": FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    validate(plan)  # nao levanta


def test_validate_rejects_unauthorized_style_technique(tmp_path: Path):
    plan = _valid_plan()
    # brief autoriza NADA em drums, embora o plano declare uma tecnica.
    brief_path, sha = _write_brief(tmp_path, {"drums": []})
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "drums": FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].name"
    assert "drums.ghost_notes" in exc.value.message
    assert "drums" in exc.value.message
    assert "authorized_techniques" in exc.value.message


def test_validate_accepts_short_name_when_canonical_is_authorized(tmp_path: Path):
    """Autorizacao por canonical casa com plano que usa apelido curto e vice-versa."""
    plan = _valid_plan()
    brief_path, sha = _write_brief(tmp_path, {"drums": ["ghost_notes"]})
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "drums": FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    validate(plan)


@pytest.mark.parametrize("family", ["bass", "drums", "guitar", "keys"])
def test_validate_rejects_style_technique_without_brief_ref(family: str):
    """Sem `brief_ref` nenhuma tecnica pode ser aplicada — vale para as 4 familias."""
    plan = _valid_plan()
    plan.brief_ref = None
    canonical_by_family = {
        "bass": "bass.ghost_notes",
        "drums": "drums.ghost_notes",
        "guitar": "guitar.palm_mute",
        "keys": "keys.arpeggio_broken_chord",
    }
    canonical = canonical_by_family[family]
    plan.style = {
        family: FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name=canonical)],
            parameters={},
        ),
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == f"style.{family}.techniques[0].name"
    assert canonical in exc.value.message
    assert family in exc.value.message
    assert "no brief_ref" in exc.value.message


def test_validate_accepts_style_without_techniques_and_without_brief_ref():
    """Plano sem `brief_ref` e sem tecnica em qualquer familia continua valido."""
    plan = _valid_plan()
    plan.brief_ref = None
    plan.style = {
        family: FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[],
            parameters={},
        )
        for family in ("bass", "drums", "guitar", "keys")
    }
    validate(plan)  # caminho de quem so usa plan.edits


def test_validate_resolves_relative_brief_path_against_plan_dir(tmp_path: Path):
    """`brief_ref.path` relativo e resolvido contra `plan_dir` — como `plan.load` faz."""
    plan = _valid_plan()
    brief_path, sha = _write_brief(tmp_path)
    plan.brief_ref = BriefRef(path="arrangement-brief.json", sha256=sha)

    validate(plan, plan_dir=tmp_path)  # nao levanta; encontra o brief pelo diretorio

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)  # sem plan_dir, path relativo nao encontra o arquivo
    assert exc.value.path == "brief_ref.path"


def test_validate_rejects_unreadable_brief_file(tmp_path: Path):
    """Brief que existe mas nao pode ser lido vira erro explicito no path."""
    brief_path, sha = _write_brief(tmp_path)
    plan = _valid_plan()
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    brief_path.chmod(0o000)
    try:
        with pytest.raises(PlanValidationError) as exc:
            validate(plan)
    finally:
        brief_path.chmod(0o644)
    assert exc.value.path == "brief_ref.path"
    assert "could not read" in exc.value.message


def test_validate_rejects_brief_with_invalid_json(tmp_path: Path):
    """Brief com JSON quebrado (usuario editou a mao) vira erro explicito."""
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text("{not valid json", encoding="utf-8")
    sha = brief_sha256(brief_path)
    plan = _valid_plan()
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "brief_ref.path"
    assert "could not parse" in exc.value.message


def test_validate_rejects_brief_root_not_object(tmp_path: Path):
    """Brief cuja raiz e um array JSON nao passa — precisa ser objeto."""
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text("[]", encoding="utf-8")
    sha = brief_sha256(brief_path)
    plan = _valid_plan()
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "brief_ref.path"
    assert "JSON object" in exc.value.message


def test_validate_rejects_brief_without_style_object(tmp_path: Path):
    """Brief sem `style` (nao ha o que autorizar) vira erro explicito."""
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(json.dumps({"input_midi": "x.mid"}), encoding="utf-8")
    sha = brief_sha256(brief_path)
    plan = _valid_plan()
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "brief_ref.path"
    assert "'style'" in exc.value.message


def test_validate_treats_non_dict_family_as_no_authorization(tmp_path: Path):
    """Familia com forma quebrada no brief e tratada como 'nada autorizado'."""
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({"style": {"drums": "boom-bap"}}), encoding="utf-8"
    )
    sha = brief_sha256(brief_path)
    plan = _valid_plan()
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "drums": FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].name"


def test_validate_treats_non_list_authorized_techniques_as_empty(tmp_path: Path):
    """`authorized_techniques` com forma quebrada e tratado como '[]', nunca 'tudo'."""
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({
            "style": {"drums": {"authorized_techniques": "drums.ghost_notes"}}
        }),
        encoding="utf-8",
    )
    sha = brief_sha256(brief_path)
    plan = _valid_plan()
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "drums": FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].name"


def test_validate_ignores_non_string_entries_in_authorized(tmp_path: Path):
    """Entrada nao-string em `authorized_techniques` e ignorada sem autorizar nada."""
    brief_path = tmp_path / "arrangement-brief.json"
    brief_path.write_text(
        json.dumps({
            "style": {
                "drums": {"authorized_techniques": [None, 42, "drums.ghost_notes"]},
            },
        }),
        encoding="utf-8",
    )
    sha = brief_sha256(brief_path)
    plan = _valid_plan()
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "drums": FamilyStyle(
            reference="X",
            researched_at="2026-08-26",
            sources=["https://example.test/x"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    validate(plan)  # a entrada valida "drums.ghost_notes" autoriza; as demais somem


@pytest.mark.parametrize(
    ("field", "path"),
    [
        ("path", "brief_ref.path"),
        ("sha256", "brief_ref.sha256"),
    ],
)
def test_from_dict_rejects_partial_brief_ref(field: str, path: str):
    data = to_dict(_valid_plan())
    data["brief_ref"] = {
        "path": "arrangement-brief.json",
        "sha256": "0" * 64,
    }
    del data["brief_ref"][field]

    with pytest.raises(PlanValidationError) as exc:
        from_dict(data)

    assert exc.value.path == path


def test_load_reads_hand_written_json(tmp_path: Path):
    """Usuario editando o JSON a mao deve conseguir carregar sem drama."""
    data = to_dict(_valid_plan())
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    plan = load(path)
    assert plan.route == "hook_eletronico_pesado"


def _plan_with_style(*, researched_at: str) -> ArrangementPlan:
    """Plano valido cujo unico ponto sob teste e `style.bass.researched_at`."""
    plan = _valid_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at=researched_at,
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[],
            parameters={},
        ),
    }
    return plan


def _complete_style() -> dict[str, FamilyStyle]:
    return {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[],
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


def test_validate_accepts_complete_style_for_all_four_families(tmp_path: Path):
    plan = _valid_plan()
    plan.style = _complete_style()
    _attach_authorized_brief(plan, tmp_path)
    validate(plan)  # nao levanta


def test_normalize_style_defaults_adds_default_for_used_family_without_style():
    plan = _valid_plan()
    plan.assumptions = []
    validate(plan)  # plano sem style continua valido.

    normalized = normalize_style_defaults(plan)

    assert normalized.style is not None
    assert normalized.style["keys"].confidence == "default"
    assert normalized.style["keys"].reference == "persona base"
    assert len(normalized.assumptions) == 1
    assert "keys" in normalized.assumptions[0]
    assert "persona base" in normalized.assumptions[0]
    assert plan.style is None
    assert plan.assumptions == []


def test_normalize_style_defaults_only_fills_used_families_missing_from_style():
    plan = _valid_plan()
    plan.assumptions = []
    plan.edits = [
        PlanEdit(track="Drums", profile="drums", intensity=0.5),
        PlanEdit(track="Bass", profile="bass", intensity=0.5),
    ]
    drums_style = FamilyStyle(
        reference="Steve Jordan",
        researched_at="2026-08-24",
        sources=["https://example.test/drums"],
        confidence="high",
        techniques=[],
        parameters={},
    )
    plan.style = {"drums": drums_style}
    validate(plan)  # familia sem entrada em style e valida.

    normalized = normalize_style_defaults(plan)

    assert normalized.style is not None
    assert normalized.style["drums"] == drums_style
    assert normalized.style["bass"].confidence == "default"
    assert normalized.style["keys"].confidence == "default"
    assert "guitar" not in normalized.style
    assert len(normalized.assumptions) == 2
    assert any("bass" in assumption for assumption in normalized.assumptions)
    assert any("keys" in assumption for assumption in normalized.assumptions)


def test_validate_resolves_simple_style_technique_name_by_family(tmp_path: Path):
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
    _attach_authorized_brief(plan, tmp_path)
    validate(plan)  # `ghost_notes` existe em drums e bass; o path desambigua.


def test_validate_accepts_canonical_style_technique_name(tmp_path: Path):
    plan = _valid_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        )
    }
    _attach_authorized_brief(plan, tmp_path)
    validate(plan)  # nao levanta


def test_validate_rejects_documented_but_unimplemented_style_technique(tmp_path: Path):
    plan = _valid_plan()
    plan.style = {
        "keys": FamilyStyle(
            reference="Pianist research",
            researched_at="2026-08-24",
            sources=["https://example.test/keys"],
            confidence="high",
            techniques=[StyleTechnique(name="keys.hand_asynchrony")],
            parameters={},
        )
    }
    _attach_authorized_brief(plan, tmp_path)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "style.keys.techniques[0].name"
    assert "exists in techniques index" in exc.value.message
    assert "not implemented by the engine" in exc.value.message
    assert "drums.ghost_notes" in exc.value.message


def test_validate_accepts_only_supported_style_techniques_from_manual_index(tmp_path: Path):
    index = build_index()

    for technique in index.techniques:
        plan = _valid_plan()
        plan.style = {
            technique.family: FamilyStyle(
                reference="Style research",
                researched_at="2026-08-24",
                sources=["https://example.test/style"],
                confidence="high",
                techniques=[StyleTechnique(name=technique.canonical)],
                parameters={},
            )
        }
        _attach_authorized_brief(plan, tmp_path)

        if technique.canonical in SUPPORTED_TECHNIQUES:
            validate(plan)
            continue

        with pytest.raises(PlanValidationError) as exc:
            validate(plan)
        assert exc.value.path == f"style.{technique.family}.techniques[0].name"
        assert "not implemented by the engine" in exc.value.message


def test_validate_rejects_unknown_style_technique_with_exact_path_and_candidates(tmp_path: Path):
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
    _attach_authorized_brief(plan, tmp_path)
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques[0].name"
    assert "drums.flam" in exc.value.message


def test_validate_rejects_style_technique_from_other_family(tmp_path: Path):
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
    _attach_authorized_brief(plan, tmp_path)
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


def test_validate_rejects_invalid_style_confidence(tmp_path: Path):
    plan = _valid_plan()
    plan.style = _complete_style()
    _attach_authorized_brief(plan, tmp_path)
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


def test_from_dict_rejects_musical_content_key_inside_style():
    data = to_dict(_valid_plan())
    data["style"] = {
        "bass": {
            "reference": "James Jamerson",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/bass"],
            "confidence": "high",
            "techniques": [],
            "parameters": {},
            "notes": [40, 42, 45],
        }
    }
    with pytest.raises(PlanValidationError) as exc:
        from_dict(data)
    assert exc.value.path == "style.bass.notes"
    assert "nunca conteudo musical" in exc.value.message


def test_validate_accepts_style_parameter_range_pair():
    plan = _valid_plan()
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
    validate(plan)  # par [min, max] e parametro de tecnica, nao conteudo.


def test_validate_accepts_style_parameter_inside_manual_range(tmp_path: Path):
    plan = _valid_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="ghost_notes")],
            parameters={"velocity": 35},
        )
    }
    _attach_authorized_brief(plan, tmp_path)
    validate(plan)  # velocity de drums.ghost_notes aceita 20-45.


def test_validate_rejects_style_parameter_below_manual_range(tmp_path: Path):
    plan = _valid_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="ghost_notes")],
            parameters={"velocity": 19},
        )
    }
    _attach_authorized_brief(plan, tmp_path)
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.parameters.velocity"
    assert "19" in exc.value.message
    assert "[20, 45]" in exc.value.message


def test_validate_rejects_style_parameter_above_manual_range(tmp_path: Path):
    plan = _valid_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="ghost_notes")],
            parameters={"velocity": 46},
        )
    }
    _attach_authorized_brief(plan, tmp_path)
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.parameters.velocity"
    assert "46" in exc.value.message
    assert "[20, 45]" in exc.value.message


def test_validate_accepts_style_parameter_without_manual_range(tmp_path: Path):
    plan = _valid_plan()
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="medium",
            techniques=[StyleTechnique(name="ghost_notes")],
            parameters={"hard_ceiling": 999},
        )
    }
    _attach_authorized_brief(plan, tmp_path)
    warnings = validate(plan)
    assert not any("style.drums.parameters.hard_ceiling" in w for w in warnings)


def test_validate_warns_for_style_parameter_source_gap(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    monkeypatch.setattr(
        "tools.techniques.SUPPORTED_TECHNIQUES",
        (*SUPPORTED_TECHNIQUES, "guitar.palm_mute"),
    )
    plan = _valid_plan()
    plan.style = {
        "guitar": FamilyStyle(
            reference="Meshuggah",
            researched_at="2026-08-24",
            sources=["https://example.test/guitar"],
            confidence="medium",
            techniques=[StyleTechnique(name="palm_mute")],
            parameters={"gate_absoluto_ms": 999},
        )
    }
    _attach_authorized_brief(plan, tmp_path)
    warnings = validate(plan)
    assert any(
        "style.guitar.parameters.gate_absoluto_ms" in warning
        and "source gap" in warning
        for warning in warnings
    )


def test_validate_rejects_midi_integer_sequence_under_innocent_style_parameter():
    plan = _valid_plan()
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[],
            parameters={"accent_shape": [40, 42, 45]},
        )
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.bass.parameters.accent_shape"
    assert "nunca conteudo musical" in exc.value.message


def test_from_dict_rejects_pitch_time_event_array_inside_style():
    data = to_dict(_valid_plan())
    data["style"] = {
        "drums": {
            "reference": "Steve Jordan",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/drums"],
            "confidence": "medium",
            "techniques": [],
            "parameters": {"accent_map": [{"pitch": 38, "time": 0.0}]},
        }
    }
    with pytest.raises(PlanValidationError) as exc:
        from_dict(data)
    assert exc.value.path == "style.drums.parameters.accent_map"
    assert "altura e tempo" in exc.value.message


def test_validate_rejects_musical_content_parameter_name_even_when_scalar():
    plan = _valid_plan()
    plan.style = {
        "keys": FamilyStyle(
            reference="Nigel Godrich",
            researched_at="2026-08-24",
            sources=["https://example.test/keys"],
            confidence="low",
            techniques=[],
            parameters={"groove": 0.25},
        )
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.keys.parameters.groove"
    assert "nunca conteudo musical" in exc.value.message


def test_validate_rejects_style_that_is_not_mapping():
    plan = _valid_plan()
    plan.style = ["drums"]

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "style"
    assert "must be dict" in exc.value.message


def test_validate_reports_technique_index_build_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
):
    from tools.techniques import TechniqueError

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
    # Anexa o brief antes do monkeypatch: `_load_brief_authorized_techniques`
    # tambem chama `build_index()`, e o teste so quer exercitar a rota do
    # validador de style.
    _attach_authorized_brief(plan, tmp_path)

    def fail_build_index():
        raise TechniqueError("manual quebrado")

    monkeypatch.setattr("tools.techniques.build_index", fail_build_index)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "style.techniques"
    assert "manual quebrado" in exc.value.message


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda entry: setattr(entry, "researched_at", "ontem"), "style.drums.researched_at"),
        (lambda entry: setattr(entry, "sources", "https://example.test/drums"), "style.drums.sources"),
        (lambda entry: setattr(entry, "techniques", "ghost_notes"), "style.drums.techniques"),
        (lambda entry: setattr(entry, "techniques", [{"name": "ghost_notes"}]), "style.drums.techniques[0]"),
        (
            lambda entry: setattr(entry, "techniques", [StyleTechnique(name="ghost_notes", density="alta")]),
            "style.drums.techniques[0].density",
        ),
        (
            lambda entry: setattr(entry, "techniques", [StyleTechnique(name="ghost_notes", density=1.5)]),
            "style.drums.techniques[0].density",
        ),
        (
            lambda entry: setattr(entry, "techniques", [StyleTechnique(name="ghost_notes", rationale=123)]),
            "style.drums.techniques[0].rationale",
        ),
        (lambda entry: setattr(entry, "parameters", []), "style.drums.parameters"),
        (lambda entry: entry.parameters.update({"": 0.5}), "style.drums.parameters"),
        (lambda entry: entry.parameters.update({"timing": "late"}), "style.drums.parameters.timing"),
    ],
)
def test_validate_rejects_malformed_style_family_values(mutate, path: str, tmp_path: Path):
    entry = FamilyStyle(
        reference="Steve Jordan",
        researched_at="2026-08-24",
        sources=["https://example.test/drums"],
        confidence="medium",
        techniques=[],
        parameters={},
    )
    mutate(entry)
    plan = _valid_plan()
    plan.style = {"drums": entry}
    # Autoriza `ghost_notes` para que o teste exercite a validacao de
    # tipo/densidade/rationale, e nao a de "sem brief_ref".
    brief_path, sha = _write_brief(tmp_path, {"drums": ["ghost_notes"]})
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == path


def test_validate_rejects_style_family_value_that_is_not_family_style():
    plan = _valid_plan()
    plan.style = {"drums": {"reference": "Steve Jordan"}}

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "style.drums"
    assert "FamilyStyle" in exc.value.message


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda data: data.update({"style": []}), "style"),
        (lambda data: data.update({"brief_ref": "arrangement-brief.json"}), "brief_ref"),
        (lambda data: data["style"].update({"drums": []}), "style.drums"),
        (
            lambda data: data["style"]["drums"].update({"sources": "https://example.test/drums"}),
            "style.drums.sources",
        ),
        (lambda data: data["style"]["drums"].update({"techniques": {}}), "style.drums.techniques"),
        (lambda data: data["style"]["drums"].update({"parameters": []}), "style.drums.parameters"),
        (
            lambda data: data["style"]["drums"].update({"techniques": [42]}),
            "style.drums.techniques[0]",
        ),
    ],
)
def test_from_dict_rejects_malformed_style_structures(mutate, path: str):
    data = to_dict(_valid_plan())
    data["style"] = {
        "drums": {
            "reference": "Steve Jordan",
            "researched_at": "2026-08-24",
            "sources": ["https://example.test/drums"],
            "confidence": "medium",
            "techniques": [],
            "parameters": {},
        }
    }
    mutate(data)

    with pytest.raises(PlanValidationError) as exc:
        from_dict(data)

    assert exc.value.path == path


@pytest.mark.parametrize(
    ("mutate", "path"),
    [
        (lambda plan: setattr(plan.sections[0], "energy", "alto"), "sections[0].energy"),
        (lambda plan: setattr(plan.elements[0], "role", 123), "elements[0].role"),
        (lambda plan: setattr(plan.elements[0], "register", (48, 72)), "elements[0].register"),
        (lambda plan: setattr(plan.elements[0], "register", [48, "72"]), "elements[0].register[1]"),
    ],
)
def test_validate_rejects_malformed_core_plan_fields(mutate, path: str):
    plan = _valid_plan()
    mutate(plan)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == path


def test_dump_writes_indented_json_that_parses(tmp_path: Path):
    """Serializacao gera JSON legivel (indent=2) — usuario edita a mao."""
    path = tmp_path / "plan.json"
    dump(_valid_plan(), path)
    text = path.read_text(encoding="utf-8")
    assert "\n" in text  # indentado
    data = json.loads(text)
    assert data["version"] == 1


# --- rejeicoes: mensagens carregam path exato ------------------------------

def test_rejects_non_string_dimensions_changed_entry():
    """Codex finding do PR #106: um plano em memoria (ou lido direto via
    `from_dict`) com item nao-string em `transitions[i].dimensions_changed`
    tinha que ser aceito por `validate()` e so quebrava depois, dentro de
    `tools.validators.transitions._normalize_dimension_name` (`AttributeError`
    em `.strip()`), com o pipeline de render ja rodado. `validate()` agora
    rejeita isso cedo, com path exato."""
    plan = _valid_plan()
    plan.transitions[0].dimensions_changed = ["densidade", 42]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "transitions[0].dimensions_changed[1]"
    assert "str" in exc.value.message


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


def test_rejects_element_without_rationale():
    data = to_dict(_valid_plan())
    del data["elements"][0]["rationale"]

    with pytest.raises(PlanValidationError) as exc:
        from_dict(data)

    assert exc.value.path == "elements[0].rationale"
    assert "missing required field" in exc.value.message


@pytest.mark.parametrize("rationale", ["", "   "])
def test_rejects_element_with_blank_rationale(rationale: str):
    plan = _valid_plan()
    plan.elements[0].rationale = rationale

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path == "elements[0].rationale"
    assert "after strip" in exc.value.message


def test_accepts_element_with_nonempty_rationale():
    plan = _valid_plan()
    plan.elements[0].rationale = "Pad cria sustentacao antes do refrao."

    validate(plan)


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


def test_validate_rejects_blank_edit_tool():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5, tool="   ")]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].tool"


def test_validate_rejects_edit_tool_that_normalizes_to_nothing():
    """Achado do Codex: 'tool' so com separador/pontuacao normaliza para
    string vazia em `render._normalize_tool_name`, virando `None` e caindo
    no fallback `generic` silencioso — exatamente o no-op que `edit.tool`
    foi criado para evitar. Precisa ser rejeitado antes do render, nao
    convertido em silencio."""
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5, tool="!!!")]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].tool"


def test_validate_accepts_valid_edit_tool():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5, tool="MODO Bass")]
    validate(plan)  # nao levanta


def test_validate_rejects_edit_tool_with_pipe():
    """Achado do Codex: 'edit.tool' vira 'plugin' no carimbo
    (`_stamp_edit_tracks`), que reserva '|' como separador de campo —
    sem checar aqui, o erro so aparecia tarde, dentro de `_format_stamp`
    no render, em vez de na validacao do plano."""
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5, tool="MODO|Bass")]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].tool"


def test_validate_rejects_non_ascii_edit_tool():
    plan = _valid_plan()
    plan.edits = [PlanEdit(track="Bass", profile="bass", intensity=0.5, tool="MÖDO Bass")]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "edits[0].tool"


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


@pytest.mark.parametrize("valor", ["20260824", "2026-W35-1", "2026-8-4", "2026/08/24"])
def test_researched_at_rejects_what_the_facade_would_reject(valor):
    """Dominio e fachada precisam recusar exatamente as mesmas datas.

    `date.fromisoformat` sozinho aceita `20260824` e `2026-W35-1`, que o JSON
    Schema da fachada recusa pelo padrao `YYYY-MM-DD`. Aceitar no dominio o que
    a fachada nega e ter duas verdades sobre a mesma data — o plano passaria
    pela API Python e quebraria na tool.
    """
    plan = _plan_with_style(researched_at=valor)

    with pytest.raises(PlanValidationError) as exc:
        validate(plan)

    assert exc.value.path.endswith(".researched_at")


def test_researched_at_accepts_a_real_iso_date():
    validate(_plan_with_style(researched_at="2026-08-24"))


def test_researched_at_rejects_an_impossible_calendar_date():
    with pytest.raises(PlanValidationError) as exc:
        validate(_plan_with_style(researched_at="2026-02-30"))

    assert "calendar date" in str(exc.value)
