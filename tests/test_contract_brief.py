"""Testes do schema e da tool `brief.validate` (US-001).

Cobrimos:
- registro no registry global com descricao-prompt razoavel;
- brief valido (com todas as familias e todos os tipos de requisito) passa;
- brief com sequencia de notas em `style` (inteiros MIDI ou nomes de nota)
  falha com `E_BRIEF_MUSICAL_CONTENT` citando o path;
- tecnica declarada inexistente falha com `E_BRIEF_TECHNIQUE_NOT_FOUND`
  citando o path e sugerindo tecnica parecida;
- desvios estruturais (campo obrigatorio ausente, campo desconhecido,
  confidence fora do vocabulario, etc.) falham com `E_BRIEF_INVALID`.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from tools import contract as _contract  # noqa: F401  # registra as tools
from tools import techniques as techniques_mod
from tools.brief_schema import (
    BRIEF_SCHEMA_VERSION,
    _looks_like_note_sequence,
    brief_schema,
    validate_brief,
)
from tools.registry import ToolError, call, get


def _valid_brief() -> dict[str, Any]:
    return {
        "version": BRIEF_SCHEMA_VERSION,
        "source_midi": {
            "path": "songs/ancora.mid",
            "sha256": "a" * 64,
            "tempo": 92.0,
            "key": "Am",
            "bars": 128,
        },
        "demanda": (
            "Arranjo mais cinematografico da segunda parte, "
            "mantendo a mao esquerda igual."
        ),
        "route": "cinematica_emocional",
        "sections_confirmed": True,
        "assumptions": [
            "familia guitar sem referencia — usar persona default",
        ],
        "requisitos": [
            {
                "id": "R1", "familia": "drums", "tipo": "tecnica",
                "alvo": "verse", "descricao": "ghost notes no snare",
            },
            {
                "id": "R2", "familia": "drums", "tipo": "reducao",
                "alvo": "verse", "descricao": "menos viradas",
            },
            {
                "id": "R3", "familia": "bass", "tipo": "criacao",
                "alvo": "bridge", "descricao": "linha nova em contraponto",
            },
            {
                "id": "R4", "familia": "keys", "tipo": "estilo",
                "alvo": "chorus", "descricao": "colchao rhodes vintage",
            },
            {
                "id": "R5", "familia": "guitar", "tipo": "restricao",
                "alvo": "outro", "descricao": "sem distorcao",
            },
            {
                "id": "R6", "familia": "arranjo", "tipo": "intensidade",
                "alvo": "chorus", "descricao": "energia 8/10",
            },
        ],
        "style": {
            "drums": {
                "reference": "Jack DeJohnette",
                "researched_at": "2026-08-23",
                "sources": [
                    "https://exemplo.tld/artigo",
                    "Modern Drummer entrevista, mar/2018",
                ],
                "confidence": "high",
                "techniques": [
                    {"name": "ghost_notes", "density": 0.35,
                     "rationale": "verse pede caixa quase falada"},
                    {"name": "microtiming", "density": 0.5,
                     "rationale": None},
                ],
                "authorized_techniques": [
                    "drums.ghost_notes", "drums.microtiming",
                ],
                "suggested_techniques": [
                    {"name": "ghost_notes", "density": 0.35,
                     "rationale": "pesquisa levantou ghost notes"},
                    {"name": "microtiming", "density": 0.5,
                     "rationale": "pesquisa levantou microtiming"},
                ],
                "parameters": {"timing_bias_ms": -8.0},
            },
            "bass": {
                "reference": "Pino Palladino",
                "researched_at": "2026-08-23",
                "sources": ["https://exemplo.tld/pino"],
                "confidence": "medium",
                "techniques": [],
                "parameters": {"ghost_density": 0.2},
            },
            "keys": {
                "reference": None,
                "researched_at": None,
                "sources": [],
                "confidence": "default",
                "techniques": [],
                "parameters": {},
            },
            "guitar": {
                "reference": None,
                "researched_at": None,
                "sources": [],
                "confidence": "default",
                "techniques": [],
                "parameters": {},
            },
        },
        "restricoes": ["nao usar distorcao no outro"],
        "antirreferencias": ["evitar soar como cover de X"],
    }


# --- registry -------------------------------------------------------------


def test_brief_validate_registered_with_prompt_description():
    t = get("brief.validate")
    assert t is not None
    assert len(t.description) > 80
    assert "brief" in t.description.lower()


def test_brief_schema_has_all_required_top_level_fields():
    schema = brief_schema()
    required = set(schema["required"])
    assert required == {
        "version", "source_midi", "demanda", "route", "sections_confirmed",
        "assumptions", "requisitos", "style", "restricoes",
        "antirreferencias",
    }


# --- valid --------------------------------------------------------------


def test_valid_brief_passes():
    env = call("brief.validate", {"brief": _valid_brief()})
    assert env["ok"] is True, env
    assert env["data"] == {"ok": True}
    assert env["warnings"] == []


# --- musical content in style ------------------------------------------


def test_brief_with_note_name_list_in_style_fails_citing_path():
    brief = _valid_brief()
    # sources aceita strings; a varredura semantica pega o formato de nome
    # de nota (C4, D4, E4) mesmo passando pelo schema estrutural.
    brief["style"]["drums"]["sources"] = ["C4", "D4", "E4"]

    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False, env
    assert env["error"]["code"] == "E_BRIEF_MUSICAL_CONTENT"
    assert env["error"]["path"].startswith("style.drums.sources")


def test_brief_with_note_name_list_deep_inside_style_fails():
    # Mesmo se o schema fosse mais permissivo, a varredura pega em qualquer
    # profundidade — asseguramos passando lista de nomes de nota via um
    # brief que respeita a estrutura ate a folha.
    brief = _valid_brief()
    brief["style"]["bass"]["sources"] = ["C#3", "Eb3"]

    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_MUSICAL_CONTENT"
    assert "style.bass.sources" in env["error"]["path"]


# --- tecnica inexistente -----------------------------------------------


def test_brief_with_unknown_technique_fails_citing_path():
    brief = _valid_brief()
    brief["style"]["drums"]["techniques"].append(
        {"name": "inexistente_xyz", "density": None, "rationale": None},
    )
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False, env
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_FOUND"
    # tres tecnicas ja existentes precedem a que inserimos: indice 2
    assert env["error"]["path"] == "style.drums.techniques[2].name"


def test_brief_unknown_technique_hint_lists_similar():
    brief = _valid_brief()
    brief["style"]["drums"]["techniques"] = [
        {"name": "ghost_notess"},  # typo — deveria ser ghost_notes
    ]
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_FOUND"
    assert "ghost_notes" in env["error"]["hint"]


def test_brief_technique_by_canonical_name_is_accepted():
    brief = _valid_brief()
    brief["style"]["drums"]["techniques"] = [
        {"name": "drums.ghost_notes"},
    ]
    brief["style"]["drums"]["authorized_techniques"] = ["drums.ghost_notes"]
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is True, env


# --- desvios estruturais ----------------------------------------------


def test_brief_missing_required_field_fails_with_e_brief_invalid():
    brief = _valid_brief()
    del brief["restricoes"]
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert "restricoes" in env["error"]["message"]


def test_brief_unknown_field_at_root_is_rejected():
    brief = _valid_brief()
    brief["surpresa"] = 42
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "surpresa"


def test_brief_confidence_out_of_vocabulary_fails():
    brief = _valid_brief()
    brief["style"]["drums"]["confidence"] = "bastante"
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "style.drums.confidence"


def test_brief_unknown_style_family_is_rejected():
    brief = _valid_brief()
    brief["style"]["vocal"] = {
        "reference": None, "researched_at": None, "sources": [],
        "confidence": "default", "techniques": [],
    }
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "style.vocal"


def test_brief_style_parameters_reject_non_number_values():
    brief = _valid_brief()
    brief["style"]["drums"]["parameters"] = {"melody": [60, 62, 64]}
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "style.drums.parameters.melody"


def test_brief_requisito_type_out_of_vocabulary_fails():
    brief = _valid_brief()
    brief["requisitos"].append({
        "id": "R7", "familia": "drums", "tipo": "improviso",
        "alvo": "verse", "descricao": "vai que vai",
    })
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert "requisitos" in env["error"]["path"]


def test_brief_source_midi_sha256_pattern_enforced():
    brief = _valid_brief()
    brief["source_midi"]["sha256"] = "not-a-hash"
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "source_midi.sha256"


def test_brief_route_out_of_vocabulary_fails():
    brief = _valid_brief()
    brief["route"] = "salsa_com_baiao"
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_INVALID"
    assert env["error"]["path"] == "route"


# --- input schema (payload) ------------------------------------------


def test_brief_validate_input_missing_brief_key_returns_schema_error():
    env = call("brief.validate", {})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_INPUT_SCHEMA"


def test_brief_valid_brief_is_not_mutated_by_validation():
    brief = _valid_brief()
    snapshot = copy.deepcopy(brief)
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is True
    assert brief == snapshot


# --- ramos defensivos ------------------------------------------------


def test_looks_like_note_sequence_flags_midi_int_array():
    # Cobre o ramo de deteccao de inteiros na faixa MIDI (a defesa fica no
    # scanner mesmo que o schema atual nao aceite arrays de int em style —
    # e a rede de seguranca contra afrouxar o schema no futuro).
    reason = _looks_like_note_sequence([60, 62, 64])
    assert reason is not None
    assert "MIDI" in reason


def test_looks_like_note_sequence_ignores_single_element_and_out_of_range():
    assert _looks_like_note_sequence([60]) is None
    assert _looks_like_note_sequence([60, 200]) is None
    assert _looks_like_note_sequence([]) is None
    assert _looks_like_note_sequence("C4") is None


def test_validate_brief_maps_techniques_index_failure_to_e_techniques_index(
    monkeypatch,
):
    def _boom(*_a, **_k):
        raise techniques_mod.TechniqueError("manual sumiu")

    monkeypatch.setattr(
        "tools.brief_schema.techniques_mod.build_index", _boom,
    )
    with pytest.raises(ToolError) as exc:
        validate_brief(_valid_brief())
    assert exc.value.code == "E_TECHNIQUES_INDEX"
    assert "manual sumiu" in exc.value.message


# --- US-001: separacao sugestao vs autorizacao --------------------------------

# Uma tecnica real por familia, usada nos testes de autorizacao. Guitarra
# nao tem tecnica implementada no motor hoje (todas as documentadas continuam
# em pesquisa futura), entao os testes de autorizacao+techniques usam apenas
# familias com implementacao real; guitar entra somente onde o campo em
# exercicio (por exemplo, `suggested_techniques`) NAO exige implementacao.
_FAMILY_TECHNIQUE = {
    "drums": "drums.ghost_notes",
    "bass": "bass.ghost_notes",
    "guitar": "guitar.palm_mute",
    "keys": "keys.damper_pedal",
}

_IMPLEMENTED_FAMILIES = ("drums", "bass", "keys")


def _reset_family(brief: dict[str, Any], family: str) -> dict[str, Any]:
    """Limpa a familia — cada teste declara so o que quer exercitar."""
    brief["style"][family] = {
        "reference": None,
        "researched_at": None,
        "sources": [],
        "confidence": "default",
        "techniques": [],
        "authorized_techniques": [],
        "suggested_techniques": [],
        "parameters": {},
    }
    return brief


@pytest.mark.parametrize("family", ["drums", "bass", "guitar", "keys"])
def test_authorized_techniques_default_empty_with_empty_techniques_is_valid(family):
    # Regra 5: authorized_techniques ausente + techniques vazio = default seguro.
    brief = _reset_family(_valid_brief(), family)
    brief["style"][family].pop("authorized_techniques")
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is True, env


@pytest.mark.parametrize("family", ["drums", "bass", "guitar", "keys"])
def test_authorized_technique_names_validated_against_index(family):
    # Regra 1: nome em authorized_techniques tem que existir no indice.
    brief = _reset_family(_valid_brief(), family)
    brief["style"][family]["authorized_techniques"] = ["nao_existe_xyz"]
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False, env
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_FOUND"
    assert env["error"]["path"] == (
        f"style.{family}.authorized_techniques[0]"
    )


@pytest.mark.parametrize("family", ["drums", "bass", "guitar", "keys"])
def test_suggested_techniques_have_same_shape_and_name_validated(family):
    # Regra 2: suggested_techniques carrega name+parameters (mesma forma de
    # techniques[]) e cada nome e validado contra o indice.
    brief = _reset_family(_valid_brief(), family)
    brief["style"][family]["suggested_techniques"] = [
        {"name": "inexistente_qwe", "density": 0.5, "rationale": "pesquisa"},
    ]
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False, env
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_FOUND"
    assert env["error"]["path"] == (
        f"style.{family}.suggested_techniques[0].name"
    )


@pytest.mark.parametrize("family", ["drums", "bass", "guitar", "keys"])
def test_techniques_must_be_subset_of_authorized_techniques(family):
    # Regra 3: nome em techniques[] fora de authorized_techniques e erro
    # com path style.<familia>.techniques[<i>].name.
    tech = _FAMILY_TECHNIQUE[family]
    brief = _reset_family(_valid_brief(), family)
    brief["style"][family]["techniques"] = [
        {"name": tech, "density": 0.3, "rationale": "quero aplicar"},
    ]
    # authorized_techniques vazio — nada foi autorizado.
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False, env
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_AUTHORIZED"
    assert env["error"]["path"] == f"style.{family}.techniques[0].name"
    assert tech in env["error"]["message"]
    assert family in env["error"]["message"]


@pytest.mark.parametrize("family", ["drums", "bass", "guitar", "keys"])
def test_techniques_nonempty_with_absent_authorized_field_is_error(family):
    # Regra 4: authorized_techniques ausente + techniques nao vazio = erro.
    tech = _FAMILY_TECHNIQUE[family]
    brief = _reset_family(_valid_brief(), family)
    brief["style"][family]["techniques"] = [{"name": tech}]
    brief["style"][family].pop("authorized_techniques")
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False, env
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_AUTHORIZED"
    assert env["error"]["path"] == f"style.{family}.techniques[0].name"


@pytest.mark.parametrize("family", _IMPLEMENTED_FAMILIES)
def test_techniques_subset_of_authorized_passes(family):
    # Contrapartida positiva da regra 3: tecnica autorizada passa.
    # Familias sem tecnica implementada no motor (guitar hoje) nao entram
    # aqui: uma tecnica so pode ser autorizada se o motor souber aplicar,
    # e passar essa contrapartida com guitarra exigiria contradizer a regra
    # 2b (E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED).
    tech = _FAMILY_TECHNIQUE[family]
    brief = _reset_family(_valid_brief(), family)
    brief["style"][family]["techniques"] = [
        {"name": tech, "density": 0.3, "rationale": "autorizada"},
    ]
    brief["style"][family]["authorized_techniques"] = [tech]
    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is True, env


@pytest.mark.parametrize("family", ["drums", "bass", "guitar", "keys"])
def test_suggested_techniques_reject_musical_content_via_shared_helper(
    family, monkeypatch,
):
    # Regra 6: anticopia do style_schema vale para suggested_techniques.
    # A regra do schema recusa campos extras hoje, entao aqui garantimos que a
    # defesa semantica compartilhada dispara caso o schema seja afrouxado no
    # futuro — mesma logica do teste ja existente para `_looks_like_note_sequence`.
    from tools import brief_schema as bs

    monkeypatch.setattr(bs, "validate_input", lambda *_a, **_k: None)

    # Chave `notes` e proibida so pelo helper compartilhado (nao pelo scanner
    # local de note-sequence), entao a violacao aqui prova que o helper do
    # style_schema esta sendo aplicado a suggested_techniques.
    brief = _reset_family(_valid_brief(), family)
    brief["style"][family]["suggested_techniques"] = [
        {"name": _FAMILY_TECHNIQUE[family], "notes": "riff transcrito"},
    ]
    with pytest.raises(ToolError) as exc:
        bs.validate_brief(brief)
    assert exc.value.code == "E_BRIEF_MUSICAL_CONTENT"
    assert exc.value.path == f"style.{family}.suggested_techniques[0].notes"


def test_brief_recusa_tecnica_canonica_de_outra_familia():
    """Achado do review com o Codex no PR #52.

    `_resolve_family_technique` tentava `idx.get(name)` ANTES de filtrar por
    familia, entao canonico de outra familia passava: `drums.ghost_notes`
    declarado sob `style.bass` era aceito. O bloco de estilo de uma familia
    so declara tecnica dela mesma — senao a barreira do brief vira decorativa
    e a recusa fica dependendo de plan/render mais adiante.
    """
    brief = _valid_brief()
    brief["style"]["bass"]["authorized_techniques"] = ["drums.ghost_notes"]
    brief["style"]["bass"]["techniques"] = [{"name": "drums.ghost_notes"}]

    with pytest.raises(ToolError) as exc:
        validate_brief(brief)
    assert exc.value.code == "E_BRIEF_TECHNIQUE_WRONG_FAMILY"
    assert "bass" in str(exc.value)


def test_brief_aceita_nome_simples_resolvido_pela_familia_do_bloco():
    """Contraprova: nome simples continua resolvendo pela familia do path.

    `ghost_notes` sob `style.drums` nao e ambiguo — a familia esta no
    caminho. A correcao da familia errada nao pode quebrar isso.
    """
    brief = _valid_brief()
    brief["style"]["drums"]["authorized_techniques"] = ["ghost_notes"]
    brief["style"]["drums"]["techniques"] = [{"name": "ghost_notes"}]
    validate_brief(brief)  # nao levanta


# --- issue #74: brief nao pode autorizar tecnica sem aplicador ------------


def test_brief_recusa_authorized_technique_documentada_mas_sem_aplicador_bass_slide():
    """`bass.slide` esta no manual e fora de `SUPPORTED_TECHNIQUES`.

    Autorizar a tecnica no brief comprometeria uma execucao que o motor nao
    entrega. `brief.validate` tem que recusar aqui — nao no render, nao no
    `run` — para nao gastar iteracao com plano invalido.
    """
    brief = _reset_family(_valid_brief(), "bass")
    brief["style"]["bass"]["authorized_techniques"] = ["bass.slide"]

    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED"
    assert env["error"]["path"] == "style.bass.authorized_techniques[0]"
    # A mensagem lista as implementadas para orientar a correcao.
    assert "bass.ghost_notes" in env["error"]["message"]


def test_brief_recusa_authorized_technique_de_keys_nao_implementada():
    """`keys.melody_lead` e uma das dez tecnicas de teclas documentadas mas
    fora do motor (issue #14). Autorizacao aqui e no-op silencioso, o vicio
    ja rejeitado nesta base."""
    brief = _reset_family(_valid_brief(), "keys")
    brief["style"]["keys"]["authorized_techniques"] = ["keys.melody_lead"]

    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED"
    assert env["error"]["path"] == "style.keys.authorized_techniques[0]"
    assert "keys.damper_pedal" in env["error"]["message"]


def test_brief_recusa_authorized_technique_de_guitarra_sem_aplicador():
    """Guitarra nao tem tecnica implementada no motor hoje; o brief nao
    pode autorizar `guitar.palm_mute` (nem qualquer outra) enquanto isso
    for verdade. A mensagem tem que sinalizar essa ausencia."""
    brief = _reset_family(_valid_brief(), "guitar")
    brief["style"]["guitar"]["authorized_techniques"] = ["guitar.palm_mute"]

    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is False
    assert env["error"]["code"] == "E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED"
    assert env["error"]["path"] == "style.guitar.authorized_techniques[0]"
    assert "guitar" in env["error"]["message"]


def test_brief_aceita_suggested_technique_sem_aplicador():
    """Sugestao e o registro do que a pesquisa levantou, inclusive
    capacidade futura. `suggested_techniques` NAO passa pela barreira de
    `SUPPORTED_TECHNIQUES` — so autorizacao/selecao passa."""
    brief = _reset_family(_valid_brief(), "guitar")
    brief["style"]["guitar"]["suggested_techniques"] = [
        {"name": "guitar.palm_mute", "rationale": "referencia usa"},
    ]

    env = call("brief.validate", {"brief": brief})
    assert env["ok"] is True, env
