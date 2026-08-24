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
from .plan import ROUTES
from .registry import SchemaError, Tool, ToolError, validate_input
from .style_schema import NOTE_NAME_RE as _NOTE_NAME_RE
from .style_schema import style_technique_schema

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


def validate_brief(brief: Any) -> None:
    """Valida `brief` contra o schema e as regras semanticas.

    Erros mapeiam para codigos estaveis:
    - `E_BRIEF_INVALID`: falha estrutural (schema).
    - `E_BRIEF_MUSICAL_CONTENT`: `style` carrega sequencia de notas.
    - `E_BRIEF_TECHNIQUE_NOT_FOUND`: tecnica citada nao existe no indice.
    - `E_TECHNIQUES_INDEX`: falha ao ler `knowledge/tecnicas/`.
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
        for i, tech in enumerate(entry.get("techniques", [])):
            name = tech["name"]
            # A familia ja esta no caminho (`style.drums.techniques[...]`), entao
            # nome cru aqui NAO e ambiguo: `ghost_notes` sob `style.drums` so pode
            # ser o de bateria. Resolve pela familia antes de cair no erro.
            resolved = idx.get(name) or next(
                (t for t in idx.candidates(name) if t.family == family), None
            )
            if resolved is None:
                candidates = list(idx.names()) + [t.name for t in idx.techniques]
                matches = difflib.get_close_matches(name, candidates, n=5, cutoff=0.4)
                raise ToolError(
                    "E_BRIEF_TECHNIQUE_NOT_FOUND",
                    f"tecnica {name!r} declarada em style.{family} "
                    f"nao existe no indice",
                    path=f"style.{family}.techniques[{i}].name",
                    hint=(
                        f"tecnicas parecidas: {matches}"
                        if matches
                        else f"tecnicas disponiveis: {list(idx.names())}"
                    ),
                )


# --- tool -----------------------------------------------------------------


BRIEF_VALIDATE_DESCRIPTION = (
    "Valida um arrangement-brief.json contra o schema (estrutura) e contra "
    "as duas regras semanticas invioláveis: (1) `style` NAO pode carregar "
    "conteudo musical — arrays de inteiros na faixa MIDI ou arrays de "
    "strings em formato de nome de nota (C4, F#3) sao rejeitados em "
    "qualquer profundidade dentro de style; (2) toda tecnica declarada em "
    "style.<familia>.techniques[].name precisa existir no indice de "
    "tecnicas (knowledge/tecnicas/). Use ANTES de gravar o brief e ANTES "
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
