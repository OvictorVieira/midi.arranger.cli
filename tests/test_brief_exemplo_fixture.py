"""Testes do brief de exemplo (US-005).

O `tests/fixtures/brief_exemplo.json` cumpre dois papeis:

- exemplo real para leitura humana — mostra como um brief completo se parece
  para o proximo agente que for editar a skill ou o schema;
- fixture executavel — o resto dos testes valida contra ele para garantir que
  o schema segue aceitando um brief realista.

Este modulo exercita os criterios estruturais que a US-005 pediu: cobertura
dos seis tipos de requisito, das tres formas de resposta de entrevista, de ao
menos uma familia com `confidence: default` e outra com confianca declarada,
de ao menos uma restricao, e a coerencia com o MIDI de origem (path existe,
sha256 casa com o arquivo apontado).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools import contract as _contract  # noqa: F401  # registra brief.validate
from tools.brief_schema import (
    REQUISITO_TYPES,
    STYLE_FAMILIES,
    validate_brief,
)
from tools.registry import get as get_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "brief_exemplo.json"


def _load_brief() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_fixture_file_exists():
    assert FIXTURE_PATH.is_file(), (
        f"fixture do brief nao encontrada em {FIXTURE_PATH.relative_to(REPO_ROOT)}"
    )


def test_fixture_passes_schema_validation():
    brief = _load_brief()
    validate_brief(brief)


def test_fixture_passes_brief_validate_tool():
    """O contrato publico da tool tambem precisa aceitar o exemplo."""
    tool = get_tool("brief.validate")
    assert tool is not None, "brief.validate nao esta registrada"
    envelope, warnings = tool.func({"brief": _load_brief()})
    assert envelope == {"ok": True}
    assert warnings == []


def test_source_midi_points_to_existing_file_with_matching_sha256():
    brief = _load_brief()
    src = REPO_ROOT / brief["source_midi"]["path"]
    assert src.is_file(), f"source_midi.path nao aponta para arquivo: {src}"
    got = hashlib.sha256(src.read_bytes()).hexdigest()
    assert got == brief["source_midi"]["sha256"], (
        "source_midi.sha256 nao bate com o arquivo apontado — "
        "fixture ficou fora de sincronia com o MIDI ancora."
    )


def test_covers_all_requisito_types():
    """Um requisito de cada tipo do vocabulario fechado."""
    brief = _load_brief()
    tipos_no_brief = {r["tipo"] for r in brief["requisitos"]}
    faltando = set(REQUISITO_TYPES) - tipos_no_brief
    assert not faltando, (
        f"exemplo nao cobre todos os tipos de requisito. faltando: {faltando}"
    )


def test_covers_three_interview_response_shapes():
    """As tres formas de resposta previstas na SKILL.md aparecem no exemplo.

    - nome de musico: `style.drums.reference == "Steve Jordan"`.
    - banda ou produtor: `style.keys.reference == "Nigel Godrich"`.
    - corpus proprio: `style.bass.reference == "corpus proprio"` com sources
      apontando para caminhos de MIDI.
    """
    brief = _load_brief()
    style = brief["style"]
    assert style["drums"]["reference"] == "Steve Jordan"
    assert style["keys"]["reference"] == "Nigel Godrich"
    assert style["bass"]["reference"] == "corpus proprio"
    assert style["bass"]["sources"], (
        "corpus proprio precisa listar caminho(s) do corpus em sources"
    )


def test_has_family_with_default_confidence_and_family_with_declared_confidence():
    brief = _load_brief()
    style = brief["style"]
    confidences = {fam: style[fam]["confidence"] for fam in STYLE_FAMILIES}
    assert "default" in confidences.values(), (
        f"nenhuma familia com confidence 'default': {confidences}"
    )
    declared = {c for c in confidences.values() if c != "default"}
    assert declared, (
        f"nenhuma familia com confidence declarado (high/medium/low): {confidences}"
    )


def test_has_at_least_one_restricao():
    brief = _load_brief()
    assert brief["restricoes"], (
        "exemplo precisa exercitar o veto — ao menos uma entrada em restricoes"
    )


def test_default_family_has_declared_assumption():
    """Familia que caiu em default precisa ter a suposicao registrada."""
    brief = _load_brief()
    style = brief["style"]
    default_families = [
        fam for fam in STYLE_FAMILIES if style[fam]["confidence"] == "default"
    ]
    assumptions_text = " ".join(brief["assumptions"]).lower()
    for fam in default_families:
        # aceita o nome ingles (chave do schema) ou o rotulo comum em pt.
        pt_alias = {
            "bass": "baixo",
            "drums": "bateria",
            "guitar": "guitarra",
            "keys": "teclas",
        }[fam]
        assert fam in assumptions_text or pt_alias in assumptions_text, (
            f"familia {fam} em default sem suposicao declarada em assumptions"
        )
