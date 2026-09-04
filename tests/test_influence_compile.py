"""Testes da tool `influence.compile` (issue #73).

Cobrimos os quatro casos que a issue pede explicitamente:
- timing laid-back -> tecnica de microtiming;
- ghost notes esparsas -> tecnica de ghost notes;
- ataque de baixo com palheta -> tecnica de ataque com style=palheta;
- achado sem tecnica compativel -> `unmapped_findings`, nunca descartado;

alem de:
- byte-identico para a mesma entrada;
- `mapping_version` presente e igual em toda sugestao;
- so emite tecnica presente em `SUPPORTED_TECHNIQUES`;
- `intensity: off` que bateria uma regra vira `not_recommended`, nao
  sugestao nem descarte silencioso;
- `unmapped_findings` do perfil de ORIGEM sao preservados na saida;
- ferramenta-alvo sem receita especifica cai para `generic` com warning
  `W_NO_TOOL_RECIPE` explicito;
- ferramenta-alvo COM receita especifica e honrada sem warning;
- entrada invalida (fora do InfluenceProfile v1) vira `ToolError`
  reaproveitando o codigo/`path` de `tools.influence.validate`.
"""

from __future__ import annotations

import copy
import json
from typing import Any

import tools.contract  # noqa: F401 -- registra as tools no import
from tools import registry
from tools.influence import from_dict as influence_from_dict
from tools.influence_compile import (
    INFLUENCE_MAPPING_VERSION,
    MAPPING_RULES,
    compile_influence,
)
from tools.techniques.engine import SUPPORTED_TECHNIQUES


def _profile(findings: list[dict[str, Any]], unmapped: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "version": 1,
        "project_ref": "musica-01",
        "sources": [
            {
                "id": "src_1",
                "url": "https://example.test/entrevista",
                "title": "Entrevista tecnica",
                "retrieved_at": "2026-08-24",
            },
        ],
        "findings": findings,
        "unmapped_findings": unmapped or [],
    }


def _finding(
    id_: str,
    family: str,
    dimension: str,
    semantic_value: str,
    *,
    intensity: str = "medium",
    confidence: str = "high",
    summary: str = "",
) -> dict[str, Any]:
    return {
        "id": id_,
        "family": family,
        "dimension": dimension,
        "semantic_value": semantic_value,
        "intensity": intensity,
        "confidence": confidence,
        "source_ids": ["src_1"],
        "user_stated": False,
        "summary": summary,
    }


def _call(payload: dict[str, Any]) -> dict[str, Any]:
    envelope = registry.call("influence.compile", payload)
    assert envelope["ok"] is True, envelope
    return envelope["data"]


# --- os quatro casos que a issue exige -------------------------------------


def test_timing_laid_back_maps_to_drums_microtiming():
    payload = {
        "profile": _profile([
            _finding(
                "f_timing", "drums", "timing_feel",
                "a batida fica levemente atras, feel laid back, empurra o groove",
                intensity="subtle",
            ),
        ]),
    }
    data = _call(payload)
    assert len(data["suggestions"]) == 1
    suggestion = data["suggestions"][0]
    assert suggestion["name"] == "drums.microtiming"
    assert suggestion["family"] == "drums"
    assert suggestion["finding_ids"] == ["f_timing"]
    assert suggestion["rationale"]
    assert 0.0 < suggestion["intensity"] <= 1.0
    assert data["unmapped_findings"] == []


def test_sparse_ghost_notes_maps_to_drums_ghost_notes():
    payload = {
        "profile": _profile([
            _finding(
                "f_ghost", "drums", "articulation",
                "usa ghost notes esparsas entre os backbeats da caixa",
                intensity="medium",
            ),
        ]),
    }
    data = _call(payload)
    assert len(data["suggestions"]) == 1
    assert data["suggestions"][0]["name"] == "drums.ghost_notes"
    assert data["suggestions"][0]["family"] == "drums"


def test_bass_pick_attack_maps_to_bass_attack_style_with_pick():
    payload = {
        "profile": _profile([
            _finding(
                "f_attack", "bass", "execution_technique",
                "ataque de baixo com palheta, som bem definido e cortante",
                intensity="strong",
            ),
        ]),
    }
    data = _call(payload)
    assert len(data["suggestions"]) == 1
    suggestion = data["suggestions"][0]
    assert suggestion["name"] == "bass.attack_style"
    assert suggestion["family"] == "bass"
    assert suggestion["style"] == "palheta"


def test_finding_without_compatible_technique_lands_in_unmapped_never_discarded():
    # Whammy bar / pitch bend profundo nao tem regra de mapeamento em
    # MAPPING_RULES (nenhuma entrada de guitarra existe la) — bom caso real
    # de "achado sem tecnica compativel", sem precisar inventar uma tecnica
    # fantasma so para o teste passar.
    assert not any(rule.family == "guitar" for rule in MAPPING_RULES)
    payload = {
        "profile": _profile([
            _finding(
                "f_whammy", "guitar", "execution_technique",
                "uso de whammy bar com pitch bend profundo nos finais de frase",
                intensity="strong",
            ),
        ]),
    }
    data = _call(payload)
    assert data["suggestions"] == []
    assert data["not_recommended"] == []
    assert len(data["unmapped_findings"]) == 1
    assert data["unmapped_findings"][0]["id"] == "f_whammy"


# --- reprodutibilidade -------------------------------------------------


def test_same_input_same_dictionary_version_is_byte_identical():
    payload = {
        "profile": _profile([
            _finding("f1", "drums", "timing_feel", "toca atras da batida, laid back"),
            _finding("f2", "bass", "execution_technique", "ataque com palheta"),
            _finding("f3", "guitar", "execution_technique", "whammy bar profundo"),
        ]),
    }
    out1 = json.dumps(_call(copy.deepcopy(payload)), sort_keys=True)
    out2 = json.dumps(_call(copy.deepcopy(payload)), sort_keys=True)
    assert out1 == out2


def test_mapping_version_present_on_result_and_every_suggestion():
    payload = {
        "profile": _profile([
            _finding("f1", "drums", "articulation", "ghost notes esparsas na caixa"),
        ]),
    }
    data = _call(payload)
    assert data["mapping_version"] == INFLUENCE_MAPPING_VERSION
    assert all(s["mapping_version"] == INFLUENCE_MAPPING_VERSION for s in data["suggestions"])


def test_only_supported_techniques_are_ever_emitted():
    supported = set(SUPPORTED_TECHNIQUES)
    for rule in MAPPING_RULES:
        assert rule.technique in supported, rule.id

    findings = [
        _finding("f_drums_timing", "drums", "timing_feel", "feel laid back, atrasado"),
        _finding("f_drums_ghost", "drums", "articulation", "ghost notes esparsas"),
        _finding("f_drums_flam", "drums", "articulation", "usa flam antes do golpe"),
        _finding("f_bass_pick", "bass", "execution_technique", "ataque com palheta"),
        _finding("f_bass_finger", "bass", "execution_technique", "toca com dedo"),
        _finding("f_keys_pedal", "keys", "articulation", "segura o pedal de sustain"),
    ]
    data = _call({"profile": _profile(findings)})
    assert len(data["suggestions"]) == len(findings)
    for suggestion in data["suggestions"]:
        assert suggestion["name"] in supported


# --- intensity=off vira not_recommended, nunca sugestao nem descarte ------


def test_off_intensity_finding_that_would_match_becomes_not_recommended():
    payload = {
        "profile": _profile([
            _finding(
                "f_no_ghost", "drums", "articulation",
                "a referencia NAO usa ghost notes, toca tudo direto",
                intensity="off",
            ),
        ]),
    }
    data = _call(payload)
    assert data["suggestions"] == []
    assert data["unmapped_findings"] == []
    assert len(data["not_recommended"]) == 1
    entry = data["not_recommended"][0]
    assert entry["finding_id"] == "f_no_ghost"
    assert entry["technique"] == "drums.ghost_notes"
    assert entry["reason"]


# --- unmapped_findings de origem sao preservados ---------------------------


def test_unmapped_findings_from_the_profile_are_passed_through():
    payload = {
        "profile": _profile(
            findings=[],
            unmapped=[
                _finding(
                    "u_1", "guitar", "execution_technique",
                    "whammy bar com pitch bend profundo",
                ),
            ],
        ),
    }
    data = _call(payload)
    assert [f["id"] for f in data["unmapped_findings"]] == ["u_1"]


# --- ferramenta-alvo ---------------------------------------------------


def test_target_tool_without_specific_recipe_falls_back_to_generic_with_warning():
    payload = {
        "profile": _profile([
            _finding("f_attack", "bass", "execution_technique", "ataque com palheta"),
        ]),
        "target_tools": {"bass": "ferramenta_inexistente"},
    }
    envelope = registry.call("influence.compile", payload)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["suggestions"][0]["tool"] == "generic"
    assert data["suggestions"][0]["requested_tool"] == "ferramenta_inexistente"
    codes = [w["code"] for w in envelope["warnings"]]
    assert "W_NO_TOOL_RECIPE" in codes


def test_target_tool_with_specific_recipe_is_honored_without_warning():
    payload = {
        "profile": _profile([
            _finding("f_attack", "bass", "execution_technique", "ataque com palheta"),
        ]),
        "target_tools": {"bass": "modo_bass"},
    }
    envelope = registry.call("influence.compile", payload)
    assert envelope["ok"] is True
    data = envelope["data"]
    assert data["suggestions"][0]["tool"] == "modo_bass"
    assert envelope["warnings"] == []


def test_target_tools_unknown_family_is_a_tool_error():
    payload = {
        "profile": _profile([
            _finding("f_attack", "bass", "execution_technique", "ataque com palheta"),
        ]),
        "target_tools": {"vocal": "algo"},
    }
    envelope = registry.call("influence.compile", payload)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "E_INFLUENCE_COMPILE_UNKNOWN_FAMILY"


# --- entrada invalida ----------------------------------------------------


def test_invalid_profile_reuses_influence_validation_error():
    payload = {"profile": _profile([_finding("f1", "vocal", "articulation", "algo")])}
    envelope = registry.call("influence.compile", payload)
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "E_INFLUENCE_UNKNOWN_FAMILY"


def test_finding_with_no_matching_keyword_lands_in_unmapped():
    payload = {
        "profile": _profile([
            _finding(
                "f_obscure", "keys", "section_behavior",
                "muda de textura completamente no refrao, sem ligacao com nenhum truque conhecido",
            ),
        ]),
    }
    data = _call(payload)
    assert data["suggestions"] == []
    assert [f["id"] for f in data["unmapped_findings"]] == ["f_obscure"]


# --- API Python direta (sem passar pelo registry) --------------------------


def test_compile_influence_python_api_matches_tool_output():
    profile = influence_from_dict(_profile([
        _finding("f1", "drums", "timing_feel", "toca atras da batida, laid back"),
    ]))
    result = compile_influence(profile)
    assert len(result.suggestions) == 1
    assert result.suggestions[0].name == "drums.microtiming"
    assert result.mapping_version == INFLUENCE_MAPPING_VERSION
