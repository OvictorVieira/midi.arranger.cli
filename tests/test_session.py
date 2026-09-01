"""Testes da sessao de trabalho (issue #96).

Cobre:
- brief com `session` valido passa;
- brief SEM `session` continua valido (retrocompat byte-identica ao antigo);
- `intent` fora do vocabulario fechado rejeita;
- familia em `families_in_scope` fora de STYLE_FAMILIES rejeita;
- duplicata em `families_in_scope` rejeita;
- `created_at` fora de ISO-8601 UTC rejeita;
- plano com `session.families_in_scope=[bass]` e `style.drums.techniques[]`
  nao-vazio rejeita citando a familia fora do escopo;
- plano com `session.families_in_scope=[bass]` e elemento cujo role mapeia
  para drums rejeita;
- plano com `session.families_in_scope=[bass]` e edit em drums (sem tecnica)
  aceita — track de origem sai byte-identica;
- round-trip serializacao: to_dict/from_dict preserva o bloco session;
- `archive_session` cria `.midiarranger/sessions/` e escreve arquivo com
  nome deterministico;
- `archive_session` falha (SessionArchiveError) quando o mesmo id colide.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from tools import contract as _contract  # noqa: F401 — registra as tools
from tools.brief_ref import brief_sha256
from tools.plan import (
    ArrangementPlan,
    BriefRef,
    Element,
    FamilyStyle,
    PlanEdit,
    PlanSection,
    PlanSession,
    PlanValidationError,
    SourceMidi,
    StyleTechnique,
    from_dict,
    to_dict,
    validate,
)
from tools.registry import call
from tools.sessions import (
    SessionArchiveError,
    archive_session,
    session_filename,
    sessions_dir,
)

# --- fixtures --------------------------------------------------------------


def _valid_brief_with_session(session: dict[str, Any] | None) -> dict[str, Any]:
    from tests.test_contract_brief import _valid_brief

    brief = _valid_brief()
    if session is not None:
        brief["session"] = session
    return brief


def _valid_session_dict() -> dict[str, Any]:
    return {
        "id": "9c7c9a2e-9f2b-4d0a-8f2a-5b5b5b5b5b5b",
        "intent": "edit",
        "families_in_scope": ["bass", "drums"],
        "created_at": "2026-09-01T12:34:56Z",
    }


def _minimal_valid_plan() -> ArrangementPlan:
    """Plano minimo — sem elements/edits/style para os testes de sessao
    poderem acrescentar so o que exercitam."""
    return ArrangementPlan(
        version=1,
        seed=42,
        source_midi=SourceMidi(
            path="song.mid",
            sha256="a" * 64,
            tempo=120.0,
            key="A",
            bars=8,
        ),
        route="cinematica_emocional",
        sections=[
            PlanSection(
                label="INTRO",
                kind="intro",
                start_bar=0,
                end_bar=8,
                source="marker",
                protagonist="texture",
                energy={
                    "densidade": 2, "impacto": 1, "largura": 3,
                    "altura": 2, "instabilidade": 1,
                },
            ),
        ],
        elements=[],
    )


def _plan_with_session(families: list[str]) -> ArrangementPlan:
    plan = _minimal_valid_plan()
    plan.session = PlanSession(
        id="session-01",
        intent="edit",
        families_in_scope=list(families),
        created_at="2026-09-01T12:34:56Z",
    )
    return plan


def _write_brief_authorizing(
    tmp_path: Path, family: str, canonical: str,
) -> tuple[Path, str]:
    brief = {
        "style": {
            fam: {
                "authorized_techniques": (
                    [canonical] if fam == family else []
                ),
            }
            for fam in ("bass", "drums", "guitar", "keys")
        },
    }
    path = tmp_path / "arrangement-brief.json"
    path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    return path, brief_sha256(path)


# --- brief: session opcional / valido / invariancia ----------------------


def test_brief_without_session_is_still_valid():
    """AC-regressao: brief antigo (sem `session`) continua carregando limpo."""
    env = call("brief.validate", {"brief": _valid_brief_with_session(None)})
    assert env["ok"] is True, env


def test_brief_with_valid_session_passes():
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(_valid_session_dict())},
    )
    assert env["ok"] is True, env


def test_brief_with_valid_session_is_not_mutated():
    brief = _valid_brief_with_session(_valid_session_dict())
    snapshot = copy.deepcopy(brief)
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is True
    assert brief == snapshot


# --- brief: session invalido -------------------------------------------


def test_brief_session_intent_out_of_vocabulary_rejects():
    session = _valid_session_dict()
    session["intent"] = "reharmonize"  # nao esta no vocabulario fechado
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "session.intent"


def test_brief_session_family_out_of_style_families_rejects():
    session = _valid_session_dict()
    session["families_in_scope"] = ["bass", "vocal"]
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert "session.families_in_scope" in env["error"]["path"]


def test_brief_session_family_duplicate_rejects():
    session = _valid_session_dict()
    session["families_in_scope"] = ["bass", "drums", "bass"]
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_SESSION_INVALID"
    assert env["error"]["path"] == "session.families_in_scope"


def test_brief_session_created_at_not_iso_utc_rejects():
    session = _valid_session_dict()
    session["created_at"] = "2026-09-01"  # falta hora/UTC
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "session.created_at"


def test_brief_session_created_at_bad_calendar_date_rejects():
    session = _valid_session_dict()
    session["created_at"] = "2026-02-30T12:00:00Z"  # 30 fev nao existe
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_SESSION_INVALID"
    assert env["error"]["path"] == "session.created_at"


def test_brief_session_id_empty_rejects():
    session = _valid_session_dict()
    session["id"] = ""
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "session.id"


def test_brief_session_missing_field_rejects():
    session = _valid_session_dict()
    del session["intent"]
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert "session.intent" in env["error"]["path"]


def test_brief_session_extra_field_rejects():
    session = _valid_session_dict()
    session["mood"] = "focused"  # additionalProperties=False
    env = call(
        "brief.validate",
        {"brief": _valid_brief_with_session(session)},
    )
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert "session.mood" in env["error"]["path"]


# --- plan: session valida / retrocompat --------------------------------


def test_plan_without_session_still_validates():
    """Regressao: plano sem `session` continua valido — modo monolitico."""
    validate(_minimal_valid_plan())


def test_plan_with_session_round_trip_preserves_bloco():
    plan = _plan_with_session(["bass"])
    data = to_dict(plan)
    assert data["session"] == {
        "id": "session-01",
        "intent": "edit",
        "families_in_scope": ["bass"],
        "created_at": "2026-09-01T12:34:56Z",
    }
    assert from_dict(data) == plan


def test_plan_session_intent_out_of_vocabulary_rejects():
    plan = _plan_with_session(["bass"])
    plan.session.intent = "reharmonize"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "session.intent"


def test_plan_session_family_out_of_style_families_rejects():
    plan = _plan_with_session(["bass", "vocal"])
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "session.families_in_scope[1]"


def test_plan_session_duplicate_families_rejects():
    plan = _plan_with_session(["bass", "bass"])
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "session.families_in_scope"
    assert "duplicate" in exc.value.message


def test_plan_session_created_at_not_iso_utc_rejects():
    plan = _plan_with_session(["bass"])
    plan.session.created_at = "2026-09-01"
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "session.created_at"


# --- plan: fronteira de escopo (o coracao da issue #96) ------------------


def test_plan_session_scope_rejects_style_technique_outside_scope(tmp_path: Path):
    """`session.families_in_scope=[bass]` + `style.drums.techniques[]` nao
    vazio deve falhar citando `style.drums.techniques` e o escopo violado.
    """
    brief_path, sha = _write_brief_authorizing(
        tmp_path, "drums", "drums.ghost_notes",
    )
    plan = _plan_with_session(["bass"])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "drums": FamilyStyle(
            reference="Steve Jordan",
            researched_at="2026-08-24",
            sources=["https://example.test/drums"],
            confidence="high",
            techniques=[StyleTechnique(name="drums.ghost_notes")],
            parameters={},
        ),
    }
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "style.drums.techniques"
    assert "drums" in exc.value.message
    assert "session.families_in_scope" in exc.value.message


def test_plan_session_scope_rejects_element_outside_scope():
    """`session.families_in_scope=[bass]` + elemento de role drums (drum_groove)
    deve falhar citando o role/familia fora do escopo."""
    plan = _plan_with_session(["bass"])
    plan.elements = [
        Element(
            id="drums_verse",
            role="drum_groove",
            sections=["INTRO"],
            register=[35, 60],
            layers=1,
            sync_role="kick_support",
            articulation="tight",
            harmony="percussion",
            rationale="linha rítmica de suporte",
        ),
    ]
    with pytest.raises(PlanValidationError) as exc:
        validate(plan)
    assert exc.value.path == "elements[0].role"
    assert "drums" in exc.value.message


def test_plan_session_scope_accepts_edit_of_out_of_scope_family(tmp_path: Path):
    """`session.families_in_scope=[bass]` + `plan.edits[]` em drums
    (sem tecnica) deve passar — track de origem sai byte-identica."""
    plan = _plan_with_session(["bass"])
    plan.edits = [PlanEdit(track="Drums", profile="drums", intensity=0.3)]
    validate(plan)  # nao levanta


def test_plan_session_scope_accepts_in_scope_style_and_elements(tmp_path: Path):
    """`session.families_in_scope=[bass]` + tecnica de bass + elemento bass
    deve passar — a familia esta em escopo."""
    brief_path, sha = _write_brief_authorizing(
        tmp_path, "bass", "bass.ghost_notes",
    )
    plan = _plan_with_session(["bass"])
    plan.brief_ref = BriefRef(path=str(brief_path), sha256=sha)
    plan.style = {
        "bass": FamilyStyle(
            reference="James Jamerson",
            researched_at="2026-08-24",
            sources=["https://example.test/bass"],
            confidence="high",
            techniques=[StyleTechnique(name="bass.ghost_notes")],
            parameters={},
        ),
    }
    plan.elements = [
        Element(
            id="bass_verse",
            role="bass",
            sections=["INTRO"],
            register=[28, 55],
            layers=1,
            sync_role="kick_support",
            articulation="tight",
            harmony="follow_chords",
            rationale="linha base do baixo",
        ),
    ]
    validate(plan)


# --- persistencia --------------------------------------------------------


def test_archive_session_creates_directory_and_deterministic_filename(
    tmp_path: Path,
):
    plan = _plan_with_session(["bass", "drums"])
    written = archive_session(plan, tmp_path)

    expected_dir = sessions_dir(tmp_path)
    assert expected_dir.is_dir()
    assert written.parent == expected_dir
    assert written.name == "session-01-edit-bass-drums.json"

    reloaded = from_dict(json.loads(written.read_text(encoding="utf-8")))
    assert reloaded == plan


def test_archive_session_empty_families_uses_all_marker(tmp_path: Path):
    plan = _plan_with_session([])
    written = archive_session(plan, tmp_path)
    assert written.name == "session-01-edit-all.json"


def test_archive_session_append_only_refuses_overwrite(tmp_path: Path):
    plan = _plan_with_session(["bass"])
    archive_session(plan, tmp_path)
    with pytest.raises(SessionArchiveError) as exc:
        archive_session(plan, tmp_path)
    assert "ja existe" in str(exc.value)
    assert "session-01" in str(exc.value)


def test_archive_session_requires_session_on_plan(tmp_path: Path):
    plan = _minimal_valid_plan()  # session=None
    with pytest.raises(SessionArchiveError):
        archive_session(plan, tmp_path)


def test_session_filename_is_deterministic():
    session = PlanSession(
        id="abc",
        intent="layer",
        families_in_scope=["keys", "guitar"],
        created_at="2026-09-01T00:00:00Z",
    )
    # A ordem das familias segue a do plano — mesmo input, mesmo nome.
    assert session_filename(session) == "abc-layer-keys-guitar.json"
