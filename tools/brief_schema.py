"""Schema e validador do arrangement-brief.json (US-001).

O brief e a fronteira entre a fase interativa (skill) e a fase autonoma
(`run`): carrega o que o usuario quer, o mapa de secoes confirmado, as
suposicoes declaradas quando faltou resposta, e o perfil de estilo
pesquisado por familia — com fontes, data e confianca.

## Regras invioláveis embutidas

- `style` NUNCA carrega conteudo musical. Nem melodia, nem riff, nem
  sequencia de notas. So parametro de tecnica e nome de tecnica do manual.
  A garantia e dupla: (1) o schema so aceita campos declarados, com
  `parameters` restrito a `{string -> number}`; (2) uma varredura semantica
  em `style` recusa arrays de inteiros na faixa MIDI (0..127) e arrays de
  strings em formato de nome de nota (`C4`, `F#3`) — em qualquer profundidade.
- Toda tecnica citada em `style.<familia>.techniques[].name` precisa existir
  no indice de tecnicas (`knowledge/tecnicas/`, via `techniques.build_index`).
  Nome desconhecido vira erro com hint listando as mais parecidas.
- `confidence` e vocabulario FECHADO: high | medium | low | default.
- `instruments.<familia>` (issue #44) e configuracao de instrumento de
  corda POR MUSICA — nunca conhecimento de repositorio, nunca inferida
  em silencio. So `guitar` e `bass` (as familias de corda). `known: false`
  e ausencia declarada ("nao sei"), nunca chute. `tuning.name` so resolve
  contra o manual `guitar.drop_tuning` (via `tools.tuning.resolve_tuning_name`)
  — nome desconhecido para o numero de cordas declarado exige `tuning.notes`
  explicito, nunca aceito em silencio. `tuning.notes` tem que ter o mesmo
  tamanho de `strings` e vir em ordem estritamente ascendente (grave->agudo).
  `bass` tambem declara `playing_style` (finger|pick|slap) e `notation`
  (`written` = soa uma oitava abaixo do escrito; `sounding` = ja na altura
  que soa) — baixo e instrumento transpositor, confundir os dois faz o
  arranjador escrever a linha uma oitava no lugar errado.

## O que este modulo expoe

- `brief_schema()`: JSON Schema estrito (subset do registry).
- `validate_brief(brief)`: valida contra schema e roda as checagens
  semanticas. Sucesso e silencioso; falha e `ToolError` com `path` do
  campo em erro.
- `BRIEF_VALIDATE_TOOL`: tool para `brief.validate` no registry global.
"""

from __future__ import annotations

import difflib
from typing import Any

from . import techniques as techniques_mod
from . import tuning as tuning_mod
from .plan import ROUTES
from .registry import SchemaError, Tool, ToolError, validate_input
from .style_schema import NOTE_NAME_RE as _NOTE_NAME_RE
from .style_schema import find_style_musical_content, style_technique_schema

# --- vocabularios fechados -------------------------------------------------

BRIEF_SCHEMA_VERSION = 1

CONFIDENCE_LEVELS = ("high", "medium", "low", "default")

REQUISITO_TYPES = (
    "tecnica", "reducao", "criacao", "estilo", "restricao", "intensidade",
)

REQUISITO_FAMILIES = (
    "bass", "drums", "guitar", "keys", "arranjo", "geral",
)

STYLE_FAMILIES = ("bass", "drums", "guitar", "keys")

_SHA256_RE = r"^[0-9a-f]{64}$"

_MIDI_PITCH_MIN = 0
_MIDI_PITCH_MAX = 127

# --- issue #44 — configuracao de instrumento de corda, por musica ---------
#
# `instruments` guarda, por familia de CORDA presente no MIDI de origem, o
# numero de cordas e a afinacao declarada pelo usuario — nunca inferida em
# silencio, nunca conhecimento de repositorio (vive so neste brief). So as
# duas familias de corda dedilhada entram aqui; bateria e teclas nao tem
# afinacao de corda para declarar.
STRINGED_INSTRUMENT_FAMILIES = ("guitar", "bass")

BASS_PLAYING_STYLES = ("finger", "pick", "slap")
"""Vocabulario fechado de `instruments.bass.playing_style` — dedo, palheta
ou slap. Sem "unknown" aqui porque a ausencia de resposta e representada
por `known: false` na familia inteira, nao por um valor dentro dela."""

BASS_NOTATION_VALUES = ("written", "sounding")
"""`instruments.bass.notation`: baixo e instrumento transpositor (soa uma
oitava abaixo do escrito). `written` = a track guarda altura escrita (o
caso comum de exportacao de tablatura/DAW); `sounding` = a track ja guarda
a altura que soa. Confundir os dois faz o arranjador escrever uma oitava
no lugar errado — ver issue #44."""


def _instrument_tuning_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"]},
            "notes": {
                "type": "array",
                "items": {
                    "type": "integer",
                    "minimum": _MIDI_PITCH_MIN,
                    "maximum": _MIDI_PITCH_MAX,
                },
            },
        },
        "required": ["name", "notes"],
    }


def _instrument_family_schema(family: str) -> dict[str, Any]:
    properties: dict[str, Any] = {
        "known": {"type": "boolean"},
        "strings": {"type": ["integer", "null"], "minimum": 1},
        "tuning": {
            "oneOf": [{"type": "null"}, _instrument_tuning_schema()],
        },
    }
    required = ["known", "strings", "tuning"]
    if family == "bass":
        properties["playing_style"] = {
            "enum": [*BASS_PLAYING_STYLES, None],
        }
        properties["notation"] = {"enum": [*BASS_NOTATION_VALUES, None]}
        required += ["playing_style", "notation"]
    return {"type": "object", "properties": properties, "required": required}


# --- schema ---------------------------------------------------------------


def _family_style_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "reference": {"type": ["string", "null"]},
            "researched_at": {"type": ["string", "null"]},
            "sources": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
            },
            "confidence": {"enum": list(CONFIDENCE_LEVELS)},
            "techniques": {
                "type": "array",
                "items": style_technique_schema(),
            },
            "authorized_techniques": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "default": [],
            },
            "suggested_techniques": {
                "type": "array",
                "items": style_technique_schema(),
                "default": [],
            },
            "parameters": {
                "type": "object",
                "additionalProperties": {"type": "number"},
            },
        },
        "required": ["reference", "sources", "confidence", "techniques"],
    }


def brief_schema() -> dict[str, Any]:
    """JSON Schema estrito do arrangement-brief.json."""
    return {
        "type": "object",
        "properties": {
            "version": {"type": "integer", "const": BRIEF_SCHEMA_VERSION},
            "source_midi": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "sha256": {"type": "string", "pattern": _SHA256_RE},
                    "tempo": {"type": ["number", "null"]},
                    "key": {"type": ["string", "null"]},
                    "bars": {
                        "oneOf": [
                            {"type": "null"},
                            {"type": "integer", "minimum": 0},
                        ],
                    },
                },
                "required": ["path", "sha256", "tempo", "key", "bars"],
            },
            "demanda": {"type": "string", "minLength": 1},
            "route": {"enum": list(ROUTES)},
            "sections_confirmed": {"type": "boolean"},
            "assumptions": {
                "type": "array", "items": {"type": "string", "minLength": 1},
            },
            "requisitos": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "minLength": 1},
                        "familia": {"enum": list(REQUISITO_FAMILIES)},
                        "tipo": {"enum": list(REQUISITO_TYPES)},
                        "alvo": {"type": "string", "minLength": 1},
                        "descricao": {"type": "string", "minLength": 1},
                    },
                    "required": ["id", "familia", "tipo", "alvo", "descricao"],
                },
            },
            "style": {
                "type": "object",
                "properties": {
                    fam: _family_style_schema() for fam in STYLE_FAMILIES
                },
                # style aceita apenas as familias declaradas; um familia
                # desconhecida (ex.: "vocal") entra como erro estrutural.
            },
            "instruments": {
                "type": "object",
                "properties": {
                    fam: _instrument_family_schema(fam)
                    for fam in STRINGED_INSTRUMENT_FAMILIES
                },
                # so as familias de corda declaradas; qualquer outra chave
                # (ex.: "drums") e erro estrutural — bateria nao tem corda.
            },
            "restricoes": {
                "type": "array", "items": {"type": "string", "minLength": 1},
            },
            "antirreferencias": {
                "type": "array", "items": {"type": "string", "minLength": 1},
            },
        },
        "required": [
            "version", "source_midi", "demanda", "route", "sections_confirmed",
            "assumptions", "requisitos", "style", "restricoes",
            "antirreferencias",
        ],
    }


# --- varredura semantica de conteudo musical ------------------------------


def _looks_like_note_sequence(value: Any) -> str | None:
    """Se `value` parece sequencia de notas, devolve o motivo; senao `None`.

    Duas heuristicas casam com o que a regra do repo chama de conteudo
    musical:
    - array de dois ou mais inteiros, todos na faixa MIDI (0..127);
    - array (nao vazio) de strings em formato de nome de nota (C4, F#3, Bb-1).
    """
    if not isinstance(value, list) or not value:
        return None
    if len(value) >= 2 and all(
        isinstance(v, int) and not isinstance(v, bool)
        and _MIDI_PITCH_MIN <= v <= _MIDI_PITCH_MAX
        for v in value
    ):
        return (
            f"array de {len(value)} inteiros na faixa MIDI (0..127) — "
            f"parece sequencia de notas"
        )
    if all(
        isinstance(v, str) and _NOTE_NAME_RE.match(v.strip())
        for v in value
    ):
        return (
            f"array de strings em formato de nome de nota "
            f"(ex.: {value[0]!r})"
        )
    return None


def _scan_no_musical_content(node: Any, path: str) -> None:
    """Percorre `node` e recusa qualquer sub-valor que pareca sequencia de notas.

    Path acumula um JSON pointer-like para apontar o campo exato em erro.
    """
    reason = _looks_like_note_sequence(node)
    if reason is not None:
        raise ToolError(
            "E_BRIEF_MUSICAL_CONTENT",
            f"campo {path!r} carrega conteudo musical: {reason}. "
            f"style so aceita parametro de tecnica e nome de tecnica — "
            f"nao conteudo musical.",
            path=path,
            hint=(
                "Descreva o comportamento pelo nome da tecnica em "
                "techniques[].name e por parametros numericos em parameters."
            ),
        )
    if isinstance(node, dict):
        for k, v in node.items():
            _scan_no_musical_content(v, f"{path}.{k}" if path else k)
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _scan_no_musical_content(item, f"{path}[{i}]")


# --- validador ------------------------------------------------------------


def _resolve_family_technique(
    idx: techniques_mod.TechniqueIndex, family: str, name: str, path: str,
) -> techniques_mod.Technique:
    """Devolve a Technique casada por nome, resolvendo pela familia do path.

    Levanta `E_BRIEF_TECHNIQUE_NOT_FOUND` citando o path e sugerindo tecnicas
    parecidas quando nada casa. E a mesma resolucao usada em `techniques[]`,
    `authorized_techniques` e `suggested_techniques` — nunca duas verdades.

    A familia do path MANDA. Tentar `idx.get(name)` primeiro deixava passar
    canonico de outra familia: `drums.ghost_notes` declarado sob `style.bass`
    resolvia pelo canonico e era aceito. `style.<familia>` so pode declarar
    tecnica daquela familia — o bloco de estilo do baixo nao autoriza nada de
    bateria.
    """
    resolved = next(
        (t for t in idx.candidates(name) if t.family == family), None
    )
    if resolved is not None:
        return resolved
    foreign = idx.get(name)
    if foreign is not None:
        raise ToolError(
            "E_BRIEF_TECHNIQUE_WRONG_FAMILY",
            f"tecnica {name!r} e da familia {foreign.family!r}, mas foi "
            f"declarada em style.{family} — bloco de estilo de uma familia "
            f"so declara tecnica dela mesma",
            path=path,
            hint=(
                f"tecnicas de {family}: "
                f"{[t.canonical for t in idx.by_family(family)]}"
            ),
        )
    candidates = list(idx.names()) + [t.name for t in idx.techniques]
    matches = difflib.get_close_matches(name, candidates, n=5, cutoff=0.4)
    raise ToolError(
        "E_BRIEF_TECHNIQUE_NOT_FOUND",
        f"tecnica {name!r} declarada em style.{family} nao existe no indice",
        path=path,
        hint=(
            f"tecnicas parecidas: {matches}"
            if matches
            else f"tecnicas disponiveis: {list(idx.names())}"
        ),
    )


def _validate_family_techniques(
    family: str, entry: dict[str, Any], idx: techniques_mod.TechniqueIndex,
) -> None:
    """Valida os tres campos de tecnica de uma familia de style.

    Ordem intencional:
    1. Existencia de cada nome no indice — `authorized`, `suggested` e
       `techniques`. Nome desconhecido erra ANTES de qualquer regra de
       autorizacao, para que erros de digitacao apontem para o field certo.
    2. `authorized_techniques[]` tem que estar em `SUPPORTED_TECHNIQUES` do
       motor: tecnica documentada mas SEM aplicador registrado nao pode ser
       autorizada, senao o brief compromete uma execucao que o motor nao
       consegue entregar. Aceitar-e-ignorar e o vicio ja rejeitado duas
       vezes nesta base (`_identity_apply` e o gerador de bateria de
       andaime). A fronteira e aqui, ANTES do `run` — nao no render.
       `suggested_techniques[]` NAO passa por essa barreira: sugestao e o
       registro do que a pesquisa levantou, inclusive capacidade futura.
    3. Anticopia sobre `suggested_techniques` via helper compartilhado.
    4. `techniques` como SUBCONJUNTO de `authorized_techniques`.
    """
    from .techniques import SUPPORTED_TECHNIQUES

    authorized_raw = entry.get("authorized_techniques", [])
    authorized_canonicals: set[str] = set()
    for i, name in enumerate(authorized_raw):
        path = f"style.{family}.authorized_techniques[{i}]"
        resolved = _resolve_family_technique(idx, family, name, path)
        if resolved.canonical not in SUPPORTED_TECHNIQUES:
            implemented_in_family = sorted(
                c for c in SUPPORTED_TECHNIQUES if c.startswith(f"{family}.")
            )
            listing = (
                ", ".join(implemented_in_family)
                if implemented_in_family
                else f"(nenhuma tecnica de {family} implementada no motor)"
            )
            raise ToolError(
                "E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED",
                f"tecnica {resolved.canonical!r} existe no manual mas nao "
                f"esta implementada pelo motor — o brief nao pode autorizar "
                f"tecnica sem aplicador, porque o `run` nao vai aplicar "
                f"nada. Tecnicas de {family} implementadas hoje: {listing}",
                path=path,
                hint=(
                    "consulte `techniques.list` com "
                    "`implemented_only=true` para ver o vocabulario que o "
                    "motor consegue executar; tecnica apenas documentada "
                    "e capacidade futura, nao autorizacao aceita agora"
                ),
            )
        authorized_canonicals.add(resolved.canonical)

    suggested = entry.get("suggested_techniques", [])
    for i, tech in enumerate(suggested):
        _resolve_family_technique(
            idx, family, tech["name"],
            f"style.{family}.suggested_techniques[{i}].name",
        )

    violation = find_style_musical_content(
        suggested, f"style.{family}.suggested_techniques",
    )
    if violation is not None:
        vpath, reason = violation
        raise ToolError(
            "E_BRIEF_MUSICAL_CONTENT",
            f"campo {vpath!r} carrega conteudo musical: {reason}. "
            f"suggested_techniques so aceita nome de tecnica e parametros — "
            f"nao conteudo musical.",
            path=vpath,
            hint=(
                "Descreva o comportamento pelo nome da tecnica em "
                "suggested_techniques[].name e por parametros numericos."
            ),
        )

    for i, tech in enumerate(entry.get("techniques", [])):
        name = tech["name"]
        path = f"style.{family}.techniques[{i}].name"
        resolved = _resolve_family_technique(idx, family, name, path)
        if resolved.canonical not in authorized_canonicals:
            raise ToolError(
                "E_BRIEF_TECHNIQUE_NOT_AUTHORIZED",
                f"tecnica {name!r} em style.{family}.techniques nao esta em "
                f"authorized_techniques da familia {family!r} — sugestao nao "
                f"e autorizacao, e o usuario nao autorizou esta tecnica",
                path=path,
                hint=(
                    f"authorized_techniques da familia {family}: "
                    f"{sorted(authorized_canonicals) or '[]'}"
                ),
            )


def _validate_instrument_tuning(
    family: str, strings: int, tuning: dict[str, Any] | None, path: str,
) -> None:
    """Valida `instruments.<familia>.tuning` contra `strings` — a peca
    central da issue #44. Regras, na ordem:

    1. `tuning` ausente (None) quando `known=true` e erro — se o usuario
       sabe a configuracao, a afinacao faz parte dela.
    2. Nem `name` nem `notes`: erro — declaracao vazia nao e declaracao.
    3. `name` presente: resolve contra o manual `guitar.drop_tuning` via
       `tools.tuning.resolve_tuning_name`. Nome desconhecido (formato nao
       reconhecido OU sem entrada no manual para aquele numero de cordas)
       e erro pedindo `notes` explicito — NUNCA aceito em silencio.
    4. `notes` presente: tamanho tem que bater com `strings` (N cordas
       precisam de N notas) e a sequencia tem que estar estritamente
       ascendente (grave -> agudo, sem corda repetida).
    5. `name` E `notes` presentes ao mesmo tempo: tem que concordar. Nome
       que resolve para um conjunto e notas que declaram outro e a mesma
       categoria de erro que nome desconhecido — a declaracao esta
       inconsistente e o validador nao adivinha qual dos dois vale.
    """
    if tuning is None:
        raise ToolError(
            "E_BRIEF_INSTRUMENT_MISSING_TUNING",
            f"instruments.{family}.known=true mas tuning e null — se a "
            f"configuracao e conhecida, a afinacao faz parte dela",
            path=path,
            hint=(
                "declare tuning.name (ex.: 'Drop C') e/ou tuning.notes "
                "(MIDI das cordas soltas, grave->agudo)"
            ),
        )
    name = tuning.get("name")
    notes = tuple(tuning.get("notes") or ())
    if not name and not notes:
        raise ToolError(
            "E_BRIEF_INSTRUMENT_TUNING_EMPTY",
            f"instruments.{family}.tuning nao declara nem name nem notes",
            path=path,
            hint="declare tuning.name ou tuning.notes — ou known=false",
        )

    resolved: tuple[int, ...] | None = None
    if name:
        resolved = tuning_mod.resolve_tuning_name(name, strings)
        if resolved is None and not notes:
            raise ToolError(
                "E_BRIEF_TUNING_NAME_UNKNOWN",
                f"instruments.{family}.tuning.name={name!r} nao resolve "
                f"contra o manual guitar.drop_tuning para {strings} corda(s)",
                path=f"{path}.name",
                hint=(
                    "nome desconhecido nao vira chute — declare "
                    "tuning.notes com o MIDI de cada corda solta, "
                    "grave->agudo"
                ),
            )

    if notes:
        if len(notes) != strings:
            raise ToolError(
                "E_BRIEF_INSTRUMENT_STRING_COUNT_MISMATCH",
                f"instruments.{family}.strings={strings} mas "
                f"tuning.notes tem {len(notes)} nota(s)",
                path=f"{path}.notes",
                hint=f"declare exatamente {strings} nota(s), grave->agudo",
            )
        if list(notes) != sorted(notes) or len(set(notes)) != len(notes):
            raise ToolError(
                "E_BRIEF_INSTRUMENT_NOTES_NOT_ORDERED",
                f"instruments.{family}.tuning.notes nao esta em ordem "
                f"estritamente ascendente (grave->agudo): {notes}",
                path=f"{path}.notes",
                hint="ordene do grave para o agudo, sem corda repetida",
            )

    if resolved is not None and notes and resolved != notes:
        raise ToolError(
            "E_BRIEF_TUNING_NAME_MISMATCH",
            f"instruments.{family}.tuning.name={name!r} resolve para "
            f"{resolved}, mas tuning.notes declara {notes} — as duas "
            f"declaracoes discordam",
            path=path,
            hint=(
                "corrija tuning.notes para bater com o nome, ou remova "
                "tuning.name e mantenha so as notas declaradas"
            ),
        )


def _validate_instruments(brief: dict[str, Any]) -> None:
    """Valida `instruments` (issue #44) quando presente. Chave ausente
    continua valida — a ausencia e o que o brief antigo (sem instruments)
    ja fazia, e a issue exige que continue assim."""
    instruments = brief.get("instruments")
    if not instruments:
        return
    for family, entry in instruments.items():
        path = f"instruments.{family}"
        known = entry.get("known")
        strings = entry.get("strings")
        tuning = entry.get("tuning")
        if known is False:
            conflicting = strings is not None or tuning is not None
            if family == "bass":
                conflicting = conflicting or any(
                    entry.get(field) is not None
                    for field in ("playing_style", "notation")
                )
            if conflicting:
                raise ToolError(
                    "E_BRIEF_INSTRUMENT_KNOWN_CONFLICT",
                    f"{path}.known=false mas strings/tuning"
                    + (
                        "/playing_style/notation"
                        if family == "bass"
                        else ""
                    )
                    + " nao sao todos null — 'nao sei' e ausencia "
                    "declarada, nao pode vir acompanhado de fato parcial",
                    path=path,
                    hint=(
                        "known=false so aceita strings=null, tuning=null"
                        + (
                            ", playing_style=null, notation=null"
                            if family == "bass"
                            else ""
                        )
                    ),
                )
            continue
        if strings is None:
            raise ToolError(
                "E_BRIEF_INSTRUMENT_MISSING_STRINGS",
                f"{path}.known=true mas strings e null",
                path=f"{path}.strings",
                hint="declare o numero de cordas, ou known=false",
            )
        _validate_instrument_tuning(family, strings, tuning, f"{path}.tuning")
        if family == "bass":
            for field in ("playing_style", "notation"):
                if entry.get(field) is None:
                    raise ToolError(
                        "E_BRIEF_INSTRUMENT_MISSING_BASS_FIELD",
                        f"{path}.known=true mas {field} e null",
                        path=f"{path}.{field}",
                        hint=(
                            "playing_style: finger|pick|slap; "
                            "notation: written (soa 8vb) | sounding "
                            "(ja soa na altura escrita)"
                        ),
                    )


def validate_brief(brief: Any) -> None:
    """Valida `brief` contra o schema e as regras semanticas.

    Erros mapeiam para codigos estaveis:
    - `E_BRIEF_INVALID`: falha estrutural (schema).
    - `E_BRIEF_MUSICAL_CONTENT`: `style` carrega sequencia de notas.
    - `E_BRIEF_TECHNIQUE_NOT_FOUND`: tecnica citada nao existe no indice.
    - `E_BRIEF_TECHNIQUE_NOT_IMPLEMENTED`: tecnica autorizada existe no
      manual mas nao esta em `SUPPORTED_TECHNIQUES` do motor.
    - `E_TECHNIQUES_INDEX`: falha ao ler `knowledge/tecnicas/`.
    - `E_BRIEF_INSTRUMENT_KNOWN_CONFLICT`: `instruments.<familia>.known
      == false` mas `strings`/`tuning` nao sao null.
    - `E_BRIEF_INSTRUMENT_MISSING_STRINGS` /
      `E_BRIEF_INSTRUMENT_MISSING_TUNING`: `known == true` sem o campo.
    - `E_BRIEF_INSTRUMENT_TUNING_EMPTY`: `tuning` sem `name` nem `notes`.
    - `E_BRIEF_TUNING_NAME_UNKNOWN`: `tuning.name` nao resolve contra o
      manual `guitar.drop_tuning` para o numero de cordas declarado.
    - `E_BRIEF_TUNING_NAME_MISMATCH`: `tuning.name` resolvido e
      `tuning.notes` declarado discordam.
    - `E_BRIEF_INSTRUMENT_STRING_COUNT_MISMATCH`: `len(tuning.notes) !=
      strings`.
    - `E_BRIEF_INSTRUMENT_NOTES_NOT_ORDERED`: `tuning.notes` fora de
      ordem estritamente ascendente (grave->agudo).
    - `E_BRIEF_INSTRUMENT_MISSING_BASS_FIELD`: baixo com `known == true`
      sem `playing_style` ou `notation`.
    """
    try:
        validate_input(brief, brief_schema())
    except SchemaError as exc:
        raise ToolError(
            "E_BRIEF_INVALID",
            exc.message,
            path=exc.path,
            hint=exc.hint,
        ) from None

    style = brief["style"]
    for family, entry in style.items():
        _scan_no_musical_content(entry, f"style.{family}")

    try:
        idx = techniques_mod.build_index()
    except techniques_mod.TechniqueError as exc:
        raise ToolError(
            "E_TECHNIQUES_INDEX",
            f"falha ao construir indice de tecnicas: {exc}",
        ) from None

    for family, entry in style.items():
        _validate_family_techniques(family, entry, idx)

    _validate_instruments(brief)


# --- tool -----------------------------------------------------------------


BRIEF_VALIDATE_DESCRIPTION = (
    "Valida um arrangement-brief.json contra o schema (estrutura) e contra "
    "as regras semanticas invioláveis: (1) `style` NAO pode carregar "
    "conteudo musical — arrays de inteiros na faixa MIDI ou arrays de "
    "strings em formato de nome de nota (C4, F#3) sao rejeitados em "
    "qualquer profundidade dentro de style; (2) toda tecnica declarada em "
    "style.<familia>.techniques[].name precisa existir no indice de "
    "tecnicas (knowledge/tecnicas/); (2b) toda tecnica em "
    "style.<familia>.authorized_techniques[] precisa tambem estar "
    "implementada pelo motor (`SUPPORTED_TECHNIQUES`); tecnica apenas "
    "documentada nao pode ser autorizada, senao o brief compromete uma "
    "execucao que o motor nao consegue entregar; (3) `instruments.<familia>` (guitar, "
    "bass), quando presente, tem numero de cordas coerente com o numero "
    "de notas de tuning, notas em ordem grave->agudo, e tuning.name so "
    "resolve contra o manual guitar.drop_tuning — nome desconhecido exige "
    "tuning.notes explicito. Use ANTES de gravar o brief e ANTES "
    "de invocar o `run` — brief invalido faz o run gastar iteracao a toa."
)


def _brief_validate_impl(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    brief = payload["brief"]
    validate_brief(brief)
    return {"ok": True}, []


BRIEF_VALIDATE_TOOL = Tool(
    name="brief.validate",
    description=BRIEF_VALIDATE_DESCRIPTION,
    input_schema={
        "type": "object",
        "properties": {
            "brief": {"type": "object", "additionalProperties": True},
        },
        "required": ["brief"],
    },
    output_schema={
        "type": "object",
        "properties": {"ok": {"type": "boolean"}},
        "required": ["ok"],
    },
    func=_brief_validate_impl,
)


__all__ = [
    "BRIEF_SCHEMA_VERSION",
    "BRIEF_VALIDATE_DESCRIPTION",
    "BRIEF_VALIDATE_TOOL",
    "CONFIDENCE_LEVELS",
    "REQUISITO_FAMILIES",
    "REQUISITO_TYPES",
    "STYLE_FAMILIES",
    "brief_schema",
    "validate_brief",
]
