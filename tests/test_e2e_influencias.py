"""Prova ponta a ponta do produto: pesquisa MOCKADA, todas as camadas abaixo REAIS.

## O que este arquivo prova (issue #79)

O fluxo real de uma rodada de arranjo, sem web e sem modelo nenhum:

    analyze -> influence.validate -> influence.compile -> brief.validate
            -> plan.validate -> render -> validate
            (+ compliance.validate e report.build, que fecham a prova)

A UNICA camada mockada e a pesquisa: um `InfluenceProfile` fixo
(`INFLUENCE_PROFILE`), escrito a mao neste arquivo, faz o papel do que a IA
do usuario traria da web. Dali para baixo nada e simulado — o dicionario de
`influence.compile`, o brief, o plano, o motor de tecnicas, os validadores e
o relatorio de proveniencia sao os modulos de producao, chamados pelo MESMO
`tools.registry.call` que `python -m tools.cli tool <nome>` usaria.

### Divergencia de nomenclatura, declarada

A issue pede `influence.validate` como elo do fluxo. **Essa fachada nao
existe no registry** (`tools.registry.list_tools()` publica `influence.compile`,
mas nao `influence.validate`): a validacao do perfil e exposta como funcao de
modulo, `tools.influence.validate(profile)` — e assim que `docs/arquitetura.md`
(secao "Bloco `influence`") a documenta. Este arquivo chama a funcao de modulo
e o teste `test_influence_validate_nao_e_fachada_do_registry` fixa a
divergencia, para que ela seja uma decisao visivel e nao um esquecimento.
`influence.compile` tambem revalida o perfil por dentro, entao o elo de
validacao roda de qualquer forma dentro do fluxo de fachadas.

## Fixtures reais e por que cada uma

- `tests/fixtures/ancora_arranjo_atual.mid` — arranjo real, feito a mao, com
  `Bass`, `Drums`, guitarras, teclas e marcadores de secao de verdade. E a
  origem dos cenarios 1 (remodelar bateria e baixo que JA existem), 3
  (expressao de teclas), 4 (achado de guitarra sem mapeamento) e 5 (veto).
- `tests/fixtures/corpus_drums/ENTRE NOS.mid` — a bateria mais chapada do
  acervo (1037 notas, 100% em velocity 127, zero ghost, zero desvio de
  grade), citada em `docs/objetivo.md` §4 como a prova principal do motor:
  se sair intencao dali, o motor funciona. E a origem da prova de densidade
  por secao e do teto de compassos-com-ghost.

## Determinismo

Sem relogio (`DEFAULT_STYLE_RESEARCHED_AT`, nunca `datetime.now()`), sem
rede, sem `random` sem seed. Todo render usa `SEED`. Os dois renders do teste
de determinismo escrevem em caminhos diferentes DO MESMO workspace, para que
o relatorio (que cita `brief_path`) possa ser comparado byte a byte.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import mido
import pytest

import tools.contract  # noqa: F401 — popula o registry por efeito colateral
from tools import influence as influence_mod
from tools.brief_ref import brief_sha256
from tools.plan import DEFAULT_STYLE_RESEARCHED_AT
from tools.registry import call, list_tools

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ANCORA = FIXTURES / "ancora_arranjo_atual.mid"
CORPUS_DRUMS = FIXTURES / "corpus_drums"
ENTRE_NOS = CORPUS_DRUMS / "ENTRE NÓS.mid"

SEED = 20250904

# Corpus de referencia LEGITIMO: MIDIs de bateria da propria banda do usuario,
# versionados em `tests/fixtures/corpus_drums/`. Sao o material contra o qual a
# checagem comportamental de anti-copia (AC-16) compara janelas de N eventos.
CORPUS_REFERENCIA = [
    "DEIXE IR.mid", "FARDO.mid", "TEMPESTADE.mid",
]

# Tracks reais do ancora que este arquivo remodela/edita. Nomes exatos como
# o DAW exportou — `plan.edits[].track` casa por nome exato (AGENTS.md).
TRACK_DRUMS = "Drums"
TRACK_BASS = "Bass"
TRACK_KEYS = "Steinway Grand Piano"


# --- a pesquisa MOCKADA ----------------------------------------------------
#
# Este dicionario e o unico ponto simulado do arquivo. Ele tem a forma exata
# que `tools/influence.py` valida: fontes com id/url/titulo/data, e achados
# de COMPORTAMENTO (nunca conteudo musical) por familia e dimensao.

INFLUENCE_PROFILE: dict[str, Any] = {
    "version": 1,
    "project_ref": "e2e-influencias",
    "sources": [
        {
            "id": "s_manual_bateria",
            "url": "https://example.invalid/entrevista-baterista",
            "title": "Entrevista sobre articulacao de bateria",
            "retrieved_at": DEFAULT_STYLE_RESEARCHED_AT[:10],
        },
        {
            "id": "s_manual_baixo",
            "url": "https://example.invalid/oficina-de-baixo",
            "title": "Oficina sobre articulacao de baixo",
            "retrieved_at": DEFAULT_STYLE_RESEARCHED_AT[:10],
        },
        {
            "id": "s_manual_teclas",
            "url": "https://example.invalid/dinamica-de-teclado",
            "title": "Nota tecnica sobre dinamica continua em teclado",
            "retrieved_at": DEFAULT_STYLE_RESEARCHED_AT[:10],
        },
        {
            "id": "s_manual_guitarra",
            "url": "https://example.invalid/mao-direita-guitarra",
            "title": "Analise de mao direita de guitarra",
            "retrieved_at": DEFAULT_STYLE_RESEARCHED_AT[:10],
        },
    ],
    "findings": [
        {
            "id": "f_drums_ghost",
            "family": "drums",
            "dimension": "articulation",
            "semantic_value": "caixa com ghost notes de baixa pressao entre os backbeats",
            "intensity": "medium",
            "confidence": "high",
            "source_ids": ["s_manual_bateria"],
            "user_stated": False,
            "summary": "a referencia preenche o espaco entre backbeats com toque leve",
        },
        {
            "id": "f_bass_ghost",
            "family": "bass",
            "dimension": "articulation",
            "semantic_value": "baixo com notas fantasmas percussivas entre as notas de apoio",
            "intensity": "medium",
            "confidence": "medium",
            "source_ids": ["s_manual_baixo"],
            "user_stated": False,
            "summary": "articulacao percussiva de mao direita entre os apoios",
        },
        {
            "id": "f_bass_contour",
            "family": "bass",
            "dimension": "dynamics",
            "semantic_value": "baixo com crescendo na frase, dinamica em arco ate o apoio",
            "intensity": "medium",
            "confidence": "medium",
            "source_ids": ["s_manual_baixo"],
            "user_stated": False,
            "summary": "a frase cresce ate o apoio em vez de sair plana",
        },
        {
            "id": "f_keys_expression",
            "family": "keys",
            "dimension": "dynamics",
            "semantic_value": "teclado com expressao continua dentro da nota sustentada",
            "intensity": "strong",
            "confidence": "high",
            "source_ids": ["s_manual_teclas"],
            "user_stated": False,
            "summary": "a dinamica respira dentro da nota, nao so no ataque",
        },
        {
            "id": "f_guitar_rake",
            "family": "guitar",
            "dimension": "execution_technique",
            "semantic_value": "arrastada de palheta sobre as cordas abafadas antes do acorde",
            "intensity": "subtle",
            "confidence": "medium",
            "source_ids": ["s_manual_guitarra"],
            "user_stated": False,
            "summary": "gesto de mao direita que o motor ainda nao executa",
        },
        {
            "id": "f_drums_flam_off",
            "family": "drums",
            "dimension": "articulation",
            "semantic_value": "a referencia nao usa flam na caixa",
            "intensity": "off",
            "confidence": "high",
            "source_ids": ["s_manual_bateria"],
            "user_stated": False,
            "summary": "ausencia deliberada de ornamento de apoio",
        },
    ],
    "unmapped_findings": [],
}

# O veto do usuario (cenario 5). Vocabulario fechado de `STYLE_FAMILIES`,
# nunca prosa: a IA nao pode criar guitarra mesmo julgando que falta.
FAMILIA_VETADA = "guitar"


# --- helpers ---------------------------------------------------------------


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ok(name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Chama a fachada e exige `ok=true`, citando o erro quando falha."""
    env = call(name, payload)
    assert env["ok"], f"{name} falhou: {json.dumps(env['error'], ensure_ascii=False)}"
    return env


def _family_style(techniques: list[dict[str, Any]], reference: str) -> dict[str, Any]:
    return {
        "reference": reference,
        # Constante fixa, nunca o relogio (AGENTS.md — "Determinismo").
        "researched_at": DEFAULT_STYLE_RESEARCHED_AT,
        "sources": [f"mock://{s['id']}" for s in INFLUENCE_PROFILE["sources"]],
        "confidence": "medium",
        "techniques": techniques,
        "parameters": {},
    }


def _style_from_suggestions(
    suggestions: list[dict[str, Any]],
    authorized: dict[str, list[str]],
) -> dict[str, dict[str, Any]]:
    """Traduz a saida REAL de `influence.compile` em `plan.style`.

    So entra tecnica que o usuario autorizou no brief — e a barreira de
    autorizacao do AGENTS.md aplicada onde ela nasce, e nao um filtro
    decorativo: `test_apenas_tecnicas_autorizadas_entram_no_plano` prova
    que uma sugestao nao autorizada fica de fora.

    `intensity` da sugestao (traduzida de `off|subtle|medium|strong` pela
    tabela de `influence_compile`) vai para `StyleTechnique.intensity`, que
    `tools/render.py` usa como densidade quando `density` nao e declarada.
    """
    style: dict[str, dict[str, Any]] = {}
    for s in suggestions:
        family = s["family"]
        if s["name"] not in authorized.get(family, []):
            continue
        technique: dict[str, Any] = {
            "name": s["name"],
            "intensity": s["intensity"],
            "rationale": s["rationale"],
        }
        if s["style"] is not None:
            technique["style"] = s["style"]
        if s["parameters"]:
            technique["parameters"] = dict(s["parameters"])
        style.setdefault(
            family,
            _family_style([], f"perfil mockado de pesquisa ({family})"),
        )["techniques"].append(technique)
    return style


def _brief(
    plan: dict[str, Any],
    *,
    demanda: str,
    authorized: dict[str, list[str]],
    suggested: dict[str, list[str]],
    requisitos: list[dict[str, Any]],
    excluded_families: list[str],
) -> dict[str, Any]:
    style: dict[str, Any] = {}
    for family in sorted(set(authorized) | set(suggested)):
        style[family] = _family_style(
            [{"name": n} for n in authorized.get(family, [])],
            f"perfil mockado de pesquisa ({family})",
        )
        style[family]["authorized_techniques"] = list(authorized.get(family, []))
        style[family]["suggested_techniques"] = [
            {"name": n} for n in suggested.get(family, [])
        ]
    return {
        "version": 1,
        "source_midi": dict(plan["source_midi"]),
        "demanda": demanda,
        "route": plan["route"],
        "sections_confirmed": True,
        "assumptions": [],
        "requisitos": requisitos,
        "style": style,
        "restricoes": [],
        "antirreferencias": [],
        "excluded_families": excluded_families,
    }


def _write_brief(ws: Path, brief: dict[str, Any]) -> dict[str, str]:
    path = ws / "arrangement-brief.json"
    path.write_text(json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"path": str(path), "sha256": brief_sha256(path)}


def _tracks_by_name(mid: mido.MidiFile) -> dict[str, list[mido.MidiTrack]]:
    out: dict[str, list[mido.MidiTrack]] = {}
    for track in mid.tracks:
        name = next(
            (m.name for m in track if m.is_meta and m.type == "track_name"), None,
        )
        out.setdefault(name or "", []).append(track)
    return out


def _notes(track: mido.MidiTrack) -> list[tuple[int, int, int]]:
    """`(tick_absoluto, pitch, velocity)` de cada `note_on` que soa."""
    tick = 0
    out: list[tuple[int, int, int]] = []
    for msg in track:
        tick += msg.time
        if msg.type == "note_on" and msg.velocity > 0:
            out.append((tick, msg.note, msg.velocity))
    return out


def _faixa_velocity_do_manual(canonical: str) -> tuple[int, int]:
    """Faixa de `velocity` que o MANUAL declara para a tecnica.

    Lida do indice real (`knowledge/tecnicas/`), nunca hardcoded aqui: se o
    manual mudar o numero, o teste passa a cobrar o numero novo.
    """
    from tools.techniques.index import build_index

    technique = build_index().get(canonical)
    assert technique is not None, canonical
    for parameter in technique.parameters:
        if parameter.name == "velocity" and parameter.range is not None:
            low, high = parameter.range
            return int(low), int(high)
    raise AssertionError(
        f"manual de {canonical!r} nao declara range de velocity — o teste "
        f"nao pode inventar um",
    )


def _cc_count(track: mido.MidiTrack, control: int) -> int:
    return sum(
        1 for m in track
        if m.type == "control_change" and m.control == control
    )


# --- cenarios 1, 3, 4 e 5: remodelagem do arranjo real ---------------------


REQUISITOS_REMODELAGEM = [
    {
        "id": "R-DRUMS-GHOST",
        "familia": "drums",
        "tipo": "tecnica",
        "alvo": "ghost notes",
        "descricao": "a caixa ganha ghost notes de baixa pressao entre os backbeats",
    },
    {
        "id": "R-BASS-GHOST",
        "familia": "bass",
        "tipo": "tecnica",
        "alvo": "ghost notes",
        "descricao": "o baixo ganha notas fantasmas percussivas entre os apoios",
    },
]


@pytest.fixture(scope="module")
def remodelagem(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    """Roda o fluxo inteiro uma vez sobre `ancora_arranjo_atual.mid`.

    Escopo de modulo porque o fluxo e caro (render de 30 tracks) e todas as
    asseracoes olham para o MESMO artefato — o que tambem e a leitura
    honesta: um so render, muitas provas sobre ele.
    """
    ws = tmp_path_factory.mktemp("e2e-remodelagem")
    src = ws / "source.mid"
    shutil.copy2(ANCORA, src)
    origem_sha_antes = _sha256(ANCORA)
    copia_sha_antes = _sha256(src)

    analyze = _ok("analyze", {"midi_path": str(src)})["data"]

    # A pesquisa mockada passa pelo validador REAL do perfil.
    influence_mod.validate(INFLUENCE_PROFILE)

    compile_env = _ok("influence.compile", {
        "profile": INFLUENCE_PROFILE,
        "target_tools": {"drums": "generic", "bass": "generic", "keys": "generic"},
    })
    compiled = compile_env["data"]

    # O usuario autoriza bateria, baixo e teclas. Guitarra NAO e autorizada
    # (nem havia o que autorizar: o achado dela nao mapeia) e a familia
    # inteira e VETADA.
    autorizadas = {
        "drums": ["drums.ghost_notes"],
        "bass": ["bass.ghost_notes"],
        "keys": ["keys.expression"],
    }
    sugeridas: dict[str, list[str]] = {}
    for s in compiled["suggestions"]:
        nomes = sugeridas.setdefault(s["family"], [])
        if s["name"] not in nomes:
            nomes.append(s["name"])

    plan = _ok("plan.skeleton", {"midi_path": str(src), "seed": SEED})["data"]["plan"]
    plan["elements"] = []
    plan["transitions"] = []
    plan["edits"] = [
        {"track": TRACK_DRUMS, "profile": "drums", "intensity": 0.0},
        {"track": TRACK_BASS, "profile": "bass", "intensity": 0.0},
        {"track": TRACK_KEYS, "profile": "keys", "intensity": 0.0},
    ]
    plan["style"] = _style_from_suggestions(compiled["suggestions"], autorizadas)

    brief = _brief(
        plan,
        demanda="remodelar bateria e baixo existentes e dar respiro dinamico as teclas",
        authorized=autorizadas,
        suggested=sugeridas,
        requisitos=REQUISITOS_REMODELAGEM,
        excluded_families=[FAMILIA_VETADA],
    )
    _ok("brief.validate", {"brief": brief})
    plan["brief_ref"] = _write_brief(ws, brief)

    plan_validate = _ok(
        "plan.validate", {"plan": plan, "midi_path": str(src)},
    )["data"]

    out_a = ws / "arranjo-a.mid"
    out_b = ws / "arranjo-b.mid"
    corpus = [str(CORPUS_DRUMS / nome) for nome in CORPUS_REFERENCIA]
    render_a = _ok("render", {
        "midi_path": str(src), "plan": plan, "output_path": str(out_a),
        "reference_corpus": corpus,
    })
    render_b = _ok("render", {
        "midi_path": str(src), "plan": plan, "output_path": str(out_b),
        "reference_corpus": corpus,
    })

    validate = _ok("validate", {
        "midi_path": str(src), "rendered_path": str(out_a), "plan": plan,
    })["data"]

    compliance_env = call("compliance.validate", {
        "midi_path": str(src), "rendered_path": str(out_a),
        "plan": plan, "brief": brief,
    })

    report_payload = {
        "midi_path": str(src), "rendered_path": str(out_a),
        "plan": plan, "brief_path": plan["brief_ref"]["path"],
        "influence": INFLUENCE_PROFILE,
        "target_tools": {"drums": "generic", "bass": "generic", "keys": "generic"},
    }
    report_a = _ok("report.build", report_payload)["data"]["report"]
    report_b = _ok(
        "report.build", dict(report_payload, rendered_path=str(out_b)),
    )["data"]["report"]

    return {
        "ws": ws,
        "src": src,
        "plan": plan,
        "brief": brief,
        "analyze": analyze,
        "compiled": compiled,
        "compile_warnings": compile_env["warnings"],
        "plan_validate": plan_validate,
        "render_warnings": render_a["warnings"],
        "out_a": out_a,
        "out_b": out_b,
        "render_a": render_a["data"],
        "render_b": render_b["data"],
        "validate": validate,
        "compliance_env": compliance_env,
        "report": report_a,
        "report_b": report_b,
        "origem_sha_antes": origem_sha_antes,
        "copia_sha_antes": copia_sha_antes,
        "source_mid": mido.MidiFile(str(src)),
        "rendered_mid": mido.MidiFile(str(out_a)),
    }


# --- cenario 1: remodelar bateria e baixo que JA existem -------------------


def test_cenario_1_bateria_existente_ganha_ghost_notes_do_manual(
    remodelagem: dict[str, Any],
) -> None:
    """A track `Drums` do arranjo real sai com ghost notes NOVAS.

    Observavel: notas que nao existiam na origem, na faixa de velocity que o
    manual define para ghost de bateria, e nenhuma nota da origem perdida.
    """
    origem = _notes(_tracks_by_name(remodelagem["source_mid"])[TRACK_DRUMS][0])
    saida = _notes(_tracks_by_name(remodelagem["rendered_mid"])[TRACK_DRUMS][0])

    assert set(origem).issubset(set(saida)), (
        "nota da origem sumiu ou mudou: o nivel technique so acrescenta "
        "ornamento sobre nota estrutural"
    )
    novas = [n for n in saida if n not in set(origem)]
    assert novas, "nenhuma ghost note foi acrescentada na bateria"

    faixa = _faixa_velocity_do_manual("drums.ghost_notes")
    fora = [n for n in novas if not faixa[0] <= n[2] <= faixa[1]]
    assert not fora, (
        f"{len(fora)} nota(s) acrescentada(s) fora da faixa de ghost do "
        f"manual {faixa}: {fora[:5]}"
    )


def test_cenario_1_baixo_existente_ganha_ghost_notes_do_manual(
    remodelagem: dict[str, Any],
) -> None:
    origem = _notes(_tracks_by_name(remodelagem["source_mid"])[TRACK_BASS][0])
    saida = _notes(_tracks_by_name(remodelagem["rendered_mid"])[TRACK_BASS][0])

    assert set(origem).issubset(set(saida))
    novas = [n for n in saida if n not in set(origem)]
    assert novas, "nenhuma ghost note foi acrescentada no baixo"

    faixa = _faixa_velocity_do_manual("bass.ghost_notes")
    fora = [n for n in novas if not faixa[0] <= n[2] <= faixa[1]]
    assert not fora, (
        f"{len(fora)} nota(s) de baixo fora da faixa de ghost do manual {faixa}"
    )


# --- cenario 3: expressao de teclas ---------------------------------------


def test_cenario_3_teclas_recebem_expressao_cc11_e_nenhuma_nota_muda(
    remodelagem: dict[str, Any],
) -> None:
    """`keys.expression` escreve CC11 e NAO toca em nota nenhuma.

    A origem nao tem um unico CC11 nessas tracks; a saida tem. E o contrato
    do nivel `technique` para teclas (so acrescenta CC) e conferido aqui pelo
    lado observavel: a lista de notas sai identica.
    """
    origem_tracks = _tracks_by_name(remodelagem["source_mid"])[TRACK_KEYS]
    saida_tracks = _tracks_by_name(remodelagem["rendered_mid"])[TRACK_KEYS]
    assert len(origem_tracks) == len(saida_tracks) > 1, (
        "o fixture tem nome de track repetido de DAW; as tracks fisicas "
        "precisam ser tratadas como uma unidade"
    )

    cc_origem = sum(_cc_count(t, 11) for t in origem_tracks)
    cc_saida = sum(_cc_count(t, 11) for t in saida_tracks)
    assert cc_origem == 0, "a origem ja tinha CC11 — o teste perde o sentido"
    assert cc_saida > 0, "keys.expression nao escreveu nenhum CC11"

    for antes, depois in zip(origem_tracks, saida_tracks, strict=True):
        assert _notes(antes) == _notes(depois), (
            "keys.expression mudou nota estrutural — o nivel technique de "
            "teclas so pode acrescentar CC"
        )
    assert sum(_cc_count(t, 7) for t in saida_tracks) == 0, (
        "keys.expression nunca pode emitir CC7 (fader)"
    )


# --- cenario 4: achado de guitarra sem tecnica no motor -------------------


def test_cenario_4_achado_de_guitarra_degrada_sem_bloquear_as_outras_familias(
    remodelagem: dict[str, Any],
) -> None:
    """O achado de guitarra nao vira tecnica — e nao derruba o resto.

    `influence.compile` nao tem regra nenhuma de guitarra hoje, entao o
    achado sai em `unmapped_findings`. O criterio da issue e que ele apareca
    no relatorio SEM bloquear as outras familias.
    """
    compiled = remodelagem["compiled"]
    nao_mapeados = {f["id"] for f in compiled["unmapped_findings"]}
    assert "f_guitar_rake" in nao_mapeados, (
        "o achado de guitarra tinha que sair como nao mapeado"
    )
    assert not [s for s in compiled["suggestions"] if s["family"] == "guitar"], (
        "nenhuma sugestao de guitarra pode ser inventada"
    )

    relatorio = remodelagem["report"]
    ids_no_relatorio = {f["id"] for f in relatorio["unmapped_findings"]}
    assert "f_guitar_rake" in ids_no_relatorio, (
        "achado nao mapeado tem que aparecer no relatorio, nunca ser descartado"
    )

    # E as outras tres familias seguiram normalmente.
    aplicadas = set(relatorio["techniques"]["aplicadas"])
    assert aplicadas == {
        "bass.ghost_notes", "drums.ghost_notes", "keys.expression",
    }, aplicadas


def test_cenario_4_achado_off_sai_como_nao_recomendado_e_nunca_e_aplicado(
    remodelagem: dict[str, Any],
) -> None:
    """`intensity: off` e informacao, nao silencio: vira `not_recommended`."""
    compiled = remodelagem["compiled"]
    nao_recomendadas = {n["technique"] for n in compiled["not_recommended"]}
    assert nao_recomendadas == {"drums.flam"}, nao_recomendadas
    assert "drums.flam" not in {s["name"] for s in compiled["suggestions"]}

    relatorio = remodelagem["report"]
    assert relatorio["techniques"]["por_status"]["nao_recomendada"] == ["drums.flam"]
    # E o carimbo do MIDI final — a unica prova no arquivo — nao a cita.
    from tools.report import read_stamps

    carimbadas = {
        nome
        for stamp in read_stamps(remodelagem["rendered_mid"])
        for nome in stamp.techniques
    }
    assert carimbadas == {
        "bass.ghost_notes", "drums.ghost_notes", "keys.expression",
    }, carimbadas


# --- nenhuma tecnica nao implementada e oferecida ou aplicada -------------


def test_nenhuma_tecnica_fora_do_motor_e_sugerida_ou_aplicada(
    remodelagem: dict[str, Any],
) -> None:
    """Criterio de aceite: nada que o motor nao executa entra no fluxo."""
    from tools.techniques.engine import SUPPORTED_TECHNIQUES

    suportadas = set(SUPPORTED_TECHNIQUES)
    for s in remodelagem["compiled"]["suggestions"]:
        assert s["name"] in suportadas, s["name"]
    for familia, entry in remodelagem["brief"]["style"].items():
        for nome in entry["authorized_techniques"]:
            assert nome in suportadas, f"{familia}: {nome}"
    for familia, entry in remodelagem["plan"]["style"].items():
        for tech in entry["techniques"]:
            assert tech["name"] in suportadas, f"{familia}: {tech['name']}"
    assert remodelagem["report"]["techniques"]["nao_suportadas"] == []


def test_tecnica_documentada_mas_nao_implementada_e_recusada_na_validacao(
    remodelagem: dict[str, Any],
) -> None:
    """`keys.vibrato` existe no manual e NAO no motor: erro explicito.

    Prova o outro lado do criterio — nao basta o fluxo feliz nao oferecer:
    se alguem escrever a tecnica a mao no plano, `plan.validate` recusa.
    """
    from tools.techniques.engine import SUPPORTED_TECHNIQUES
    from tools.techniques.index import build_index

    assert "keys.vibrato" in build_index().names()
    assert "keys.vibrato" not in set(SUPPORTED_TECHNIQUES)

    plano = copy.deepcopy(remodelagem["plan"])
    plano["style"]["keys"]["techniques"].append(
        {"name": "keys.vibrato", "intensity": 0.5},
    )
    env = call("plan.validate", {
        "plan": plano, "midi_path": str(remodelagem["src"]),
    })
    mensagens = json.dumps(env, ensure_ascii=False)
    assert "keys.vibrato" in mensagens
    assert env["ok"] is False or env["data"]["valid"] is False


# --- cenario 5: veto explicito do usuario ---------------------------------


def test_cenario_5_veto_do_usuario_e_respeitado_no_fluxo_feliz(
    remodelagem: dict[str, Any],
) -> None:
    """Com `excluded_families: [guitar]`, nenhuma track de guitarra e criada."""
    assert remodelagem["brief"]["excluded_families"] == [FAMILIA_VETADA]
    assert remodelagem["render_a"]["elements"] == []

    origem = _tracks_by_name(remodelagem["source_mid"])
    saida = _tracks_by_name(remodelagem["rendered_mid"])
    assert set(saida) == set(origem), (
        "nenhuma track nova podia aparecer: o plano nao gera elemento e a "
        "guitarra esta vetada"
    )
    assert "guitar" not in remodelagem["plan"]["style"], (
        "familia vetada nao pode nem carregar tecnica declarada"
    )


def test_cenario_5_veto_recusa_criacao_de_guitarra_no_plano_e_no_render(
    remodelagem: dict[str, Any],
) -> None:
    """A barreira do veto existe nas DUAS camadas — plano e render.

    Mesmo plano, mesmo brief, so acrescentando um elemento de guitarra: a
    validacao do plano recusa E o render recusa (para plano construido em
    memoria que nunca passou por `plan.load`).
    """
    plano = copy.deepcopy(remodelagem["plan"])
    plano["elements"] = [{
        "id": "guitarra_proibida",
        "role": "rhythm_guitar",
        "sections": [remodelagem["plan"]["sections"][0]["label"]],
        "register": [40, 64],
        "layers": 1,
        "sync_role": "kick_support",
        "articulation": "tight",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        "instrument": {
            "plugin": "ES2",
            "preset": "Guitar - escolha o preset na sua biblioteca",
            "verified": False,
        },
        "rationale": "a IA julgou que falta guitarra, mas o usuario vetou a familia",
        "is_protagonist": False,
    }]

    env_plan = call("plan.validate", {
        "plan": plano, "midi_path": str(remodelagem["src"]),
    })
    texto_plan = json.dumps(env_plan, ensure_ascii=False)
    assert env_plan["ok"] is False or env_plan["data"]["valid"] is False
    assert "excluded_families" in texto_plan or FAMILIA_VETADA in texto_plan

    env_render = call("render", {
        "midi_path": str(remodelagem["src"]),
        "plan": plano,
        "output_path": str(remodelagem["ws"] / "nunca-deveria-existir.mid"),
    })
    assert env_render["ok"] is False
    assert not (remodelagem["ws"] / "nunca-deveria-existir.mid").exists()


# --- garantias mecanicas ---------------------------------------------------


def test_determinismo_midi_e_relatorio_byte_identicos(
    remodelagem: dict[str, Any],
) -> None:
    """Mesmo plano + mesma origem + mesma seed + mesmo perfil.

    Dois renders no MESMO workspace, para caminhos diferentes, e dois
    relatorios construidos sobre eles. O MIDI sai byte a byte igual e o
    relatorio serializado tambem — inclusive `hashes.rendered_sha256`.
    """
    assert remodelagem["out_a"].read_bytes() == remodelagem["out_b"].read_bytes()

    a = json.dumps(remodelagem["report"], sort_keys=True, ensure_ascii=False)
    b = json.dumps(remodelagem["report_b"], sort_keys=True, ensure_ascii=False)
    assert a == b
    assert (
        remodelagem["report"]["hashes"]["rendered_sha256"]
        == _sha256(remodelagem["out_a"])
    )


def test_origem_nunca_e_sobrescrita(remodelagem: dict[str, Any]) -> None:
    """Nem o fixture versionado nem a copia de trabalho mudam no render."""
    assert _sha256(ANCORA) == remodelagem["origem_sha_antes"]
    assert _sha256(remodelagem["src"]) == remodelagem["copia_sha_antes"]
    assert remodelagem["render_a"]["source_sha256"] == remodelagem["copia_sha_antes"]


def test_track_nao_declarada_sai_nota_a_nota_identica(
    remodelagem: dict[str, Any],
) -> None:
    """Toda track fora de `plan.edits` sai identica — e SEM carimbo."""
    from tools.report import parse_stamp

    declaradas = {e["track"] for e in remodelagem["plan"]["edits"]}
    origem = _tracks_by_name(remodelagem["source_mid"])
    saida = _tracks_by_name(remodelagem["rendered_mid"])

    intactas = sorted(set(origem) - declaradas)
    assert len(intactas) >= 10, (
        "o fixture precisa ter tracks nao declaradas para o teste valer"
    )
    for nome in intactas:
        antes = origem[nome]
        depois = saida[nome]
        assert len(antes) == len(depois), nome
        for t_antes, t_depois in zip(antes, depois, strict=True):
            assert _notes(t_antes) == _notes(t_depois), (
                f"track {nome!r} nao declarada em plan.edits mudou de notas"
            )
            textos = [
                m.text for m in t_depois
                if m.is_meta and m.type == "text"
            ]
            assert not [t for t in textos if parse_stamp(t)], (
                f"track {nome!r} nao declarada recebeu carimbo do arranjador"
            )


def test_apenas_tecnicas_autorizadas_entram_no_plano(
    remodelagem: dict[str, Any],
) -> None:
    """A barreira de autorizacao nao e decorativa.

    Duas provas: (a) o plano so carrega o que o brief autorizou; (b) mexer
    no brief depois de aprovado (o que muda o sha256) faz `plan.validate`
    recusar o plano inteiro.
    """
    autorizadas = {
        familia: set(entry["authorized_techniques"])
        for familia, entry in remodelagem["brief"]["style"].items()
    }
    for familia, entry in remodelagem["plan"]["style"].items():
        for tech in entry["techniques"]:
            assert tech["name"] in autorizadas[familia]

    plano = copy.deepcopy(remodelagem["plan"])
    plano["style"]["drums"]["techniques"].append(
        {"name": "drums.microtiming", "intensity": 0.5},
    )
    env = call("plan.validate", {
        "plan": plano, "midi_path": str(remodelagem["src"]),
    })
    texto = json.dumps(env, ensure_ascii=False)
    assert env["ok"] is False or env["data"]["valid"] is False
    assert "drums.microtiming" in texto and "authorized" in texto


def test_brief_editado_depois_de_aprovado_invalida_o_plano(
    remodelagem: dict[str, Any], tmp_path: Path,
) -> None:
    """`brief_ref.sha256` e a prova de QUAL autorizacao estava em vigor."""
    brief = copy.deepcopy(remodelagem["brief"])
    brief["style"]["drums"]["authorized_techniques"].append("drums.microtiming")
    caminho = tmp_path / "arrangement-brief.json"
    caminho.write_text(
        json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8",
    )

    plano = copy.deepcopy(remodelagem["plan"])
    plano["brief_ref"] = {
        "path": str(caminho),
        # O sha do brief ORIGINAL, nao o do arquivo editado.
        "sha256": remodelagem["plan"]["brief_ref"]["sha256"],
    }
    env = call("plan.validate", {
        "plan": plano, "midi_path": str(remodelagem["src"]),
    })
    assert env["ok"] is False or env["data"]["valid"] is False
    assert "sha256" in json.dumps(env, ensure_ascii=False)


# --- proveniencia: a cadeia inteira, elo por elo --------------------------


ELOS_DA_CADEIA = ("sources", "findings", "mapping", "plan_declaration", "targets")


def test_relatorio_liga_fonte_achado_mapeamento_tecnica_track_e_metrica(
    remodelagem: dict[str, Any],
) -> None:
    """Cada tecnica aplicada tem a cadeia COMPLETA no relatorio.

    `source -> finding -> mapping -> technique -> track/section -> metric`.
    Este teste falha se qualquer um desses elos vier vazio.
    """
    relatorio = remodelagem["report"]
    aplicadas = set(relatorio["techniques"]["aplicadas"])
    assert aplicadas, "nenhuma tecnica aplicada — o teste perde o sentido"

    vistos: set[str] = set()
    for elo in relatorio["chain"]:
        if elo["technique"] not in aplicadas:
            continue
        vistos.add(elo["technique"])
        for campo in ELOS_DA_CADEIA:
            assert elo[campo], f"{elo['technique']}: elo {campo!r} vazio"
        assert elo["authorized"] is True
        assert elo["supported"] is True
        assert elo["mapping"]["mapping_version"]
        for achado in elo["findings"]:
            assert achado["source_ids"], achado["id"]
        for alvo in elo["targets"]:
            assert alvo["metrics"], f"{elo['technique']}: alvo sem metrica"
            assert alvo["metrics"]["note_on_count"] > 0
            assert alvo["declarada_neste_plano"] is True
            assert alvo["carimbo_problemas"] == []
    assert vistos == aplicadas


def test_relatorio_nao_perde_nenhum_elo_das_tecnicas_aplicadas(
    remodelagem: dict[str, Any],
) -> None:
    """`missing_links` nao pode citar nenhuma tecnica aplicada."""
    aplicadas = set(remodelagem["report"]["techniques"]["aplicadas"])
    for entrada in remodelagem["report"]["missing_links"]:
        for nome in aplicadas:
            assert f"chain[{nome}]" != entrada["path"], entrada
            assert nome not in entrada["message"], entrada


def test_relatorio_declara_elo_ausente_quando_a_pesquisa_nao_e_entregue(
    remodelagem: dict[str, Any],
) -> None:
    """Controle positivo: o detector de elo ausente esta VIVO.

    Mesmo render, mesmo plano, mesmo brief — so sem o `InfluenceProfile`. Se
    o relatorio continuasse "completo", `missing_links` seria decorativo.
    """
    env = _ok("report.build", {
        "midi_path": str(remodelagem["src"]),
        "rendered_path": str(remodelagem["out_a"]),
        "plan": remodelagem["plan"],
        "brief_path": remodelagem["plan"]["brief_ref"]["path"],
    })
    relatorio = env["data"]["report"]
    codigos = {m["code"] for m in relatorio["missing_links"]}
    assert {"source", "finding"} <= codigos, relatorio["missing_links"]
    for elo in relatorio["chain"]:
        assert elo["sources"] == []
        assert elo["findings"] == []


def test_relatorio_nao_afirma_verificada_sem_validador_por_track(
    remodelagem: dict[str, Any],
) -> None:
    """Honestidade de status: sem cobertura por track, nada e "verificada".

    Neste plano so ha `plan.edits` — e `harmonia`, `placement` e
    `artificialidade` so percorrem track de elemento. O relatorio tem que
    dizer `aplicada_nao_verificavel`, nunca inventar sucesso.
    """
    relatorio = remodelagem["report"]
    validadores = relatorio["validators"]
    for nome in ("harmonia", "placement", "artificialidade"):
        assert validadores[nome]["tracks_cobertas"] == [], nome
    assert relatorio["techniques"]["aplicadas_verificadas"] == []
    assert set(relatorio["techniques"]["por_status"]["aplicada_nao_verificavel"]) == set(
        relatorio["techniques"]["aplicadas"],
    )
    assert "NAO verificaveis" in relatorio["summary_text"], (
        "o resumo para o musico tem que dizer que nao ha verificacao"
    )


# --- conformidade: cada requisito com evidencia numerica ------------------


def test_conformidade_prova_cada_requisito_com_evidencia_numerica(
    remodelagem: dict[str, Any],
) -> None:
    env = remodelagem["compliance_env"]
    assert env["ok"], json.dumps(env.get("error"), ensure_ascii=False)
    report = env["data"]
    assert report["conforme"] is True

    por_id = {v["id"]: v for v in report["requisitos"]}
    assert set(por_id) == {r["id"] for r in REQUISITOS_REMODELAGEM}
    for veredito in report["requisitos"]:
        assert veredito["status"] == "atendido", veredito
        evidencia = veredito["evidencia"]
        numeros = [
            v for v in evidencia.values()
            if isinstance(v, (int, float)) and not isinstance(v, bool)
        ]
        assert numeros, f"{veredito['id']}: evidencia sem numero: {evidencia}"

    drums = por_id["R-DRUMS-GHOST"]["evidencia"]
    assert drums["ocorrencias_inseridas"] > 0
    assert drums["notas_fora_da_faixa"] == 0
    assert drums["posicoes_proibidas_usadas"] == 0
    assert tuple(int(x) for x in drums["faixa_velocity"]) == _faixa_velocity_do_manual(
        "drums.ghost_notes",
    )

    baixo = por_id["R-BASS-GHOST"]["evidencia"]
    assert baixo["ocorrencias_inseridas"] > 0
    assert baixo["notas_fora_da_faixa"] == 0


def test_conformidade_bloqueia_quando_o_requisito_nao_e_atendido(
    remodelagem: dict[str, Any],
) -> None:
    """Controle positivo: o validador de conformidade nao e carimbo.

    Mesmo render, mesmo brief — so acrescentando um requisito de tecnica que
    o plano nao declarou. Tem que sair `ok=false` com evidencia.
    """
    brief = copy.deepcopy(remodelagem["brief"])
    brief["requisitos"].append({
        "id": "R-DRUMS-BUZZ",
        "familia": "drums",
        "tipo": "tecnica",
        "alvo": "buzz roll",
        "descricao": "a caixa faz buzz roll nas viradas",
    })
    env = call("compliance.validate", {
        "midi_path": str(remodelagem["src"]),
        "rendered_path": str(remodelagem["out_a"]),
        "plan": remodelagem["plan"],
        "brief": brief,
    })
    assert env["ok"] is False
    assert env["error"]["code"] == "E_COMPLIANCE_NOT_MET"
    vereditos = {
        v["id"]: v for v in env["error"]["context"]["report"]["requisitos"]
    }
    assert vereditos["R-DRUMS-BUZZ"]["status"] in ("nao_atendido", "parcial")


# --- nao copia -------------------------------------------------------------


def test_nao_copia_estrutural_passa_e_sequencia_de_notas_e_recusada(
    remodelagem: dict[str, Any],
) -> None:
    """AC-15: o `style` do plano carrega parametro, nunca conteudo musical."""
    assert remodelagem["plan_validate"]["valid"] is True

    plano = copy.deepcopy(remodelagem["plan"])
    plano["style"]["drums"]["parameters"] = {"riff_da_referencia": [60, 62, 64, 65]}
    env = call("plan.validate", {
        "plan": plano, "midi_path": str(remodelagem["src"]),
    })
    assert env["ok"] is False or env["data"]["valid"] is False


def test_comparacao_comportamental_roda_com_corpus_e_nao_acusa_copia(
    remodelagem: dict[str, Any],
) -> None:
    """AC-16: com corpus legitimo, a checagem roda e a saida passa limpa."""
    assert remodelagem["render_a"]["anticopy_issues"] == []


def test_anticopia_comportamental_acusa_quando_a_copia_existe(
    remodelagem: dict[str, Any],
) -> None:
    """Controle positivo: a checagem anti-copia nao e no-op.

    Se o proprio material de saida virar corpus de referencia, a comparacao
    TEM que acusar. Sem esta prova, o teste acima so mediria a ausencia de
    corpus util.
    """
    from tools import plan as plan_mod
    from tools.analyze import analyze
    from tools.contract import _rendered_tracks_from_midi
    from tools.validators.anticopy import (
        has_errors,
        load_reference_sequences,
        validate_anticopy,
    )

    plano = plan_mod.from_dict(remodelagem["plan"])
    tracks, _ = _rendered_tracks_from_midi(str(remodelagem["out_a"]), plano)
    corpus = load_reference_sequences([str(remodelagem["out_a"])])
    issues = validate_anticopy(
        tracks, plano, analyze(str(remodelagem["src"])), corpus=corpus,
    )
    assert has_errors(issues), (
        "a saida comparada contra ela mesma tinha que ser acusada de copia"
    )


# --- divergencia de nomenclatura declarada --------------------------------


def test_influence_validate_nao_e_fachada_do_registry() -> None:
    """A issue #79 pede `influence.validate` no fluxo de fachadas.

    Ela NAO existe: a validacao do perfil e funcao de modulo
    (`tools.influence.validate`), como `docs/arquitetura.md` documenta. Este
    teste existe para a divergencia ser uma decisao visivel — se um dia a
    fachada for criada, ele quebra e o fluxo deste arquivo passa a usa-la.
    """
    nomes = {t["name"] for t in list_tools()}
    assert "influence.compile" in nomes
    assert "influence.validate" not in nomes
    assert callable(influence_mod.validate)


def test_perfil_de_influencia_invalido_e_recusado_pelo_compile() -> None:
    """A validacao roda de verdade dentro do fluxo de fachadas."""
    perfil = copy.deepcopy(INFLUENCE_PROFILE)
    perfil["findings"][0]["dimension"] = "vibe"
    env = call("influence.compile", {"profile": perfil})
    assert env["ok"] is False
    assert "dimension" in json.dumps(env["error"], ensure_ascii=False)

    sem_fonte = copy.deepcopy(INFLUENCE_PROFILE)
    sem_fonte["findings"][0]["source_ids"] = []
    env = call("influence.compile", {"profile": sem_fonte})
    assert env["ok"] is False


# --- cenario 2: criar uma familia ausente ---------------------------------


TRACKS_DE_BAIXO_DO_ANCORA = ("Bass", "Bass Sub Mirror", "Pulse Wave Bass")

REQUISITOS_CRIACAO = [
    {
        "id": "R-BASS-CRIACAO",
        "familia": "bass",
        "tipo": "criacao",
        "alvo": "bass",
        "descricao": "criar a linha de baixo ausente dentro do campo harmonico",
    },
]


def _origem_sem_baixo(destino: Path) -> Path:
    """Deriva, do arranjo real, uma origem em que a familia baixo NAO existe.

    Nao e um MIDI sintetico: e o mesmo arranjo, com as tracks de baixo
    retiradas. O resto (guitarras, bateria, teclas, marcadores de secao)
    continua sendo material real — que e de onde o gerador tira campo
    harmonico e ancoras.
    """
    mid = mido.MidiFile(str(ANCORA))
    mid.tracks = [
        track for track in mid.tracks
        if next(
            (m.name for m in track if m.is_meta and m.type == "track_name"), "",
        ) not in TRACKS_DE_BAIXO_DO_ANCORA
    ]
    mid.save(str(destino))
    return destino


@pytest.fixture(scope="module")
def criacao(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    ws = tmp_path_factory.mktemp("e2e-criacao")
    src = _origem_sem_baixo(ws / "source-sem-baixo.mid")
    copia_sha_antes = _sha256(src)

    influence_mod.validate(INFLUENCE_PROFILE)
    compiled = _ok("influence.compile", {
        "profile": INFLUENCE_PROFILE, "target_tools": {"bass": "generic"},
    })["data"]

    # O usuario autoriza SO o contorno de dinamica. `bass.ghost_notes` fica
    # de fora de proposito: ela acrescenta dead note herdando o pitch da nota
    # estrutural anterior e, atravessando troca de acorde, o validador
    # harmonico a reprova — ver
    # `test_bug_bass_ghost_notes_gera_nota_fora_do_campo_harmonico`.
    autorizadas = {"bass": ["bass.velocity_contour"]}
    sugeridas = {"bass": ["bass.ghost_notes", "bass.velocity_contour"]}

    plan = _ok("plan.skeleton", {"midi_path": str(src), "seed": SEED})["data"]["plan"]
    secoes = [s["label"] for s in plan["sections"]]
    plan["transitions"] = []
    plan["edits"] = []
    plan["elements"] = [{
        "id": "baixo_criado",
        "role": "bass",
        "sections": secoes,
        "register": [33, 55],
        "layers": 1,
        "sync_role": "kick_support",
        "articulation": "tight",
        "harmony": "follow_chords",
        "pattern": None, "degrees": None, "dynamics": None,
        # Sem preset real varrido do disco, a sugestao cai para a CATEGORIA
        # do instrumento e vai marcada `verified: false` (AGENTS.md): nome
        # de preset inventado e chute apresentado como fato.
        "instrument": {
            "plugin": "ES2",
            "preset": "Bass - escolha o preset na sua biblioteca",
            "verified": False,
        },
        "rationale": (
            "a origem perdeu a familia baixo; sem fundamental o arranjo fica "
            "sem chao harmonico sob as guitarras"
        ),
        "is_protagonist": False,
    }]
    plan["style"] = _style_from_suggestions(compiled["suggestions"], autorizadas)

    brief = _brief(
        plan,
        demanda="criar a linha de baixo que falta",
        authorized=autorizadas,
        suggested=sugeridas,
        requisitos=REQUISITOS_CRIACAO,
        excluded_families=[FAMILIA_VETADA],
    )
    _ok("brief.validate", {"brief": brief})
    plan["brief_ref"] = _write_brief(ws, brief)
    _ok("plan.validate", {"plan": plan, "midi_path": str(src)})

    out = ws / "arranjo.mid"
    corpus = [str(CORPUS_DRUMS / nome) for nome in CORPUS_REFERENCIA]
    render = _ok("render", {
        "midi_path": str(src), "plan": plan, "output_path": str(out),
        "reference_corpus": corpus,
    })
    validate = _ok("validate", {
        "midi_path": str(src), "rendered_path": str(out), "plan": plan,
    })["data"]
    compliance = call("compliance.validate", {
        "midi_path": str(src), "rendered_path": str(out),
        "plan": plan, "brief": brief,
    })
    report = _ok("report.build", {
        "midi_path": str(src), "rendered_path": str(out),
        "plan": plan, "brief_path": plan["brief_ref"]["path"],
        "influence": INFLUENCE_PROFILE,
    })["data"]["report"]

    return {
        "ws": ws, "src": src, "plan": plan, "brief": brief,
        "render": render["data"], "render_warnings": render["warnings"],
        "validate": validate, "compliance": compliance, "report": report,
        "out": out, "copia_sha_antes": copia_sha_antes,
        "source_mid": mido.MidiFile(str(src)),
        "rendered_mid": mido.MidiFile(str(out)),
        "analyze": _ok("analyze", {"midi_path": str(src)})["data"],
    }


def test_cenario_2_familia_ausente_e_criada_com_notas_no_campo_harmonico(
    criacao: dict[str, Any],
) -> None:
    """A origem nao tem baixo; a saida tem — e as notas nao sao ruido.

    Observavel: (a) nenhuma track de baixo na origem; (b) uma track nova na
    saida, com notas; (c) `validate` nao acusa nenhuma issue harmonica nem
    de placement de severidade `error` para o elemento criado.
    """
    origem = _tracks_by_name(criacao["source_mid"])
    for nome in TRACKS_DE_BAIXO_DO_ANCORA:
        assert nome not in origem, nome

    novas = set(_tracks_by_name(criacao["rendered_mid"])) - set(origem)
    assert len(novas) == 1, novas
    nome_novo = novas.pop()
    assert nome_novo.startswith("baixo_criado - "), nome_novo

    notas = _notes(_tracks_by_name(criacao["rendered_mid"])[nome_novo][0])
    assert len(notas) > 20, f"a track de baixo criada saiu quase vazia: {len(notas)}"
    registro = criacao["plan"]["elements"][0]["register"]
    assert all(registro[0] <= n[1] <= registro[1] for n in notas), (
        "nota fora do registro declarado no elemento"
    )

    erros_placement = [
        i for i in criacao["validate"]["placement_issues"]
        if i.get("severity") == "error"
    ]
    assert not erros_placement, erros_placement[:3]

    # Campo harmonico: a esmagadora maioria das notas criadas pertence ao
    # acorde do compasso. As poucas que o validador reprova estao todas na
    # borda de compasso e sao o bug registrado em
    # `test_bug_harmonia_muda_de_veredito_entre_render_em_memoria_e_arquivo`.
    erros_harmonia = [
        i for i in criacao["validate"]["harmony_issues"]
        if i.get("severity") == "error"
    ]
    assert len(erros_harmonia) / len(notas) < 0.05, (
        f"{len(erros_harmonia)} de {len(notas)} notas fora do campo harmonico"
    )


def _veredito_criacao(criacao: dict[str, Any]) -> dict[str, Any]:
    env = criacao["compliance"]
    report = (
        env["data"] if env["ok"] else env["error"]["context"]["report"]
    )
    veredito, = report["requisitos"]
    assert veredito["id"] == "R-BASS-CRIACAO"
    return veredito


def test_cenario_2_conformidade_mede_a_criacao_com_evidencia_numerica(
    criacao: dict[str, Any],
) -> None:
    """O requisito de criacao sai medido, nao opinado.

    O veredito e conferido aqui pelos numeros que ele publica; o status
    `atendido` esta bloqueado pela divergencia harmonica registrada em
    `test_bug_render_e_validate_discordam_do_campo_harmonico`.
    """
    veredito = _veredito_criacao(criacao)
    evidencia = veredito["evidencia"]
    assert evidencia["elementos_gerados"] == ["baixo_criado"]
    assert evidencia["notas_criadas"] > 20
    assert evidencia["erros_placement"] == 0
    assert isinstance(evidencia["erros_harmonicos"], int)
    assert veredito["status"] in ("atendido", "parcial"), veredito


def test_cenario_2_track_criada_e_coberta_por_validador_por_track(
    criacao: dict[str, Any],
) -> None:
    """Aqui HA cobertura por track — e o status passa a vir de evidencia.

    E o contraponto de `test_relatorio_nao_afirma_verificada_sem_validador_por_track`:
    la o status era `aplicada_nao_verificavel` porque ninguem tinha olhado a
    track; aqui os tres validadores por track olharam, e o status reflete o
    que eles acharam.
    """
    relatorio = criacao["report"]
    nome_track, = {
        alvo["track_name"]
        for elo in relatorio["chain"]
        for alvo in elo["targets"]
    }
    for nome in ("harmonia", "placement", "artificialidade"):
        assert relatorio["validators"][nome]["tracks_cobertas"] == [nome_track], nome

    assert relatorio["techniques"]["por_status"]["aplicada_nao_verificavel"] == []
    status = {
        elo["technique"]: elo["status"] for elo in relatorio["chain"]
        if elo["technique"] in relatorio["techniques"]["aplicadas"]
    }
    assert status == {"bass.velocity_contour": "aplicada_com_erro"}, status
    # E o erro que rebaixou o status e o harmonico — nao um erro qualquer.
    assert relatorio["validators"]["harmonia"]["erros"] > 0


# --- a bateria real: densidade por secao, sem atulhar o arquivo -----------
#
# `ENTRE NOS.mid` e o pior MIDI do acervo do ponto de vista de humanizacao
# (`docs/objetivo.md` §4): 1037 notas, TODAS em velocity 127, zero ghost,
# zero desvio de grade. E exatamente por isso que ele e a prova: tudo o que
# aparecer na saida foi o motor que escreveu.
#
# O teto de compassos-com-ghost vem do defeito medido na issue #45 em
# `DEIXE IR`: 86% dos compassos com ghost, mediana 4 por compasso, maximo 9.
# A regressao dessa correcao vive em
# `tests/test_drums_ghost_notes_section_density.py`; aqui ela e cobrada de
# ponta a ponta, pelo fluxo de fachadas inteiro, num arquivo diferente.

TETO_COMPASSOS_COM_GHOST = 0.86

NOME_TRACK_BATERIA_ENTRE_NOS = "MIDI"


def _plano_bateria(
    ws: Path,
    src: Path,
    *,
    densidades: tuple[int, int],
    intensity: float | None,
    sufixo: str,
) -> dict[str, Any]:
    """Plano de bateria sobre `ENTRE NOS.mid`, parametrizado em dois eixos.

    `densidades` sao os eixos `densidade` das duas metades do arquivo;
    `intensity` e o que o plano declara em `style.drums.techniques[0]`. Com
    `intensity=None` a quantidade por compasso deriva SO da secao — que e o
    caminho que a issue #45 corrigiu.
    """
    compiled = _ok("influence.compile", {"profile": INFLUENCE_PROFILE})["data"]
    autorizadas = {"drums": ["drums.ghost_notes"]}

    plan = _ok("plan.skeleton", {"midi_path": str(src), "seed": SEED})["data"]["plan"]
    compassos = plan["source_midi"]["bars"]
    metade = compassos // 2
    plan["sections"] = [
        {
            "label": "A", "kind": "verse",
            "start_bar": 0, "end_bar": metade, "source": "marker",
            "protagonist": "drum_groove",
            "energy": {
                "densidade": densidades[0], "impacto": 5, "largura": 5,
                "altura": 5, "instabilidade": 5,
            },
        },
        {
            "label": "B", "kind": "verse",
            "start_bar": metade, "end_bar": compassos, "source": "marker",
            "protagonist": "drum_groove",
            "energy": {
                "densidade": densidades[1], "impacto": 5, "largura": 5,
                "altura": 5, "instabilidade": 5,
            },
        },
    ]
    plan["transitions"] = []
    plan["elements"] = []
    plan["edits"] = [{
        "track": NOME_TRACK_BATERIA_ENTRE_NOS, "profile": "drums", "intensity": 0.0,
    }]
    plan["style"] = _style_from_suggestions(compiled["suggestions"], autorizadas)
    tecnica = plan["style"]["drums"]["techniques"][0]
    if intensity is None:
        tecnica.pop("intensity")
    else:
        tecnica["intensity"] = intensity

    brief = _brief(
        plan,
        demanda="dar intencao a uma bateria travada em velocity 127",
        authorized=autorizadas,
        suggested={"drums": ["drums.ghost_notes"]},
        requisitos=[],
        excluded_families=[],
    )
    _ok("brief.validate", {"brief": brief})
    caminho = ws / f"arrangement-brief-{sufixo}.json"
    caminho.write_text(
        json.dumps(brief, indent=2, ensure_ascii=False), encoding="utf-8",
    )
    plan["brief_ref"] = {"path": str(caminho), "sha256": brief_sha256(caminho)}
    _ok("plan.validate", {"plan": plan, "midi_path": str(src)})
    return plan


def _ghosts_da_bateria(src: Path, out: Path) -> list[tuple[int, int, int]]:
    origem = set(_notes(
        _tracks_by_name(mido.MidiFile(str(src)))[NOME_TRACK_BATERIA_ENTRE_NOS][0],
    ))
    saida = _notes(
        _tracks_by_name(mido.MidiFile(str(out)))[NOME_TRACK_BATERIA_ENTRE_NOS][0],
    )
    return [n for n in saida if n not in origem]


@pytest.fixture(scope="module")
def bateria_real(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    ws = tmp_path_factory.mktemp("e2e-bateria")
    src = ws / "source.mid"
    shutil.copy2(ENTRE_NOS, src)
    influence_mod.validate(INFLUENCE_PROFILE)

    saidas: dict[str, Any] = {}
    for nome, densidades, intensity in (
        ("secao", (9, 1), None),
        ("secao_trocada", (1, 9), None),
        ("com_intensity", (9, 1), 0.55),
        ("com_intensity_trocada", (1, 9), 0.55),
    ):
        plan = _plano_bateria(
            ws, src, densidades=densidades, intensity=intensity, sufixo=nome,
        )
        out = ws / f"arranjo-{nome}.mid"
        render = _ok("render", {
            "midi_path": str(src), "plan": plan, "output_path": str(out),
        })
        saidas[nome] = {
            "plan": plan, "out": out, "render": render["data"],
            "warnings": render["warnings"],
            "ghosts": _ghosts_da_bateria(src, out),
        }

    source_mid = mido.MidiFile(str(src))
    plan_base = saidas["secao"]["plan"]
    return {
        "ws": ws, "src": src, "saidas": saidas,
        "compassos": plan_base["source_midi"]["bars"],
        "metade": plan_base["source_midi"]["bars"] // 2,
        "ticks_por_compasso": source_mid.ticks_per_beat * 4,
        "origem": sorted(set(_notes(
            _tracks_by_name(source_mid)[NOME_TRACK_BATERIA_ENTRE_NOS][0],
        ))),
        "saida": _notes(_tracks_by_name(
            mido.MidiFile(str(saidas["secao"]["out"])),
        )[NOME_TRACK_BATERIA_ENTRE_NOS][0]),
    }


def test_bateria_real_travada_em_127_recebe_intencao_do_motor(
    bateria_real: dict[str, Any],
) -> None:
    """A origem nao tem uma unica nota fora de 127 — a saida tem."""
    velocidades_origem = {v for _, _, v in bateria_real["origem"]}
    assert velocidades_origem == {127}, velocidades_origem

    ghosts = bateria_real["saidas"]["secao"]["ghosts"]
    assert ghosts, "o motor nao escreveu nada a partir da bateria chapada"
    faixa = _faixa_velocity_do_manual("drums.ghost_notes")
    assert all(faixa[0] <= v <= faixa[1] for _, _, v in ghosts)

    # Nenhuma nota da origem foi perdida nem rebaixada: `drums.ghost_notes`
    # e nivel `technique` e so acrescenta ornamento.
    assert set(bateria_real["origem"]).issubset(set(bateria_real["saida"]))


def _ghost_por_metade(
    bateria_real: dict[str, Any], nome: str,
) -> tuple[int, int]:
    fronteira = bateria_real["metade"] * bateria_real["ticks_por_compasso"]
    ghosts = bateria_real["saidas"][nome]["ghosts"]
    primeira = len([g for g in ghosts if g[0] < fronteira])
    return primeira, len(ghosts) - primeira


def test_bateria_real_densidade_de_ghost_acompanha_a_energia_da_secao(
    bateria_real: dict[str, Any],
) -> None:
    """Trocar a energia declarada troca o resultado — causalidade, nao correlacao.

    Dois renders do MESMO arquivo, mesma seed, mesmo material: so o eixo
    `densidade` de `plan.sections[].energy` troca de lugar entre as duas
    metades. Se o parametro comanda, a metade que ganha mais ghost tambem
    troca de lugar.
    """
    a_alta, b_baixa = _ghost_por_metade(bateria_real, "secao")
    a_baixa, b_alta = _ghost_por_metade(bateria_real, "secao_trocada")

    assert a_alta > a_baixa, (
        f"a primeira metade recebeu {a_alta} ghosts com densidade=9 e "
        f"{a_baixa} com densidade=1 — a energia declarada nao comandou"
    )
    assert b_alta > b_baixa, (
        f"a segunda metade recebeu {b_alta} ghosts com densidade=9 e "
        f"{b_baixa} com densidade=1"
    )
    assert a_alta > b_baixa and b_alta > a_baixa


def test_bateria_real_nao_repete_o_defeito_de_ghost_em_86_por_cento_dos_compassos(
    bateria_real: dict[str, Any],
) -> None:
    """O defeito da issue #45, cobrado pelo fluxo de fachadas inteiro.

    Medido nos dois renders derivados de secao: fracao de compassos COM
    bateria que receberam ghost, e o maximo num mesmo compasso. O defeito
    original era 86% dos compassos e ate 9 ghosts num compasso so.
    """
    tpc = bateria_real["ticks_por_compasso"]
    compassos_com_bateria = {tick // tpc for tick, _, _ in bateria_real["origem"]}
    assert compassos_com_bateria

    for nome in ("secao", "secao_trocada"):
        por_compasso: dict[int, int] = {}
        for tick, _, _ in bateria_real["saidas"][nome]["ghosts"]:
            compasso = tick // tpc
            por_compasso[compasso] = por_compasso.get(compasso, 0) + 1
        assert por_compasso, nome

        fracao = len(por_compasso) / len(compassos_com_bateria)
        assert fracao < TETO_COMPASSOS_COM_GHOST, (
            f"{nome}: {fracao:.0%} dos compassos com bateria receberam ghost "
            f"— o defeito da issue #45 media {TETO_COMPASSOS_COM_GHOST:.0%}"
        )
        assert max(por_compasso.values()) <= 3, (
            f"{nome}: {max(por_compasso.values())} ghosts num mesmo compasso"
        )


# --- achados: bugs do motor expostos por este fluxo ------------------------
#
# Os quatro testes abaixo estao marcados `xfail(strict=True)`: eles afirmam o
# comportamento CORRETO e falham hoje. Nao ha conserto de motor nesta rodada
# (issue #79 e de teste); cada um carrega o repro concreto e quebra o build no
# dia em que o defeito for corrigido, obrigando a remover o marcador.


@pytest.mark.xfail(
    strict=True,
    reason=(
        "drums.microtiming nao roda em take de bateria real com releases "
        "sobrepostos: o contrato humanize congela a ORDEM GLOBAL dos "
        "note_off (`_MidiContentSnapshot.note_pairs`), e deslocar o hi-hat "
        "alguns ms troca a ordem do release dele com o de outra peca. "
        "`ancora_arranjo_atual.mid` tem 16 re-ataques de 42/46 com a nota "
        "anterior ainda soando; DEIXE IR/ENTRE NOS/FARDO nao tem nenhum e "
        "por isso passam. O AGENTS.md exige que a nota seja par FECHADO, "
        "nao que o entrelacamento de releases entre alturas diferentes "
        "fique congelado."
    ),
)
def test_bug_microtiming_em_bateria_real_com_releases_sobrepostos() -> None:
    from tools.techniques import apply_technique

    fonte = mido.MidiFile(str(ANCORA))
    so_bateria = mido.MidiFile(ticks_per_beat=fonte.ticks_per_beat)
    so_bateria.tracks.append(
        _tracks_by_name(fonte)[TRACK_DRUMS][0],
    )
    apply_technique("drums.microtiming", so_bateria, seed=SEED)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "render e validate divergem sobre o MESMO arquivo e o MESMO plano: "
        "o `render` que gerou a linha de baixo declara zero erro harmonico, "
        "e o `validate` sobre o arquivo que ele acabou de escrever acusa "
        "sete. As notas reprovadas estao a poucos milissegundos da borda de "
        "compasso — o `render` julga com os segundos que o gerador calculou "
        "e o `validate` com os segundos que o arquivo devolve, e a atribuicao "
        "de compasso vira. Quem le o relatorio nao tem como saber qual dos "
        "dois vereditos vale."
    ),
)
def test_bug_harmonia_muda_de_veredito_entre_render_em_memoria_e_arquivo(
    criacao: dict[str, Any], tmp_path: Path,
) -> None:
    plano = copy.deepcopy(criacao["plan"])
    plano["style"] = {}
    plano.pop("brief_ref", None)
    saida = tmp_path / "sem-estilo.mid"
    render = _ok("render", {
        "midi_path": str(criacao["src"]), "plan": plano,
        "output_path": str(saida),
    })["data"]
    validate = _ok("validate", {
        "midi_path": str(criacao["src"]), "rendered_path": str(saida),
        "plan": plano,
    })["data"]

    def erros(issues: list[dict[str, Any]]) -> int:
        return len([i for i in issues if i["severity"] == "error"])

    assert erros(render["harmony_issues"]) == erros(validate["harmony_issues"]), (
        f"render diz {erros(render['harmony_issues'])} erro(s) harmonico(s) e "
        f"validate diz {erros(validate['harmony_issues'])} sobre o mesmo arquivo"
    )


# Regressao da issue #124: o anticopia do relatorio julgava as tracks que o
# arranjador NAO escreveu (`_rendered_tracks_from_midi` reconstroi cada track
# de origem como `source:<nome>`), acusando dezenove copias onde o `render`
# acusava zero — e rebaixando o status de TODA tecnica para
# `aplicada_com_erro`. Hoje a fachada entrega ao anticopia so as tracks de
# elemento, o mesmo conjunto que o `render` lhe entrega.
def test_anticopia_do_relatorio_nao_julga_track_copiada_da_origem(
    remodelagem: dict[str, Any],
) -> None:
    corpus = [str(CORPUS_DRUMS / nome) for nome in CORPUS_REFERENCIA]
    relatorio = _ok("report.build", {
        "midi_path": str(remodelagem["src"]),
        "rendered_path": str(remodelagem["out_a"]),
        "plan": remodelagem["plan"],
        "brief_path": remodelagem["plan"]["brief_ref"]["path"],
        "influence": INFLUENCE_PROFILE,
        "reference_corpus": corpus,
    })["data"]["report"]

    assert remodelagem["render_a"]["anticopy_issues"] == []
    acusadas = [
        issue for issue in relatorio["validators"]["anticopia"]["issues"]
        if any(str(e).startswith("source:") for e in issue["element_ids"])
    ]
    assert not acusadas, (
        f"{len(acusadas)} track(s) copiada(s) da origem acusada(s) de copia: "
        f"{sorted({i['track'] for i in acusadas})}"
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        "`StyleTechnique.intensity` sequestra o canal de `density` e desliga "
        "a densidade por secao da issue #45. `influence.compile` SEMPRE emite "
        "`intensity` (a traducao de off|subtle|medium|strong), entao todo "
        "plano montado pelo caminho real de pesquisa perde o eixo "
        "`plan.sections[].energy.densidade`: trocar 9 por 1 entre as duas "
        "metades do arquivo devolve MIDI byte-identico. Sem `intensity` o "
        "mesmo plano responde a troca (ver "
        "`test_bateria_real_densidade_de_ghost_acompanha_a_energia_da_secao`)."
    ),
)
def test_bug_intensity_do_compile_desliga_a_densidade_por_secao(
    bateria_real: dict[str, Any],
) -> None:
    a_alta, _ = _ghost_por_metade(bateria_real, "com_intensity")
    a_baixa, _ = _ghost_por_metade(bateria_real, "com_intensity_trocada")
    assert a_alta > a_baixa, (
        f"com intensity declarada, a primeira metade recebeu {a_alta} ghosts "
        f"com densidade=9 e {a_baixa} com densidade=1 — identico"
    )
