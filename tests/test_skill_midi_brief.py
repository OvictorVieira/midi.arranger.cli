"""Testes da skill de brief (US-002).

Cobrimos:
- o arquivo `skills/midi-brief/SKILL.md` existe;
- o frontmatter e YAML valido no subset que a skill usa
  (name, description) e traz `name: midi-brief`;
- a descricao carrega gatilho em portugues e em ingles (a skill precisa
  disparar nos dois idiomas dentro do provider);
- todo caminho de arquivo referenciado no corpo do SKILL.md existe no
  repositorio (documentos ou modulos);
- toda tool citada por nome (via `python3 -m tools.cli tool <nome>`) esta
  registrada no registry global — o que impede o SKILL.md de mandar o agente
  chamar tool inexistente;
- as clausulas obrigatorias da entrevista estao presentes: 4 familias, no
  maximo 5 perguntas agrupadas, 3 formas de resposta, default declarado em
  `assumptions`, modo rapido nao trava;
- todo nome de tecnica citado no corpo existe de verdade, e o que a skill
  oferece para autorizacao esta em `SUPPORTED_TECHNIQUES` — nome inventado ou
  so documentado no manual faz `brief.validate` recusar o brief inteiro depois
  da entrevista toda;
- todo comando `date -u` citado produz valor que algum validador real aceita
  (`session.created_at` e `sources[].retrieved_at`).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools import brief_schema as _brief_schema
from tools import contract as _contract  # noqa: F401  # registra as tools
from tools.registry import get as get_tool

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = REPO_ROOT / "skills" / "midi-brief" / "SKILL.md"

# Artefatos que a skill (ou a fase `run`) PRODUZ no projeto do usuario. Eles
# sao citados no corpo da SKILL.md por nome de arquivo e, por definicao, nao
# existem neste repositorio.
_PRODUCED_ARTIFACTS = frozenset({
    "arrangement-brief.json",
    "influence-profile.json",
    "arrangement-plan.json",
    "arrangement-report.json",
})


# --- helpers --------------------------------------------------------------


def _split_frontmatter(text: str) -> tuple[str, str]:
    """Divide o SKILL.md em (frontmatter, body).

    Aceita `---` na primeira linha e `---` fechando em linha propria. Se o
    formato estiver quebrado, devolve `("", text)` — os testes explicitos
    de frontmatter capturam isso.
    """
    if not text.startswith("---\n"):
        return "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", text
    return text[4:end], text[end + 5 :]


def _parse_frontmatter(fm: str) -> dict[str, str]:
    """Parser minimo de YAML para o subset que a skill usa.

    Aceita `key: value` e `key: "value com dois pontos e virgula"`.
    Rejeita listas, objetos aninhados e continuacao de linha — se
    aparecerem, o autor precisa simplificar o frontmatter ou trocar por
    PyYAML (que hoje nao e dependencia deste projeto).
    """
    fields: dict[str, str] = {}
    for raw in fm.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"linha do frontmatter sem ':': {line!r}")
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if not key:
            raise ValueError(f"chave vazia no frontmatter: {line!r}")
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in ('"', "'")
        ):
            value = value[1:-1]
        if value.startswith(("[", "{", "|", ">")):
            raise ValueError(
                f"frontmatter usa construcao YAML nao suportada em {line!r}"
            )
        fields[key] = value
    return fields


# --- testes ---------------------------------------------------------------


def test_skill_file_exists():
    assert SKILL_PATH.is_file(), (
        f"skill nao encontrada em {SKILL_PATH.relative_to(REPO_ROOT)}"
    )


def test_frontmatter_is_valid_yaml_subset():
    text = SKILL_PATH.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(text)
    assert fm, "SKILL.md sem frontmatter YAML delimitado por ---"
    assert body.strip(), "SKILL.md sem corpo depois do frontmatter"
    fields = _parse_frontmatter(fm)
    assert fields.get("name") == "midi-brief", (
        f"frontmatter.name deve ser 'midi-brief', recebi {fields.get('name')!r}"
    )
    description = fields.get("description")
    assert description, "frontmatter.description ausente ou vazio"


def test_description_has_triggers_pt_and_en():
    text = SKILL_PATH.read_text(encoding="utf-8")
    fm, _ = _split_frontmatter(text)
    fields = _parse_frontmatter(fm)
    description = fields["description"]
    pt_triggers = ["arranja", "brief", "midi"]
    en_triggers = ["arrange", "brief", "midi"]
    missing_pt = [t for t in pt_triggers if t.lower() not in description.lower()]
    missing_en = [t for t in en_triggers if t.lower() not in description.lower()]
    assert not missing_pt, f"description sem gatilhos em portugues: {missing_pt}"
    assert not missing_en, f"description sem gatilhos em ingles: {missing_en}"


def test_referenced_files_exist():
    """Todo caminho tipo `docs/foo.md` citado na SKILL.md precisa existir."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    pattern = re.compile(r"`([A-Za-z0-9_./\-]+\.(?:md|json|py))`")
    seen: set[str] = set()
    missing: list[str] = []
    for match in pattern.finditer(body):
        candidate = match.group(1)
        if candidate in seen:
            continue
        seen.add(candidate)
        if candidate in _PRODUCED_ARTIFACTS:
            # produzido pela skill ou pela fase `run`; nao existe no repo.
            continue
        target = REPO_ROOT / candidate
        if not target.exists():
            missing.append(candidate)
    assert not missing, f"arquivos referenciados na SKILL.md nao existem: {missing}"


def test_referenced_tools_are_registered():
    """Toda `tools.cli tool <nome>` citada precisa estar registrada."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    pattern = re.compile(r"tools\.cli tool ([a-zA-Z][a-zA-Z0-9_.]*)")
    names = sorted(set(pattern.findall(body)))
    assert names, "SKILL.md nao cita nenhuma tool — o fluxo depende delas"
    missing = [n for n in names if get_tool(n) is None]
    assert not missing, f"tools citadas na SKILL.md nao registradas: {missing}"


@pytest.mark.parametrize(
    "clause",
    [
        # Fluxo obrigatorio.
        "analyze",
        "sections_confirmed",
        "brief.validate",
        # Entrevista por familia (as quatro).
        "bateria",
        "baixo",
        "teclas",
        "guitarra",
        # Regra de tamanho da entrevista.
        "cinco perguntas",
        # Tres formas de resposta.
        "nome de musico",
        "corpus proprio",
        # Default declarado quando falta resposta.
        "default da persona",
        "assumptions",
        # Modo rapido nao trava.
        "Modo rapido",
        "nao trava",
        # US-003: pesquisa e confianca.
        "pesquise ao vivo",
        "researched_at",
        "sources",
        "tecnica e comportamento",
        "nunca conteudo musical",
        "confianca declarada",
        "chute apresentado como fato",
        "mostre as fontes antes de gravar",
        "vocabulario de tecnica e fechado",
        "techniques.describe",
        "nao vira base de conhecimento",
        # issue #44 — configuracao de instrumento de corda.
        "instruments",
        "quantas cordas",
        "afinacao",
        "guitar.drop_tuning",
        "nao sei",
        "altura escrita",
        "altura soante",
        "instrumento transpositor",
        "finger",
        "declaracao do usuario vence",
        # PR #64 achado P2#2 — familia por patch REGENTE, nunca historico.
        "governing_programs",
        # PR #64 achado P2#3 — presenca de familia nao depende so da
        # classificacao automatica ter tido sucesso.
        "nao e o unico sinal de presenca",
        # PR #64 achado P2#5 — comando de lookup usa arquivo/stdin real,
        # nunca substituicao de processo.
        "--input -",
        # Issue #97 — pergunta 0 de escopo da sessao antes de perguntar
        # familias.
        "Escopo da sessao",
        "families_in_scope",
        "session.id",
        "session.intent",
        "session.created_at",
        # Vocabulario fechado de intent.
        "`edit`",
        "`create`",
        "`layer`",
        "`transition`",
        "`mixed`",
        # Pergunta 3 (e demais por-familia) filtra por escopo.
        "roda SO para as familias em `families_in_scope`",
        # Nota sobre retomada de sessao (fora do escopo).
        ".midiarranger/sessions/",
        # Achado do Codex na PR #99 — familia de corda sendo CRIADA (intent
        # create/mixed, ausente da origem) tambem precisa da pergunta de
        # afinacao, senao a linha nova nasce sem piso fisico declarado.
        "sendo GERADA do zero nesta sessao",
        # Achado do Codex na PR #105 (issue #17) — veto de familia inteira
        # ("nao quero guitarra gerada") tem que virar excluded_families
        # estruturado, senao fica so em restricoes livre e nunca bloqueia
        # a criacao de verdade.
        "excluded_families",
        "nao quero guitarra gerada",
        # Achado do Codex na PR #105 (segunda rodada) — modo rapido nao
        # pergunta a pergunta 5, mas nao pode descartar um veto de familia
        # que o usuario ja deu no proprio pedido inicial ("vai logo, mas
        # nao crie guitarra"); so cai pra `[]` quando o pedido nao tinha
        # veto nenhum.
        "nao descarta um veto",
        "nao crie guitarra",
        # Issue #76 — a skill como coordenadora do fluxo reference-driven.
        # Linguagem do produto (posicionamento legal, nao estetica).
        "influenciado por caracteristicas de performance",
        # Fluxo de dez passos, com o render fora desta fase.
        "influence.compile",
        "influence-profile.json",
        "InfluenceProfile",
        # A skill nao inventa numero a partir de prosa.
        "Nunca invente numero MIDI nem parametro tecnico a partir de prosa",
        # Fonte + confianca + resumo parafraseado antes de autorizar.
        "Apresentacao antes da autorizacao",
        "Resumo parafraseado",
        # So capacidade executavel e oferecida.
        "implemented_only",
        "implemented: false",
        # Achado nao suportado permanece visivel.
        "unmapped_findings",
        "Achado nao suportado permanece visivel",
        # Autorizacao do conjunto recomendado em uma acao, com lista canonica.
        "conjunto recomendado",
        "grave a lista canonica completa",
        # Sem acesso a web: tres saidas explicitas.
        "Quando nao ha acesso a web",
        "Fornecer as fontes manualmente",
        "Usar a persona default",
        "Cancelar aquela referencia",
        # Antirreferencia e veto tem precedencia sobre sugestao.
        "Antirreferencias e vetos mandam mais que sugestao",
        # Lacuna nao vira afirmacao sobre a referencia.
        "Lacuna nao e decisao",
        # A skill nao renderiza antes da autorizacao.
        "nao renderiza",
    ],
)
def test_body_carries_required_clauses(clause):
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    assert clause.lower() in body.lower(), (
        f"clausula ausente no corpo da SKILL.md: {clause!r}"
    )


def test_body_does_not_use_process_substitution_for_tool_input():
    """Regressao do achado P2#5 do PR #64: `tools.cli.py::_read_payload`
    so aceita `-` (stdin) ou arquivo regular para `--input` — substituicao
    de processo (`<(echo ...)`) cria um FIFO cujo `Path.is_file()` e
    False, e a chamada retorna `E_INPUT_FILE`. Nenhum exemplo de comando
    no corpo da skill pode usar essa sintaxe."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    matches = re.findall(r"--input\s+<\(", body)
    assert not matches, (
        "SKILL.md usa substituicao de processo (`--input <(...)`) em "
        "exemplo de comando — falha com E_INPUT_FILE em Bash real"
    )


def test_body_does_not_leak_musical_content_example():
    """A skill nunca deve dar exemplo de conteudo musical dentro de style."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    # Um bloco `parameters: {timing_bias_ms: -8}` esta ok. O que nao esta ok
    # e um exemplo com sequencia de notas MIDI (>=2 inteiros na faixa 0..127)
    # ou uma lista de nomes de nota (C4, F#3, Bb-1). A regra e a mesma do
    # `_looks_like_note_sequence` em `tools/brief_schema.py`.
    note_names = re.findall(r"\b[A-G][#b]?-?\d\b", body)
    # A entrevista cita `C4` e `F#3` UMA VEZ como exemplo do que e proibido.
    # Toleramos ate 3 mencoes; acima disso, e conteudo musical de fato.
    assert len(note_names) <= 3, (
        f"SKILL.md tem {len(note_names)} nomes de nota — parece conteudo musical: "
        f"{note_names[:6]}"
    )


def test_session_intent_vocabulary_matches_brief_schema():
    """Issue #97/#99 (esta skill) foi escrita em paralelo com a issue
    #96/#98, que define o schema real de `session` em
    `tools/brief_schema.py`. Sem esta checagem, o vocabulario que a
    entrevista ensina o agente a usar podia divergir silenciosamente do
    vocabulario que `brief_schema.py` de fato aceita — cada lado citando
    os mesmos cinco nomes por coincidencia, nao por contrato."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    intent_re = re.compile(r"`(edit|create|layer|transition|mixed)`")
    cited_intents = set(intent_re.findall(body))
    assert cited_intents == set(_brief_schema.SESSION_INTENTS), (
        f"intent citado na SKILL.md ({sorted(cited_intents)}) diverge do "
        "vocabulario real em brief_schema.SESSION_INTENTS "
        f"({sorted(_brief_schema.SESSION_INTENTS)})"
    )


def test_session_families_vocabulary_matches_brief_schema():
    """Mesma garantia da checagem acima, para `families_in_scope`: os
    nomes de familia citados entre crases na pergunta 0(b) tem que ser
    exatamente `brief_schema.STYLE_FAMILIES` — nem a mais (familia que o
    schema recusa), nem a menos (familia que o schema aceita e a skill
    nunca oferece)."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    families_re = re.compile(r"`(bass|drums|guitar|keys)`")
    cited_families = set(families_re.findall(body))
    assert cited_families == set(_brief_schema.STYLE_FAMILIES), (
        f"familias citadas na SKILL.md ({sorted(cited_families)}) divergem "
        "do vocabulario real em brief_schema.STYLE_FAMILIES "
        f"({sorted(_brief_schema.STYLE_FAMILIES)})"
    )


def test_date_capture_commands_match_the_patterns_they_serve():
    """Todo comando `date -u` que a SKILL.md manda rodar tem que produzir uma
    string que algum validador real aceita.

    Sao dois carimbos, com formatos DIFERENTES: `session.created_at` (ISO-8601
    UTC completo, `tools/brief_schema.py`) e `sources[].retrieved_at` (data
    simples `YYYY-MM-DD`, `ISO_DATE_RE` de `tools/style_schema.py`, exigido
    por `tools/influence.py`). Um formato citado que nao serve a nenhum dos
    dois e instrucao que produz valor recusado depois da entrevista."""
    from datetime import UTC, datetime

    from tools.style_schema import ISO_DATE_RE

    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    strftime_formats = re.findall(r"date -u \+([^\s`)]+)", body)
    assert strftime_formats, "SKILL.md nao cita o comando 'date -u +...'"

    now = datetime.now(UTC)
    seen_created_at = False
    seen_retrieved_at = False
    for fmt in strftime_formats:
        sample = now.strftime(fmt)
        if _brief_schema._SESSION_CREATED_AT_RE.match(sample):
            seen_created_at = True
            continue
        if ISO_DATE_RE.match(sample):
            seen_retrieved_at = True
            continue
        raise AssertionError(
            f"'date -u +{fmt}' produz {sample!r}, que nao bate nem com o "
            "padrao de session.created_at (brief_schema.py) nem com o de "
            "sources[].retrieved_at (ISO_DATE_RE)"
        )

    assert seen_created_at, (
        "SKILL.md nao ensina a capturar session.created_at com 'date -u'"
    )
    assert seen_retrieved_at, (
        "SKILL.md nao ensina a capturar sources[].retrieved_at com 'date -u' — "
        "campo de proveniencia da pesquisa nao pode ficar sem instrucao de "
        "captura, mesma regra de session.created_at"
    )


# Nomes de tecnica que a SKILL.md cita DELIBERADAMENTE fora do conjunto
# autorizavel: o texto os aponta como manual a consultar (`techniques.describe`),
# nunca como tecnica a oferecer para `authorized_techniques`. Cada entrada e
# checada abaixo: tem que existir no indice E estar FORA de
# `SUPPORTED_TECHNIQUES` — assim a lista nao pode virar esconderijo de nome
# inventado nem ficar obsoleta quando o motor passar a implementar a tecnica.
_CITED_AS_MANUAL_ONLY = frozenset({
    "guitar.drop_tuning",
})


def _cited_technique_names(body: str) -> set[str]:
    """Todo identificador `familia.tecnica` entre crases no corpo da skill."""
    families = "|".join(sorted(_brief_schema.STYLE_FAMILIES))
    return set(re.findall(rf"`((?:{families})\.[a-z_][a-z0-9_]*)`", body))


def test_cited_technique_names_are_real():
    """Regressao: a etapa de autorizacao mandava o agente ler em voz alta
    `laid_back_timing`, `rim_shot` e `cross_stick` — tres nomes que NAO existem
    nem no indice de manuais nem no motor. Usuario que marcasse um deles
    derrubava o brief inteiro em `E_BRIEF_TECHNIQUE_NOT_FOUND`, depois da
    entrevista toda. Nenhum nome de tecnica citado pela skill pode ser
    inventado."""
    from tools.techniques.index import build_index

    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    cited = _cited_technique_names(body)
    assert cited, "SKILL.md nao cita nenhum nome canonico de tecnica"

    known = set(build_index().names())
    invented = sorted(cited - known)
    assert not invented, (
        f"SKILL.md cita tecnica que nao existe em manual nenhum: {invented}"
    )


def test_cited_techniques_offered_for_authorization_are_implemented():
    """A skill so pode oferecer para autorizacao o que o motor executa: nome
    apenas documentado no manual faz `brief.validate` recusar o brief inteiro
    com `E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED`. As unicas excecoes sao os nomes
    que o texto cita explicitamente como manual a consultar, listados em
    `_CITED_AS_MANUAL_ONLY`."""
    from tools.techniques import SUPPORTED_TECHNIQUES
    from tools.techniques.index import build_index

    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    cited = _cited_technique_names(body)
    offered = cited - _CITED_AS_MANUAL_ONLY
    not_implemented = sorted(offered - set(SUPPORTED_TECHNIQUES))
    assert not not_implemented, (
        f"SKILL.md cita como autorizavel tecnica que o motor nao executa: "
        f"{not_implemented}"
    )

    # A excecao nao pode virar esconderijo: cada nome dispensado tem que ser
    # real E de fato nao implementado.
    known = set(build_index().names())
    for name in sorted(_CITED_AS_MANUAL_ONLY):
        assert name in known, (
            f"{name} esta em _CITED_AS_MANUAL_ONLY mas nao existe no indice"
        )
        assert name not in SUPPORTED_TECHNIQUES, (
            f"{name} ja e executada pelo motor — tire de _CITED_AS_MANUAL_ONLY "
            "em vez de manter a dispensa"
        )


# Palavras entre crases que a secao de autorizacao usa e que NAO sao nome de
# tecnica: campos de artefato, chaves de saida de tool e nomes de fase. Tudo
# que sobrar nessa secao e presumido nome de tecnica e vai para a checagem.
_AUTHORIZATION_SECTION_NON_TECHNIQUE_WORDS = frozenset({
    "assumptions",
    "authorized_techniques",
    "suggested_techniques",
    "techniques",
    "families_in_scope",
    "unmapped_findings",
    "not_recommended",
    "implemented_only",
    "family",
    "reference",
    "confidence",
    "run",
    "render",
})


def _authorization_section(body: str) -> str:
    start = body.index("## Autorizacao")
    end = body.index("\n## ", start + len("## Autorizacao"))
    return body[start:end]


def test_authorization_section_names_no_invented_technique():
    """Regressao: a fala de exemplo da etapa de autorizacao citava
    `laid_back_timing`, `rim_shot` e `cross_stick`. Nenhum dos tres existe —
    nem no indice de manuais, nem em `SUPPORTED_TECHNIQUES`. Era o script que
    o agente le em voz alta para o usuario marcar, entao usuario que marcasse
    `rim_shot` matava o brief em `E_BRIEF_TECHNIQUE_NOT_FOUND`.

    Nesta secao, toda palavra entre crases que nao e campo de artefato nem
    nome de tool tem que ser nome canonico de tecnica implementada."""
    from tools.registry import list_tools
    from tools.techniques import SUPPORTED_TECHNIQUES

    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)
    section = _authorization_section(body)

    tool_names = {tool["name"] for tool in list_tools()}
    tokens = set(re.findall(r"`([a-z][a-z0-9_.]*)`", section))
    candidates = (
        tokens
        - tool_names
        - _AUTHORIZATION_SECTION_NON_TECHNIQUE_WORDS
        - {"familia.tecnica"}
    )
    bogus = sorted(name for name in candidates if name not in SUPPORTED_TECHNIQUES)
    assert not bogus, (
        "a secao de autorizacao cita nome que nao e tecnica implementada nem "
        f"campo conhecido: {bogus}. Use o nome canonico `familia.tecnica` de "
        "`techniques.list --implemented_only`."
    )


def test_route_vocabulary_matches_plan_routes():
    """Regressao: o modo rapido mandava gravar `route: banda`, que nao existe
    em `tools.plan.ROUTES` — brief.validate recusaria o brief inteiro em
    `E_BRIEF_INVALID`. Todo nome de rota citado entre crases tem que ser uma
    rota real."""
    from tools.plan import ROUTES

    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    cited = set(re.findall(r"`route`: `([a-z_]+)`", body))
    assert cited, "SKILL.md nao cita nenhum valor concreto de route"
    invalid = sorted(cited - set(ROUTES))
    assert not invalid, (
        f"SKILL.md manda gravar rota inexistente: {invalid}; "
        f"validas: {list(ROUTES)}"
    )


def test_product_language_never_promises_a_clone():
    """Posicionamento legal do produto (issue #76): a skill fala em arranjo
    *influenciado por caracteristicas de performance*. As palavras 'clone',
    'copia' e 'reproducao exata' so podem aparecer sendo PROIBIDAS — nunca
    como promessa."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    assert "influenciado por caracteristicas de performance" in body.lower()

    forbidden = re.compile(r"clone|copia|reproducao exata")
    negations = ("nunca", "nao ", "jamais", "recusa", "sem soar")
    offending = []
    for paragraph in re.split(r"\n\s*\n", body):
        lowered = paragraph.lower()
        if forbidden.search(lowered) and not any(n in lowered for n in negations):
            offending.append(paragraph.strip()[:120])
    assert not offending, (
        f"SKILL.md fala de clone/copia sem proibir: {offending}"
    )


def test_flow_has_the_ten_coordinated_steps_in_order():
    """O fluxo do MVP reference-driven tem dez passos numerados, na ordem, e o
    passo 10 (render/entrega) e delegado ao `run` — nao acontece aqui."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    flow = body.split("## O fluxo, em ordem", 1)[1].split("\n## ", 1)[0]
    steps = re.findall(r"^\s*(\d{1,2})\. ", flow, flags=re.MULTILINE)
    assert steps == [str(n) for n in range(1, 11)], (
        f"fluxo da skill nao tem os dez passos em ordem: {steps}"
    )

    lowered = flow.lower()
    for expected in (
        "analyze",
        "influenceprofile",
        "influence.compile",
        "unmapped_findings",
        "authorized_techniques",
        "brief.validate",
        "midi-arranger run",
    ):
        assert expected in lowered, f"passo do fluxo sem {expected!r}"

    assert "voce nao renderiza nada antes da autorizacao" in lowered
