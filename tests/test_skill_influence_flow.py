"""Fluxo reference-driven da skill `midi-brief` (issue #76), com pesquisa MOCKADA.

A pesquisa ao vivo e a unica parte nao-deterministica do produto e nao e
testavel (`docs/objetivo.md` §4). O que ESTE arquivo testa e todo o resto do
fluxo que a skill coordena depois dela: o que a pesquisa devolve vira
`InfluenceProfile`, o perfil e validado, `influence.compile` traduz achado em
tecnica canonica executavel, o que o motor nao executa permanece visivel em
`unmapped_findings`, veto do usuario derruba sugestao, e so o que o usuario
autorizou passa por `brief.validate`.

A pesquisa entra aqui como fixture de texto (`_MOCKED_RESEARCH`) — nunca rede:
a fixture `no_network` derruba qualquer tentativa de socket durante os testes.
"""

from __future__ import annotations

import socket
import urllib.request
from typing import Any

import pytest

from tools import contract as _contract  # noqa: F401  # popula o registry
from tools import influence as influence_mod
from tools.registry import ToolError
from tools.registry import get as get_tool
from tools.techniques import SUPPORTED_TECHNIQUES
from tools.techniques.index import build_index

# --- a pesquisa mockada ---------------------------------------------------
#
# Forma do que uma busca ao vivo devolveria para a skill: documento com url,
# titulo, data de recuperacao e trechos parafraseados de COMPORTAMENTO. Nunca
# conteudo musical, nunca numero de MIDI.

_MOCKED_RESEARCH: dict[str, list[dict[str, Any]]] = {
    "drums": [
        {
            "id": "src_drums_1",
            "url": "https://example.org/entrevista-baterista",
            "title": "Entrevista sobre pocket e dinamica",
            "retrieved_at": "2026-09-01",
            "behaviors": [
                {
                    "dimension": "articulation",
                    "semantic_value": (
                        "usa ghost notes como articulacao de dinamica na caixa"
                    ),
                    "intensity": "medium",
                    "confidence": "high",
                    "summary": "articula pressao com ghost notes em vez de acentuar",
                },
            ],
        },
    ],
    "bass": [
        {
            "id": "src_bass_1",
            "url": "https://example.org/aula-baixo",
            "title": "Aula sobre abafamento e feel",
            "retrieved_at": "2026-09-01",
            "behaviors": [
                {
                    "dimension": "articulation",
                    "semantic_value": "nao usa palm mute em nenhum trecho",
                    "intensity": "off",
                    "confidence": "medium",
                    "summary": "abafamento de palma ausente na referencia",
                },
            ],
        },
    ],
    "guitar": [
        {
            "id": "src_guitar_1",
            "url": "https://example.org/rig-rundown",
            "title": "Rig rundown",
            "retrieved_at": "2026-09-02",
            "behaviors": [
                {
                    "dimension": "execution_technique",
                    "semantic_value": "uso de alavanca com mergulho profundo",
                    "intensity": "strong",
                    "confidence": "high",
                    "summary": "alavanca como recurso expressivo principal",
                },
            ],
        },
    ],
}

_FAMILIES_RESEARCHED = ("drums", "bass", "guitar")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Nenhum teste deste arquivo pode tocar a rede."""

    def _boom(*args, **kwargs):  # pragma: no cover - so dispara se houver bug
        raise AssertionError("o fluxo tentou acessar a rede num teste mockado")

    monkeypatch.setattr(socket, "socket", _boom)
    monkeypatch.setattr(urllib.request, "urlopen", _boom)


# --- passo 4: a pesquisa vira InfluenceProfile ----------------------------


def _profile_from_research(
    families: tuple[str, ...] = _FAMILIES_RESEARCHED,
) -> dict[str, Any]:
    """Traduz o resultado da pesquisa mockada no perfil da musica.

    E o passo 4 do fluxo da skill: fonte vira `sources[]`, comportamento vira
    `findings[]` com `source_ids` apontando a fonte que o sustenta.
    """
    sources: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    for family in families:
        for doc in _MOCKED_RESEARCH[family]:
            sources.append(
                {
                    "id": doc["id"],
                    "url": doc["url"],
                    "title": doc["title"],
                    "retrieved_at": doc["retrieved_at"],
                }
            )
            for i, behavior in enumerate(doc["behaviors"]):
                findings.append(
                    {
                        "id": f"f_{family}_{i}",
                        "family": family,
                        "dimension": behavior["dimension"],
                        "semantic_value": behavior["semantic_value"],
                        "intensity": behavior["intensity"],
                        "confidence": behavior["confidence"],
                        "source_ids": [doc["id"]],
                        "user_stated": False,
                        "summary": behavior["summary"],
                    }
                )
    return {
        "version": influence_mod.INFLUENCE_SCHEMA_VERSION,
        "project_ref": "musica-de-teste",
        "sources": sources,
        "findings": findings,
        "unmapped_findings": [],
    }


def _compile(profile: dict[str, Any]) -> dict[str, Any]:
    data, _warnings = get_tool("influence.compile").func({"profile": profile})
    return data


def _validate_brief(brief: dict[str, Any]) -> dict[str, Any]:
    data, _warnings = get_tool("brief.validate").func({"brief": brief})
    return data


# --- passo 5: o perfil e validado antes de virar sugestao -----------------


def test_profile_from_mocked_research_is_valid():
    influence_mod.validate(_profile_from_research())


def test_finding_without_source_is_refused_unless_user_stated():
    profile = _profile_from_research(("drums",))
    profile["findings"][0]["source_ids"] = []
    with pytest.raises(influence_mod.InfluenceValidationError) as exc:
        influence_mod.validate(profile)
    assert exc.value.code == "E_INFLUENCE_FINDING_NO_SOURCE"

    profile["findings"][0]["user_stated"] = True
    influence_mod.validate(profile)


def test_profile_refuses_midi_numbers_invented_from_prose():
    """A skill nao pode transformar prosa em numero de MIDI. Se tentar gravar
    a sequencia no achado, o validador do perfil recusa antes de compilar."""
    profile = _profile_from_research(("drums",))
    profile["findings"][0]["semantic_value"] = "caixa em 38 40 42 na virada"
    with pytest.raises(influence_mod.InfluenceValidationError) as exc:
        influence_mod.validate(profile)
    assert exc.value.code == "E_INFLUENCE_MUSICAL_CONTENT"


# --- passo 6: compilacao deterministica -----------------------------------


def test_compile_only_suggests_techniques_the_engine_executes():
    """Nao e redundante com `_assert_rules_reference_supported_techniques()`,
    que roda no import de `tools/influence_compile.py`: aquele assert checa a
    TABELA de regras, este checa a SAIDA da compilacao — que cada sugestao
    emitida carrega o achado que a justifica e uma razao nao vazia, alem do
    nome executavel."""
    result = _compile(_profile_from_research())
    assert result["suggestions"], "pesquisa mockada deveria render sugestao"
    for suggestion in result["suggestions"]:
        assert suggestion["name"] in SUPPORTED_TECHNIQUES
        assert suggestion["finding_ids"], "sugestao sem achado que a justifique"
        assert suggestion["rationale"].strip()


def test_compile_is_deterministic_without_network():
    profile = _profile_from_research()
    assert _compile(profile) == _compile(profile)


def test_unsupported_finding_stays_visible_as_unmapped():
    """Achado pesquisado que o motor nao executa (alavanca de guitarra) nao
    vira sugestao, nao e descartado, e permanece visivel em
    `unmapped_findings` para a skill apresentar ao usuario."""
    result = _compile(_profile_from_research())
    unmapped_ids = {f["id"] for f in result["unmapped_findings"]}
    assert "f_guitar_0" in unmapped_ids
    suggested_families = {s["family"] for s in result["suggestions"]}
    assert "guitar" not in suggested_families
    unmapped = next(f for f in result["unmapped_findings"] if f["id"] == "f_guitar_0")
    assert unmapped["source_ids"] == ["src_guitar_1"]
    assert unmapped["confidence"] == "high"


def test_finding_with_intensity_off_becomes_not_recommended():
    """`intensity: off` e a referencia dizendo COM FONTE que nao usa aquilo —
    o oposto de lacuna. Nao vira sugestao e fica auditavel."""
    result = _compile(_profile_from_research())
    not_recommended = {n["finding_id"]: n for n in result["not_recommended"]}
    assert "f_bass_0" in not_recommended
    assert not_recommended["f_bass_0"]["technique"] == "bass.palm_mute"
    assert "bass.palm_mute" not in {s["name"] for s in result["suggestions"]}


# --- passo 7 e 8: apresentacao, veto e autorizacao ------------------------


def _brief(style: dict[str, Any], *, assumptions: list[str]) -> dict[str, Any]:
    return {
        "version": 1,
        "source_midi": {
            "path": "tests/fixtures/ancora_arranjo_atual.mid",
            "sha256": "e2727e269436ee09e0ced1e5b41345592ec3fec6d869938d4ac62ac0b41a35df",
            "tempo": 120.0,
            "key": None,
            "bars": 32,
        },
        "demanda": (
            "Arranjo influenciado por caracteristicas de performance das "
            "referencias citadas na entrevista."
        ),
        "route": "organica_inquietante",
        "sections_confirmed": True,
        "assumptions": assumptions,
        "requisitos": [],
        "style": style,
        "restricoes": [],
        "antirreferencias": [],
    }


def _style_from(
    result: dict[str, Any], authorized: dict[str, list[str]],
) -> dict[str, Any]:
    """Monta `style` como a skill monta no passo 9: sugestao registrada em
    `suggested_techniques`, autorizacao do usuario em `authorized_techniques`,
    e `techniques[]` como subconjunto do que ele autorizou."""
    style: dict[str, Any] = {}
    for suggestion in result["suggestions"]:
        family = suggestion["family"]
        entry = style.setdefault(
            family,
            {
                "reference": "referencia citada pelo usuario",
                "researched_at": "2026-09-01",
                "sources": [
                    doc["url"] for doc in _MOCKED_RESEARCH.get(family, [])
                ],
                "confidence": "high",
                "techniques": [],
                "authorized_techniques": [],
                "suggested_techniques": [],
            },
        )
        entry["suggested_techniques"].append(
            {"name": suggestion["name"], "rationale": suggestion["rationale"]}
        )
    for family, names in authorized.items():
        entry = style[family]
        entry["authorized_techniques"] = list(names)
        entry["techniques"] = [
            {"name": name, "rationale": "autorizada pelo usuario na entrevista"}
            for name in names
        ]
    return style


def _recommended_set(result: dict[str, Any]) -> dict[str, list[str]]:
    """O conjunto que a skill oferece para autorizacao em UMA acao: exatamente
    as sugestoes que `influence.compile` produziu, por familia."""
    recommended: dict[str, list[str]] = {}
    for suggestion in result["suggestions"]:
        recommended.setdefault(suggestion["family"], []).append(suggestion["name"])
    return recommended


def test_authorizing_the_recommended_set_passes_brief_validate():
    """"Autorizar o conjunto recomendado" grava a lista canonica que
    `influence.compile` produziu, e essa lista passa por `brief.validate` sem
    ajuste — o conjunto oferecido e executavel de ponta a ponta."""
    result = _compile(_profile_from_research())
    recommended = _recommended_set(result)
    assert recommended == {"drums": ["drums.ghost_notes"]}

    brief = _brief(_style_from(result, recommended), assumptions=[
        "Guitarra — alavanca levantada pela pesquisa mas nao executavel pelo "
        "motor; ficou como achado nao suportado.",
    ])
    assert _validate_brief(brief)["ok"] is True


@pytest.mark.parametrize("marker", ["todas", "all", "*"])
def test_a_marker_of_all_is_refused_instead_of_the_canonical_list(marker):
    """A skill proibe gravar um marcador de "todas" em vez da lista nome por
    nome. A barreira de maquina que sustenta a regra: `brief.validate` nao
    resolve marcador nenhum — ele nao e nome de tecnica e o brief e recusado."""
    result = _compile(_profile_from_research())
    style = _style_from(result, {"drums": [marker]})
    with pytest.raises(ToolError) as exc:
        _validate_brief(_brief(style, assumptions=[]))
    assert exc.value.code == "E_BRIEF_TECHNIQUE_NOT_FOUND"


def test_user_can_authorize_a_technique_the_research_did_not_suggest():
    """O usuario pode autorizar tecnica que a pesquisa NAO sugeriu, desde que o
    motor a execute. `brief.validate` aceita: a barreira e sobre autorizacao e
    sobre estar implementada, nunca sobre ter sido sugerida."""
    result = _compile(_profile_from_research())
    suggested = {s["name"] for s in result["suggestions"]}
    extra = "drums.flam"
    assert extra not in suggested
    assert extra in SUPPORTED_TECHNIQUES

    style = _style_from(result, {"drums": ["drums.ghost_notes", extra]})
    assert _validate_brief(_brief(style, assumptions=[]))["ok"] is True


def test_suggestion_recorded_without_authorization_is_accepted_but_inert():
    """Veto e silencio caem no MESMO estado de maquina: a sugestao fica
    registrada em `suggested_techniques` e `authorized_techniques` fica vazio.
    O que este teste exercita e a assimetria real de `brief.validate` — o mesmo
    nome que ele ACEITA em `suggested_techniques` (registro do que a pesquisa
    levantou) ele RECUSA em `techniques[]` sem autorizacao. Sugestao nao e
    autorizacao, e a barreira e de maquina, nao de prosa."""
    result = _compile(_profile_from_research())
    suggested_only = _style_from(result, {})
    assert "drums.ghost_notes" in [
        t["name"] for t in suggested_only["drums"]["suggested_techniques"]
    ]
    assert suggested_only["drums"]["authorized_techniques"] == []

    # Lado aceito: sugestao registrada sem autorizacao e brief valido.
    assert _validate_brief(_brief(suggested_only, assumptions=[
        "Bateria — drums.ghost_notes sugerida pela pesquisa mas nao autorizada "
        "(vetada ou nao confirmada pelo usuario).",
    ]))["ok"] is True

    # Lado recusado: a MESMA sugestao promovida a `techniques[]` sem passar por
    # `authorized_techniques`.
    promoted = _style_from(result, {})
    promoted["drums"]["techniques"] = [
        {"name": "drums.ghost_notes", "rationale": "aplicada sem autorizacao"}
    ]
    with pytest.raises(ToolError) as exc:
        _validate_brief(_brief(promoted, assumptions=[]))
    assert exc.value.code == "E_BRIEF_TECHNIQUE_NOT_AUTHORIZED"


def test_unmapped_finding_cannot_be_authorized_as_a_technique():
    """O achado de guitarra nao suportado nao pode virar autorizacao "na mao":
    a tecnica correspondente EXISTE no manual mas o motor nao a executa, e
    `brief.validate` recusa com `E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED`.

    Regressao (revisao do PR #121): a versao anterior deste teste autorizava
    `guitar.whammy_bar`, nome que nao existe em manual nenhum — batia sempre em
    `E_BRIEF_TECHNIQUE_NOT_FOUND` e o `assert ... in {dois codigos}` escondia
    isso. Removendo a barreira `SUPPORTED_TECHNIQUES` de `tools/brief_schema.py`
    o teste continuava PASSANDO: ele testava erro de digitacao, nao a barreira
    "documentada mas nao implementada". O nome usado agora e real e o codigo
    esperado e exato."""
    unimplemented = "guitar.dive_bomb"
    assert unimplemented in set(build_index().names()), (
        "o teste precisa de tecnica REAL do manual; nome inexistente bate em "
        "NOT_FOUND e nao exercita a barreira"
    )
    assert unimplemented not in SUPPORTED_TECHNIQUES

    result = _compile(_profile_from_research())
    style = _style_from(result, {})
    style["guitar"] = {
        "reference": "referencia citada pelo usuario",
        "researched_at": "2026-09-02",
        "sources": ["https://example.org/rig-rundown"],
        "confidence": "high",
        "techniques": [],
        "authorized_techniques": [unimplemented],
        "suggested_techniques": [],
    }
    with pytest.raises(ToolError) as exc:
        _validate_brief(_brief(style, assumptions=[]))
    assert exc.value.code == "E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED"


# --- sem acesso a web: as tres saidas -------------------------------------


def _no_web_research(family: str) -> list[dict[str, Any]]:
    """Simula a ausencia de ferramenta de busca: a pesquisa volta vazia."""
    return []


def test_no_web_access_falls_back_to_declared_default_without_inventing():
    """Saida 2 das tres oferecidas pela skill: persona default, com a
    ausencia DECLARADA em assumptions — nunca achado inventado de cabeca."""
    assert _no_web_research("drums") == []
    style = {
        "drums": {
            "reference": None,
            "researched_at": None,
            "sources": [],
            "confidence": "default",
            "techniques": [],
            "authorized_techniques": [],
            "suggested_techniques": [],
        },
    }
    brief = _brief(style, assumptions=[
        "Bateria — sem acesso a web nesta sessao; referencia nao pesquisada, "
        "assumida a persona default.",
    ])
    assert _validate_brief(brief)["ok"] is True


def test_no_web_access_with_user_supplied_source_is_user_stated():
    """Saida 1: o usuario fornece o material. Vira achado `user_stated`, sem
    fonte fabricada, e compila normalmente."""
    profile = {
        "version": influence_mod.INFLUENCE_SCHEMA_VERSION,
        "project_ref": "musica-de-teste",
        "sources": [],
        "findings": [
            {
                "id": "f_drums_user",
                "family": "drums",
                "dimension": "articulation",
                "semantic_value": "quero ghost notes discretas na caixa",
                "intensity": "subtle",
                "confidence": "default",
                "source_ids": [],
                "user_stated": True,
                "summary": "preferencia declarada pelo usuario",
            },
        ],
        "unmapped_findings": [],
    }
    influence_mod.validate(profile)
    result = _compile(profile)
    assert [s["name"] for s in result["suggestions"]] == ["drums.ghost_notes"]
