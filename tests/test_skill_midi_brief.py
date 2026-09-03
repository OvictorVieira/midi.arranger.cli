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
  `assumptions`, modo rapido nao trava.
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
        if candidate == "arrangement-brief.json":
            # produzido pela skill; nao existe no repo.
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


def test_session_created_at_command_matches_brief_schema_pattern():
    """O comando `date -u` que a SKILL.md manda rodar para `created_at`
    precisa produzir uma string que bate com o padrao ISO-8601 UTC que
    `tools/brief_schema.py` valida (`_SESSION_CREATED_AT_PATTERN`)."""
    text = SKILL_PATH.read_text(encoding="utf-8")
    _, body = _split_frontmatter(text)

    strftime_formats = re.findall(r"date -u \+([^\s`)]+)", body)
    assert strftime_formats, "SKILL.md nao cita o comando 'date -u +...'"

    from datetime import UTC, datetime

    for fmt in strftime_formats:
        sample = datetime.now(UTC).strftime(fmt)
        assert _brief_schema._SESSION_CREATED_AT_RE.match(sample), (
            f"'date -u +{fmt}' produz {sample!r}, que nao bate com o "
            "padrao ISO-8601 UTC validado em brief_schema.py"
        )
