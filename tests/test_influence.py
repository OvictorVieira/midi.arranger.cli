"""Testes de `tools/influence.py` — InfluenceProfile v1.

Cobrimos:
- perfil valido (por familia, com fontes e user_stated);
- serializacao (`to_dict`/`from_dict`) preserva forma;
- vocabulario fechado (`family`, `dimension`, `intensity`, `confidence`);
- regra fonte-vs-preferencia (finding sem fonte E sem `user_stated` falha;
  finding com fonte E `user_stated=True` tambem falha por inconsistencia);
- barreira anticopia estrutural (chave `notes`, array de MIDI, array de
  nomes de nota, array de eventos em qualquer profundidade);
- barreira anticopia semantica (string `semantic_value` com sequencia de
  nomes de nota ou de inteiros MIDI);
- `source_ids` orfao (id que nao existe em sources[]);
- ids duplicados de source ou finding;
- `unmapped_findings` obedecem as mesmas regras dos findings.
"""

from __future__ import annotations

from typing import Any

import pytest

from tools.influence import (
    CONFIDENCE_LEVELS,
    INFLUENCE_DIMENSIONS,
    INFLUENCE_INTENSITIES,
    INFLUENCE_SCHEMA_VERSION,
    STYLE_FAMILIES,
    InfluenceValidationError,
    from_dict,
    to_dict,
    validate,
)


def _valid_profile_dict() -> dict[str, Any]:
    """Perfil valido de referencia. Cada teste que quiser um caso invalido
    parte de `copy.deepcopy(_valid_profile_dict())` e altera um campo."""
    return {
        "version": INFLUENCE_SCHEMA_VERSION,
        "project_ref": "musica-01",
        "sources": [
            {
                "id": "src_1",
                "url": "https://example.test/interview",
                "title": "Entrevista tecnica do baterista",
                "retrieved_at": "2026-08-24",
            },
            {
                "id": "src_2",
                "url": "https://example.test/masterclass",
                "title": "Masterclass do baixista",
                "retrieved_at": "2026-08-24",
            },
        ],
        "findings": [
            {
                "id": "f_drums_ghost",
                "family": "drums",
                "dimension": "articulation",
                "semantic_value": "usa ghost notes como articulacao de dinamica",
                "intensity": "medium",
                "confidence": "high",
                "source_ids": ["src_1"],
                "user_stated": False,
                "summary": "A referencia articula pressao com ghost notes em vez de acentuar",
            },
            {
                "id": "f_bass_feel",
                "family": "bass",
                "dimension": "timing_feel",
                "semantic_value": "atrasa levemente contra a bateria em versos",
                "intensity": "subtle",
                "confidence": "medium",
                "source_ids": ["src_2"],
                "user_stated": False,
                "summary": "Push-pull sutil de timing contra o backbeat",
            },
            {
                "id": "f_keys_preference",
                "family": "keys",
                "dimension": "arrangement_function",
                "semantic_value": "teclas ficam de pad, nao respondem melodia",
                "intensity": "strong",
                "confidence": "default",
                "source_ids": [],
                "user_stated": True,
                "summary": "Preferencia declarada pelo usuario",
            },
        ],
        "unmapped_findings": [
            {
                "id": "u_guitar_whammy",
                "family": "guitar",
                "dimension": "execution_technique",
                "semantic_value": "uso de whammy bar com pitch bend profundo",
                "intensity": "strong",
                "confidence": "high",
                "source_ids": ["src_1"],
                "user_stated": False,
                "summary": "Tecnica levantada mas ainda nao executada pelo motor",
            },
        ],
    }


# --- valido ---------------------------------------------------------------


def test_valid_profile_passes():
    validate(_valid_profile_dict())


def test_validate_accepts_dataclass_form():
    profile = from_dict(_valid_profile_dict())
    validate(profile)


def test_to_dict_from_dict_roundtrip_preserves_form():
    payload = _valid_profile_dict()
    profile = from_dict(payload)
    round_tripped = to_dict(profile)
    assert round_tripped == payload


def test_empty_profile_is_valid():
    validate({
        "version": INFLUENCE_SCHEMA_VERSION,
        "sources": [],
        "findings": [],
        "unmapped_findings": [],
    })


# --- vocabulario fechado --------------------------------------------------


def test_family_outside_vocab_fails():
    payload = _valid_profile_dict()
    payload["findings"][0]["family"] = "vocal"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_UNKNOWN_FAMILY"
    assert "findings[0].family" in exc.value.path


def test_dimension_outside_vocab_fails():
    payload = _valid_profile_dict()
    payload["findings"][0]["dimension"] = "vibes"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_UNKNOWN_DIMENSION"
    assert "findings[0].dimension" in exc.value.path


def test_intensity_outside_vocab_fails():
    payload = _valid_profile_dict()
    payload["findings"][0]["intensity"] = "extreme"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_UNKNOWN_INTENSITY"


def test_confidence_outside_vocab_fails():
    payload = _valid_profile_dict()
    payload["findings"][0]["confidence"] = "kinda"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_UNKNOWN_CONFIDENCE"


def test_dimensions_cover_the_eight_declared_by_the_issue():
    assert set(INFLUENCE_DIMENSIONS) == {
        "timing_feel",
        "dynamics",
        "articulation",
        "density",
        "arrangement_function",
        "register",
        "section_behavior",
        "execution_technique",
    }


def test_intensities_cover_the_four_declared_by_the_issue():
    assert INFLUENCE_INTENSITIES == ("off", "subtle", "medium", "strong")


def test_confidence_reuses_brief_vocabulary():
    # A regra e nao criar duas verdades — o perfil compartilha o
    # vocabulario do brief.
    assert CONFIDENCE_LEVELS == ("high", "medium", "low", "default")


# --- fonte vs preferencia -------------------------------------------------


def test_finding_without_source_and_without_user_stated_fails():
    payload = _valid_profile_dict()
    # Zera fontes do primeiro finding sem marcar user_stated.
    payload["findings"][0]["source_ids"] = []
    payload["findings"][0]["user_stated"] = False
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_FINDING_NO_SOURCE"


def test_finding_without_source_but_user_stated_passes():
    payload = _valid_profile_dict()
    # Vira preferencia explicita — passa.
    payload["findings"][0]["source_ids"] = []
    payload["findings"][0]["user_stated"] = True
    validate(payload)


def test_finding_with_source_and_user_stated_true_fails_as_contradiction():
    payload = _valid_profile_dict()
    payload["findings"][0]["user_stated"] = True  # ja tem source_ids
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_FINDING_SOURCE_AND_USER"


def test_source_id_unknown_fails():
    payload = _valid_profile_dict()
    payload["findings"][0]["source_ids"] = ["src_ghost"]
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_SOURCE_ID_UNKNOWN"


# --- ids ------------------------------------------------------------------


def test_duplicate_source_id_fails():
    payload = _valid_profile_dict()
    payload["sources"].append(dict(payload["sources"][0]))
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_DUP_SOURCE_ID"


def test_duplicate_finding_id_across_findings_and_unmapped_fails():
    payload = _valid_profile_dict()
    payload["unmapped_findings"][0]["id"] = payload["findings"][0]["id"]
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_DUP_FINDING_ID"


def test_source_date_wrong_format_fails():
    payload = _valid_profile_dict()
    payload["sources"][0]["retrieved_at"] = "24/08/2026"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_SOURCE_DATE_INVALID"


# --- anticopia estrutural -------------------------------------------------


def test_notes_key_rejected_at_any_depth():
    payload = _valid_profile_dict()
    # Enfia um campo `notes` numa dimensao ANINHADA — barreira estrutural
    # tem que ver mesmo em profundidade. Usa o `unmapped_findings` para
    # nao chocar com o rejeitador de campos desconhecidos ANTES da
    # varredura estrutural (a validate roda anticopia antes de from_dict
    # quando o payload e dict cru).
    payload["unmapped_findings"][0]["notes"] = [60, 64, 67]
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_midi_pitch_sequence_rejected():
    payload = _valid_profile_dict()
    payload["unmapped_findings"][0]["pattern"] = [60, 64, 67, 69]
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_event_array_with_pitch_and_time_rejected():
    payload = _valid_profile_dict()
    payload["unmapped_findings"][0]["events"] = [
        {"pitch": 60, "onset": 0},
        {"pitch": 64, "onset": 1},
    ]
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


# --- anticopia semantica (string) -----------------------------------------


def test_semantic_value_note_name_sequence_rejected():
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = "linha C4 D4 E4 repetindo"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_summary_with_midi_number_sequence_rejected():
    payload = _valid_profile_dict()
    payload["findings"][0]["summary"] = "toca 60 64 67 no compasso 3"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_prose_that_mentions_a_single_note_is_allowed():
    # A regra dispara so em SEQUENCIA (3+). Mencao isolada e prosa
    # legitima e nao vira anticopia.
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = "pedaliza a tonica em C durante o refrao"
    validate(payload)


# --- anticopia semantica: notas nao-contiguas (achado PR #101) ------------


def test_semantic_value_note_names_scattered_with_connectives_rejected():
    # Achado do review na PR #101: conectivo ("subindo pra", "depois")
    # entre as notas escapava da deteccao antiga, que so contava tokens
    # CONTIGUOS no split. Esta e a forma mais provavel de a IA do usuario
    # escrever `summary`/`semantic_value` em prosa natural.
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = (
        "groove com nota pedal em D4, subindo pra F4, depois A4"
    )
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_semantic_value_note_names_repeated_with_depois_rejected():
    # Segundo caso do achado: "depois" repetido entre cada nota.
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = (
        "toca C4 depois D4 depois E4 depois F4"
    )
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_semantic_value_bare_note_names_scattered_in_prose_rejected():
    # Nota SEM numero de oitava, espalhada em prosa. NOTE_NAME_RE (que
    # exige digito de oitava) nunca casaria "C", "D", "E", "F" soltos —
    # por isso `_BARE_NOTE_RE`, local a `tools/influence.py`, cobre esse
    # caso sem alterar `style_schema.NOTE_NAME_RE`.
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = "sobe de C pra D pra E pra F"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_summary_midi_numbers_scattered_with_connectives_rejected():
    # Mesma logica de contagem nao-contigua vale para inteiros MIDI.
    payload = _valid_profile_dict()
    payload["findings"][0]["summary"] = (
        "primeiro toca 60, depois sobe pra 64, e fecha em 67"
    )
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_prose_with_single_bare_note_mention_still_allowed():
    # Falso-positivo preservado: mencao ISOLADA (< 3 ocorrencias) de nota
    # sem oitava continua passando, mesmo em prosa com conectivo.
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = (
        "tonica em D4 sustentada por dois compassos antes do refrao"
    )
    validate(payload)


# --- anticopia semantica: notas coladas em pontuacao (achado Codex PR #101) -


def test_semantic_value_note_names_joined_by_slash_rejected():
    # Achado do review na PR #101: o split so cortava em espaco/virgula/
    # ponto-e-virgula, entao "C4/D4/E4" ficava um token so e nao casava
    # NOTE_NAME_RE nenhuma vez — a barreira nao disparava.
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = "toca C4/D4/E4 na virada"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_semantic_value_note_names_joined_by_hyphen_rejected():
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = "desce C4-D4-E4 no fill"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


def test_semantic_value_note_names_with_trailing_punctuation_rejected():
    # Ponto final apos a ultima nota colava o token ("E4.") e a ultima
    # ocorrencia deixava de contar.
    payload = _valid_profile_dict()
    payload["findings"][0]["semantic_value"] = (
        "toca C4 depois D4 depois E4."
    )
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


# --- user_stated tem que ser booleano de verdade (achado Codex PR #101) ---


def test_user_stated_string_false_is_rejected_not_coerced():
    # Achado do review: `bool("false")` e True em Python. Um achado SEM
    # fonte com `"user_stated": "false"` nao pode passar como se o usuario
    # tivesse mesmo declarado a preferencia — a proveniencia inverteria.
    payload = _valid_profile_dict()
    payload["findings"][0]["source_ids"] = []
    payload["findings"][0]["user_stated"] = "false"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_SHAPE"
    assert "user_stated" in exc.value.path


def test_user_stated_int_one_is_rejected_not_coerced():
    payload = _valid_profile_dict()
    payload["findings"][0]["source_ids"] = []
    payload["findings"][0]["user_stated"] = 1
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_SHAPE"


# --- forma das colecoes do perfil (achado Codex PR #101) ------------------


def test_findings_wrong_type_falsey_dict_is_rejected():
    # Achado do review: `payload.get("findings", []) or []` tratava
    # QUALQUER valor falsey (nao so ausencia) como lista vazia — um
    # `findings` acidentalmente serializado como `{}` descartava os achados
    # reais em silencio em vez de falhar como E_INFLUENCE_SHAPE.
    payload = _valid_profile_dict()
    payload["findings"] = {}
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_SHAPE"
    assert exc.value.path == "findings"


def test_sources_wrong_type_falsey_string_is_rejected():
    payload = _valid_profile_dict()
    payload["sources"] = ""
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_SHAPE"
    assert exc.value.path == "sources"


def test_unmapped_findings_wrong_type_falsey_zero_is_rejected():
    payload = _valid_profile_dict()
    payload["unmapped_findings"] = 0
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_SHAPE"
    assert exc.value.path == "unmapped_findings"


def test_findings_absent_key_still_defaults_to_empty_list():
    # Ausencia genuina (chave nem existe) continua sendo a UNICA forma
    # legitima de "sem achados" — nao pode virar erro.
    payload = _valid_profile_dict()
    del payload["findings"]
    validate(payload)


def test_findings_none_still_defaults_to_empty_list():
    payload = _valid_profile_dict()
    payload["findings"] = None
    validate(payload)


# --- unknown field --------------------------------------------------------


def test_unknown_root_field_fails():
    payload = _valid_profile_dict()
    payload["artist_persona"] = "Fulano"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    # Passa pela barreira estrutural antes; o campo desconhecido tambem
    # e coberto pela varredura, mas o payload nao carrega nada musical,
    # entao o motivo e "campos desconhecidos".
    assert exc.value.code == "E_INFLUENCE_UNKNOWN_FIELD"


def test_unknown_finding_field_fails():
    payload = _valid_profile_dict()
    payload["findings"][0]["extra"] = "bar"
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_UNKNOWN_FIELD"


# --- version --------------------------------------------------------------


def test_wrong_version_fails():
    payload = _valid_profile_dict()
    payload["version"] = 2
    with pytest.raises(InfluenceValidationError) as exc:
        validate(payload)
    assert exc.value.code == "E_INFLUENCE_VERSION"


# --- families que a issue exige exemplo ------------------------------------


def test_examples_cover_the_four_declared_families():
    payload = _valid_profile_dict()
    families_covered = {
        f["family"]
        for f in payload["findings"] + payload["unmapped_findings"]
    }
    # A issue pede exemplos para bateria, baixo, teclas e um nao mapeavel
    # de guitarra. A fixture usada nesses testes ja cobre os quatro.
    assert families_covered == set(STYLE_FAMILIES)
