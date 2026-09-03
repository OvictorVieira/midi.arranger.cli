"""InfluenceProfile v1 — contrato entre a pesquisa e o dicionario de tecnicas.

Este modulo define o artefato que aterrissa por MUSICA os achados factuais
de pesquisa da IA do usuario, para consumo do maquinario deterministico.

## Onde ele encaixa

A IA do usuario faz pesquisa ao vivo. O maquinario nao pode receber prosa
arbitraria nem — muito menos — receber conteudo musical de uma obra de
referencia. O `InfluenceProfile` e o filtro entre esses dois mundos:

- entra pesquisa em forma livre;
- sai perfil estruturado, com fontes explicitas, vocabulario fechado por
  dimensao e nivel de confianca declarado POR ACHADO;
- fica no plano DAQUELA musica — nunca vira persona persistente de artista,
  nunca vira base de conhecimento em `knowledge/`.

## Regras invioláveis embutidas

- `family` e restrita a `STYLE_FAMILIES` (bass, drums, guitar, keys) —
  reutilizando a mesma constante do brief para nao criar duas verdades.
- `dimension` e vocabulario FECHADO (`INFLUENCE_DIMENSIONS`); nome fora dele
  e erro, jamais aceito em silencio.
- `intensity` e vocabulario FECHADO (`INFLUENCE_INTENSITIES`); mesma regra.
- `confidence` e vocabulario FECHADO — reutiliza `CONFIDENCE_LEVELS` do
  brief para casar high|medium|low|default por achado.
- Achado sem fonte so passa se marcado `user_stated=True`; se veio de
  pesquisa (source_ids nao-vazio), `user_stated` tem que ser False.
- `semantic_value` e livre em conteudo semantico, mas passa pela MESMA
  barreira anticopia estrutural de `style_schema.py` — sequencia de nomes
  de nota, sequencia de numeros MIDI ou array de eventos em qualquer
  profundidade do payload sao erro.
- Perfil pesquisado vive por musica: nao ha registry global, nao ha cache.
  O consumidor le do disco (ou de memoria) por musica e descarta.
- Numeros exatos de MIDI (`60`, `[60, 64, 67]`, `["C4", "D4"]`) NUNCA
  aterrissam aqui — o maquinario e os manuais sao a fonte de numero. Este
  perfil registra COMPORTAMENTO, nao CONTEUDO.

## O que este modulo expoe

- Dataclasses `InfluenceSource`, `InfluenceFinding`, `InfluenceProfile` —
  a forma canonica em memoria.
- `from_dict(payload)` / `to_dict(profile)` — serializacao estavel.
- `validate(profile)` — validador deterministico que aceita dataclass OU
  dict e roda todas as checagens (vocabulario, fonte, anticopia,
  confianca). Sucesso e silencioso; falha e `InfluenceValidationError`
  com `path` do finding em erro.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .brief_schema import CONFIDENCE_LEVELS, STYLE_FAMILIES
from .style_schema import (
    ISO_DATE_RE,
    NOTE_NAME_RE,
    find_style_musical_content,
)

# --- versao ---------------------------------------------------------------

INFLUENCE_SCHEMA_VERSION = 1

# --- vocabularios fechados ------------------------------------------------

INFLUENCE_DIMENSIONS = (
    "timing_feel",
    "dynamics",
    "articulation",
    "density",
    "arrangement_function",
    "register",
    "section_behavior",
    "execution_technique",
)
"""Dimensoes iniciais fechadas — pesquisa registra COMPORTAMENTO em uma
destas categorias. Nomeadas em `snake_case` ingles para casar com o resto
do maquinario (`STYLE_FAMILIES`, chaves de tecnica no manual). Categoria
nova entra por acrescimo a esta tupla, nunca por texto livre silencioso."""

INFLUENCE_INTENSITIES = ("off", "subtle", "medium", "strong")
"""Intensidades fechadas — descreve o QUANTO daquele comportamento, sem
numero de MIDI (numero e responsabilidade do manual/motor). `off` existe
para registrar 'a referencia NAO usa isso', que e informacao util."""

# `confidence` reutiliza CONFIDENCE_LEVELS do brief: high|medium|low|default.
# Nao redeclara para nao criar duas verdades.

_SOURCE_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_FINDING_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")

# --- dataclasses ----------------------------------------------------------


@dataclass(frozen=True)
class InfluenceSource:
    """Uma fonte citada pela pesquisa.

    `id` e local ao perfil (referenciado por `finding.source_ids`).
    `retrieved_at` e data ISO-8601 curta (`YYYY-MM-DD`) — mesmo padrao de
    `style_schema.ISO_DATE_PATTERN`.
    """

    id: str
    url: str
    title: str
    retrieved_at: str


@dataclass(frozen=True)
class InfluenceFinding:
    """Um achado factual sobre COMPORTAMENTO em uma dimensao.

    - `semantic_value`: string livre em conteudo semantico. NAO pode conter
      sequencia de notas nem numeros MIDI — a barreira anticopia recusa.
    - `source_ids`: referencia estavel a `InfluenceSource.id`. Vazio somente
      quando `user_stated=True`.
    - `user_stated`: True quando o achado e preferencia explicita do
      usuario (nao veio de pesquisa). Neste caso, `source_ids` fica vazio.
    - `confidence`: declarada POR achado (nao pela familia inteira).
    - `summary`: resumo parafraseado do achado — livre, mas anticopia
      tambem valida.
    """

    id: str
    family: str
    dimension: str
    semantic_value: str
    intensity: str
    confidence: str
    source_ids: tuple[str, ...] = ()
    user_stated: bool = False
    summary: str = ""


@dataclass
class InfluenceProfile:
    """O perfil por musica.

    - `version`: `INFLUENCE_SCHEMA_VERSION` (=1). Cravado para permitir
      evolucao versionada no futuro.
    - `project_ref`: identificador local do projeto/musica; opcional. NUNCA
      identidade de artista — perfil vive por musica.
    - `sources`, `findings`, `unmapped_findings`: listas ordenadas. Ordem
      preservada na serializacao.
    """

    version: int = INFLUENCE_SCHEMA_VERSION
    project_ref: str | None = None
    sources: list[InfluenceSource] = field(default_factory=list)
    findings: list[InfluenceFinding] = field(default_factory=list)
    unmapped_findings: list[InfluenceFinding] = field(default_factory=list)


# --- excecao --------------------------------------------------------------


class InfluenceValidationError(ValueError):
    """Levantada quando o perfil falha validacao.

    Mesmo formato de `PlanValidationError`: `path` (JSON pointer-like) +
    `message` acionavel. `code` estavel permite reagir programaticamente.
    """

    def __init__(
        self,
        code: str,
        path: str,
        message: str,
        *,
        hint: str = "",
    ) -> None:
        self.code = code
        self.path = path
        self.message = message
        self.hint = hint
        pieces = [f"[{code}]", f"{path}:", message]
        if hint:
            pieces.append(f"(hint: {hint})")
        super().__init__(" ".join(pieces))


# --- serializacao ---------------------------------------------------------


_SOURCE_KEYS = {"id", "url", "title", "retrieved_at"}
_FINDING_KEYS = {
    "id",
    "family",
    "dimension",
    "semantic_value",
    "intensity",
    "confidence",
    "source_ids",
    "user_stated",
    "summary",
}
_PROFILE_KEYS = {
    "version",
    "project_ref",
    "sources",
    "findings",
    "unmapped_findings",
}


def to_dict(profile: InfluenceProfile) -> dict[str, Any]:
    """Serializa `profile` para um dict estavel."""
    return {
        "version": profile.version,
        "project_ref": profile.project_ref,
        "sources": [_source_to_dict(s) for s in profile.sources],
        "findings": [_finding_to_dict(f) for f in profile.findings],
        "unmapped_findings": [
            _finding_to_dict(f) for f in profile.unmapped_findings
        ],
    }


def _source_to_dict(source: InfluenceSource) -> dict[str, Any]:
    return {
        "id": source.id,
        "url": source.url,
        "title": source.title,
        "retrieved_at": source.retrieved_at,
    }


def _finding_to_dict(finding: InfluenceFinding) -> dict[str, Any]:
    return {
        "id": finding.id,
        "family": finding.family,
        "dimension": finding.dimension,
        "semantic_value": finding.semantic_value,
        "intensity": finding.intensity,
        "confidence": finding.confidence,
        "source_ids": list(finding.source_ids),
        "user_stated": finding.user_stated,
        "summary": finding.summary,
    }


def from_dict(payload: Any) -> InfluenceProfile:
    """Desserializa `payload` para `InfluenceProfile`, rejeitando campos
    desconhecidos e tipos incompativeis. Nao roda checagens semanticas;
    para isso, chame `validate()` em seguida."""
    if not isinstance(payload, dict):
        raise InfluenceValidationError(
            "E_INFLUENCE_SHAPE",
            "",
            f"perfil precisa ser objeto, recebi {type(payload).__name__}",
        )
    _reject_unknown_keys(payload, _PROFILE_KEYS, "")

    version = payload.get("version", INFLUENCE_SCHEMA_VERSION)
    if version != INFLUENCE_SCHEMA_VERSION:
        raise InfluenceValidationError(
            "E_INFLUENCE_VERSION",
            "version",
            f"esperava {INFLUENCE_SCHEMA_VERSION}, recebi {version!r}",
        )

    return InfluenceProfile(
        version=version,
        project_ref=payload.get("project_ref"),
        sources=[
            _source_from_dict(s, f"sources[{i}]")
            for i, s in enumerate(payload.get("sources", []) or [])
        ],
        findings=[
            _finding_from_dict(f, f"findings[{i}]")
            for i, f in enumerate(payload.get("findings", []) or [])
        ],
        unmapped_findings=[
            _finding_from_dict(f, f"unmapped_findings[{i}]")
            for i, f in enumerate(
                payload.get("unmapped_findings", []) or []
            )
        ],
    )


def _source_from_dict(payload: Any, path: str) -> InfluenceSource:
    if not isinstance(payload, dict):
        raise InfluenceValidationError(
            "E_INFLUENCE_SHAPE",
            path,
            f"source precisa ser objeto, recebi {type(payload).__name__}",
        )
    _reject_unknown_keys(payload, _SOURCE_KEYS, path)
    for key in ("id", "url", "title", "retrieved_at"):
        if key not in payload:
            raise InfluenceValidationError(
                "E_INFLUENCE_SHAPE",
                f"{path}.{key}",
                "campo obrigatorio ausente",
            )
    return InfluenceSource(
        id=payload["id"],
        url=payload["url"],
        title=payload["title"],
        retrieved_at=payload["retrieved_at"],
    )


def _finding_from_dict(payload: Any, path: str) -> InfluenceFinding:
    if not isinstance(payload, dict):
        raise InfluenceValidationError(
            "E_INFLUENCE_SHAPE",
            path,
            f"finding precisa ser objeto, recebi {type(payload).__name__}",
        )
    _reject_unknown_keys(payload, _FINDING_KEYS, path)
    for key in (
        "id", "family", "dimension", "semantic_value", "intensity",
        "confidence",
    ):
        if key not in payload:
            raise InfluenceValidationError(
                "E_INFLUENCE_SHAPE",
                f"{path}.{key}",
                "campo obrigatorio ausente",
            )
    source_ids = payload.get("source_ids", []) or []
    if not isinstance(source_ids, list) or not all(
        isinstance(s, str) for s in source_ids
    ):
        raise InfluenceValidationError(
            "E_INFLUENCE_SHAPE",
            f"{path}.source_ids",
            "precisa ser lista de strings",
        )
    return InfluenceFinding(
        id=payload["id"],
        family=payload["family"],
        dimension=payload["dimension"],
        semantic_value=payload["semantic_value"],
        intensity=payload["intensity"],
        confidence=payload["confidence"],
        source_ids=tuple(source_ids),
        user_stated=bool(payload.get("user_stated", False)),
        summary=payload.get("summary", ""),
    )


def _reject_unknown_keys(
    payload: dict[str, Any], allowed: set[str], path: str,
) -> None:
    extras = sorted(set(payload) - allowed)
    if extras:
        raise InfluenceValidationError(
            "E_INFLUENCE_UNKNOWN_FIELD",
            path or "<root>",
            f"campos desconhecidos: {extras}",
            hint=f"campos aceitos: {sorted(allowed)}",
        )


# --- validador ------------------------------------------------------------


def validate(profile: InfluenceProfile | dict[str, Any]) -> None:
    """Valida o perfil deterministicamente.

    Aceita `InfluenceProfile` OU dict cru (que sera passado por `from_dict`
    antes das checagens semanticas). Sucesso e silencioso; falha e
    `InfluenceValidationError` citando o `finding.id` ou o `source.id` em
    erro.

    Regras, na ordem:
    1. `version` casa com `INFLUENCE_SCHEMA_VERSION`.
    2. Anticopia estrutural: nenhum sub-campo do perfil pode conter
       sequencia de notas/eventos (mesma barreira usada em `style`).
    3. `sources[i].id` unico, formato aceito, `retrieved_at` no formato
       ISO curto, `url`/`title` nao vazios.
    4. Cada finding em `findings` e `unmapped_findings`: vocabulario
       fechado (`family`, `dimension`, `intensity`, `confidence`),
       `source_ids` referenciam sources existentes, e a regra
       "fonte OU user_stated": achado sem fonte precisa de
       `user_stated=True`; achado com fonte precisa de `user_stated=False`
       (contradicao entre 'fonte + preferencia' e' inconsistencia — o
       validador nao adivinha qual dos dois vale).
    5. Anticopia semantica: `semantic_value` e `summary` (strings livres)
       nao podem parecer sequencia de nomes de nota.
    """
    if isinstance(profile, dict):
        # Roda a barreira anticopia estrutural ANTES da conversao — dict
        # cru pode ter campos musicais em profundidade que `from_dict`
        # rejeitaria como desconhecidos, mas queremos uma mensagem
        # anticopia especifica quando o formato do dado E musica.
        _validate_no_musical_content(profile, "")
        profile = from_dict(profile)
    elif not isinstance(profile, InfluenceProfile):
        raise InfluenceValidationError(
            "E_INFLUENCE_SHAPE",
            "",
            (
                "perfil precisa ser InfluenceProfile ou dict, recebi "
                f"{type(profile).__name__}"
            ),
        )

    if profile.version != INFLUENCE_SCHEMA_VERSION:
        raise InfluenceValidationError(
            "E_INFLUENCE_VERSION",
            "version",
            f"esperava {INFLUENCE_SCHEMA_VERSION}, recebi {profile.version!r}",
        )

    # Sanitiza a forma serializada para varredura anticopia estrutural.
    _validate_no_musical_content(to_dict(profile), "")

    source_ids: set[str] = set()
    for i, source in enumerate(profile.sources):
        path = f"sources[{i}]"
        _validate_source(source, path)
        if source.id in source_ids:
            raise InfluenceValidationError(
                "E_INFLUENCE_DUP_SOURCE_ID",
                f"{path}.id",
                f"source.id duplicada: {source.id!r}",
            )
        source_ids.add(source.id)

    finding_ids: set[str] = set()
    for i, finding in enumerate(profile.findings):
        path = f"findings[{i}]"
        _validate_finding(finding, path, source_ids, finding_ids)

    for i, finding in enumerate(profile.unmapped_findings):
        path = f"unmapped_findings[{i}]"
        _validate_finding(finding, path, source_ids, finding_ids)


def _validate_source(source: InfluenceSource, path: str) -> None:
    if not source.id or not _SOURCE_ID_RE.match(source.id):
        raise InfluenceValidationError(
            "E_INFLUENCE_SOURCE_ID_INVALID",
            f"{path}.id",
            f"source.id invalida: {source.id!r}",
            hint="use [A-Za-z0-9_.:-], 1..64 caracteres",
        )
    if not source.url.strip():
        raise InfluenceValidationError(
            "E_INFLUENCE_SOURCE_URL_EMPTY",
            f"{path}.url",
            "url vazia",
        )
    if not source.title.strip():
        raise InfluenceValidationError(
            "E_INFLUENCE_SOURCE_TITLE_EMPTY",
            f"{path}.title",
            "title vazio",
        )
    if not ISO_DATE_RE.match(source.retrieved_at):
        raise InfluenceValidationError(
            "E_INFLUENCE_SOURCE_DATE_INVALID",
            f"{path}.retrieved_at",
            f"retrieved_at fora do formato ISO YYYY-MM-DD: {source.retrieved_at!r}",
        )


def _validate_finding(
    finding: InfluenceFinding,
    path: str,
    source_ids: set[str],
    seen_finding_ids: set[str],
) -> None:
    if not finding.id or not _FINDING_ID_RE.match(finding.id):
        raise InfluenceValidationError(
            "E_INFLUENCE_FINDING_ID_INVALID",
            f"{path}.id",
            f"finding.id invalido: {finding.id!r}",
            hint="use [A-Za-z0-9_.:-], 1..64 caracteres",
        )
    if finding.id in seen_finding_ids:
        raise InfluenceValidationError(
            "E_INFLUENCE_DUP_FINDING_ID",
            f"{path}.id",
            f"finding.id duplicado: {finding.id!r}",
        )
    seen_finding_ids.add(finding.id)

    if finding.family not in STYLE_FAMILIES:
        raise InfluenceValidationError(
            "E_INFLUENCE_UNKNOWN_FAMILY",
            f"{path}.family",
            f"familia {finding.family!r} fora do vocabulario",
            hint=f"familias aceitas: {list(STYLE_FAMILIES)}",
        )
    if finding.dimension not in INFLUENCE_DIMENSIONS:
        raise InfluenceValidationError(
            "E_INFLUENCE_UNKNOWN_DIMENSION",
            f"{path}.dimension",
            f"dimensao {finding.dimension!r} fora do vocabulario",
            hint=f"dimensoes aceitas: {list(INFLUENCE_DIMENSIONS)}",
        )
    if finding.intensity not in INFLUENCE_INTENSITIES:
        raise InfluenceValidationError(
            "E_INFLUENCE_UNKNOWN_INTENSITY",
            f"{path}.intensity",
            f"intensity {finding.intensity!r} fora do vocabulario",
            hint=f"intensidades aceitas: {list(INFLUENCE_INTENSITIES)}",
        )
    if finding.confidence not in CONFIDENCE_LEVELS:
        raise InfluenceValidationError(
            "E_INFLUENCE_UNKNOWN_CONFIDENCE",
            f"{path}.confidence",
            f"confidence {finding.confidence!r} fora do vocabulario",
            hint=f"confiancas aceitas: {list(CONFIDENCE_LEVELS)}",
        )
    if not isinstance(finding.semantic_value, str) or not finding.semantic_value.strip():
        raise InfluenceValidationError(
            "E_INFLUENCE_SEMANTIC_VALUE_EMPTY",
            f"{path}.semantic_value",
            "semantic_value vazio",
        )

    # Referencia a fontes: cada source_id tem que existir.
    for j, sid in enumerate(finding.source_ids):
        if sid not in source_ids:
            raise InfluenceValidationError(
                "E_INFLUENCE_SOURCE_ID_UNKNOWN",
                f"{path}.source_ids[{j}]",
                f"source_id {sid!r} nao existe em sources[]",
            )

    # Regra fonte-vs-preferencia. Achado de pesquisa aponta fonte;
    # achado sem fonte precisa ser declarado como preferencia explicita.
    has_sources = len(finding.source_ids) > 0
    if not has_sources and not finding.user_stated:
        raise InfluenceValidationError(
            "E_INFLUENCE_FINDING_NO_SOURCE",
            f"{path}.source_ids",
            (
                f"finding {finding.id!r} nao aponta fonte e nao esta "
                "marcado como preferencia do usuario"
            ),
            hint=(
                "declare source_ids apontando para sources[], ou marque "
                "user_stated=true se for preferencia explicita do usuario"
            ),
        )
    if has_sources and finding.user_stated:
        raise InfluenceValidationError(
            "E_INFLUENCE_FINDING_SOURCE_AND_USER",
            f"{path}.user_stated",
            (
                f"finding {finding.id!r} tem source_ids e user_stated=true — "
                "o validador nao adivinha qual dos dois vale"
            ),
            hint=(
                "escolha um: se veio de pesquisa mantenha as fontes e "
                "user_stated=false; se e preferencia do usuario, apague "
                "source_ids"
            ),
        )

    _validate_free_string(finding.semantic_value, f"{path}.semantic_value")
    if finding.summary:
        _validate_free_string(finding.summary, f"{path}.summary")


# --- barreiras anticopia --------------------------------------------------


def _validate_no_musical_content(node: Any, path: str) -> None:
    """Reusa a barreira estrutural de `style` sobre o payload do perfil.

    `find_style_musical_content` recusa chaves de conteudo musical (`notes`,
    `pattern`, `riff`, ...), sequencias planas de numeros MIDI, arrays de
    eventos com pitch+time, arrays de nomes de nota, pares numericos em
    sequencia. Passamos o payload inteiro por ela para bloquear tentativas
    de embutir nota/tick/velocity em qualquer profundidade — inclusive em
    campos livres como `summary` e `semantic_value` que ainda sao strings
    (a checagem por dict/lista atua so em containers; strings vao pelo
    `_validate_free_string`).
    """
    violation = find_style_musical_content(node, path)
    if violation is not None:
        vpath, reason = violation
        raise InfluenceValidationError(
            "E_INFLUENCE_MUSICAL_CONTENT",
            vpath,
            (
                f"{reason} — o perfil registra COMPORTAMENTO, nao "
                "CONTEUDO musical"
            ),
            hint=(
                "descreva o comportamento em prosa parafraseada em "
                "semantic_value/summary e categorize pela dimensao "
                "(timing_feel, dynamics, ...)"
            ),
        )


def _validate_free_string(value: str, path: str) -> None:
    """Recusa string que carregue disfarcadamente uma sequencia de notas.

    ACHADO PR #101: a versao anterior so contava sequencia quando os
    tokens batiam CONTIGUAMENTE no split (`"C4 D4 E4"`). Prosa natural
    intercala conectivo entre as notas — exatamente a forma mais provavel
    de a IA do usuario escrever `summary`/`semantic_value` — e escapava
    ilesa: `"groove com nota pedal em D4, subindo pra F4, depois A4"` ou
    `"toca C4 depois D4 depois E4 depois F4"` passavam, enquanto so a
    lista colada `"riff: C4 D4 E4"` era bloqueada. A heuristica corrigida
    NAO exige adjacencia posicional: conta quantos tokens da string
    INTEIRA batem com o padrao, em qualquer posicao, ignorando quantas
    palavras nao-nota existam entre eles. Tres ou mais ocorrencias na
    mesma string (`semantic_value`/`summary` sao curtos — uma frase ou
    duas — entao "na string inteira" ja e a janela razoavel; nao ha
    necessidade de uma janela deslizante menor) e o sinal de sequencia
    musical disfarcada de prosa. Mencao ISOLADA de uma unica nota
    (`"tonica em D4"`, `"pedaliza a tonica em C"`) continua tendo so 1
    ocorrencia e passa — a barreira nao pode virar bloqueio hiperagressivo
    de qualquer mencao musical legitima.

    Duas familias de padrao, ambas contadas por ocorrencia total (nao por
    run contiguo):
    - Nome de nota em notacao cientifica COM oitava (`NOTE_NAME_RE`,
      importado de `style_schema` — mesmo padrao usado por brief/plano,
      nao duplicado aqui) — `C4`, `F#3`, `Bb-1`.
    - Nome de nota SEM oitava (`_BARE_NOTE_RE`, regex LOCAL a este
      modulo — decisao deliberada de NAO alterar `NOTE_NAME_RE` em
      `style_schema.py`, que exige digito de oitava e e reusado em outros
      pontos do projeto; mudar seu formato ali teria efeito colateral fora
      do escopo deste achado). `_BARE_NOTE_RE` so casa letra MAIUSCULA
      A-G com acidente opcional (`C`, `F#`, `Bb`): em portugues, "a" e "e"
      minusculos sao preposicao/conjuncao de altissima frequencia e
      virariam falso-positivo constante se contassem como nota; exigir
      maiuscula E 3+ ocorrencias na mesma string reduz esse risco sem
      reabrir o furo de nota solta em prosa (`"sobe de C pra D pra E pra
      F"` tem 4 ocorrencias e e rejeitado).
    - Sequencia de inteiros em faixa MIDI (0..127) — mesma contagem
      nao-contigua.

    ACHADO PR #101 (review do Codex): o split so cortava em espaco/virgula/
    ponto-e-virgula, entao pontuacao musical comum — `/` e `-` entre notas
    (`"C4/D4/E4"`, `"C4-D4-E4"`) ou ponto final apos a ultima nota
    (`"C4 depois D4 depois E4."`) deixava o token colado a pontuacao
    (`"E4."`, `"C4/D4/E4"` inteiro) e ele parava de casar `NOTE_NAME_RE`/
    `_BARE_NOTE_RE`, furando a barreira. O tokenizador corrigido quebra em
    QUALQUER caractere que nao seja letra, digito ou acidente de nota
    (`#`, `♯`, `♭`) — cobre `/`, `-`, `.`, `!`, `?`, parenteses etc, alem
    de espaco/virgula/ponto-e-virgula que ja funcionavam. Acidente fica
    fora da classe de separador de proposito: cortar em `#`/`♭` quebraria
    `F#3`/`Bb` em pedacos que nao casam nota nenhuma.
    """
    tokens = [t for t in re.split(r"[^A-Za-z0-9#♯♭]+", value) if t]

    note_hits = sum(1 for t in tokens if _looks_like_note_name(t))
    if note_hits >= 3:
        raise InfluenceValidationError(
            "E_INFLUENCE_MUSICAL_CONTENT",
            path,
            (
                "string carrega sequencia de nomes de nota — o perfil nao "
                "aceita conteudo musical, mesmo em prosa"
            ),
            hint=(
                "descreva o comportamento (ex.: 'motivo cromatico "
                "descendente') em vez de escrever as notas"
            ),
        )

    midi_hits = sum(1 for t in tokens if _is_midi_int_token(t))
    if midi_hits >= 3:
        raise InfluenceValidationError(
            "E_INFLUENCE_MUSICAL_CONTENT",
            path,
            (
                "string carrega sequencia de numeros na faixa MIDI — o "
                "perfil nao aceita conteudo musical, mesmo em prosa"
            ),
            hint=(
                "descreva o comportamento em prosa; numeros exatos de MIDI "
                "vem dos manuais, nao da pesquisa"
            ),
        )


# Nome de nota SEM oitava (`C`, `F#`, `Bb`, `G♯`) — LOCAL a este modulo, ver
# docstring de `_validate_free_string` para a razao de nao mexer em
# `style_schema.NOTE_NAME_RE`. Restrito a maiuscula para nao casar "a"/"e"
# minusculos (preposicao/conjuncao comuns em portugues).
_BARE_NOTE_RE = re.compile(r"^[A-G](#|b|♯|♭)?$")


def _looks_like_note_name(token: str) -> bool:
    """Casa nome de nota COM oitava (`style_schema.NOTE_NAME_RE`) OU SEM
    oitava (`_BARE_NOTE_RE`, local)."""
    return bool(NOTE_NAME_RE.match(token) or _BARE_NOTE_RE.match(token))


def _is_midi_int_token(token: str) -> bool:
    try:
        n = int(token)
    except ValueError:
        return False
    return 0 <= n <= 127


__all__ = [
    "CONFIDENCE_LEVELS",
    "INFLUENCE_DIMENSIONS",
    "INFLUENCE_INTENSITIES",
    "INFLUENCE_SCHEMA_VERSION",
    "InfluenceFinding",
    "InfluenceProfile",
    "InfluenceSource",
    "InfluenceValidationError",
    "STYLE_FAMILIES",
    "from_dict",
    "to_dict",
    "validate",
]
