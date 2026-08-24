"""Indice de tecnicas derivado dos manuais em `knowledge/tecnicas/` (US-005).

O indice NUNCA e hardcoded: e sempre reconstruido a partir dos manuais em
tempo de carga. Isso garante que acrescentar um manual novo passe a valer
sem tocar em Python.

## Formato dos manuais

Cada tecnica documentada vive num bloco `technique` (fenced code block com
linguagem `technique`) contendo um objeto JSON:

    ```technique
    {
      "name": "ghost_notes",
      "family": "drums",
      "summary": "Nota fantasma de caixa entre backbeats.",
      "verified": true,
      "description": "Como aplicar: ...",
      "parameters": [
        {"name": "velocity", "value": null, "range": [20, 45],
         "source": "Toontrack — how to program drums"}
      ],
      "tools": {
        "generic": {"notes": [38], "velocity": [20, 45]},
        "superior_drummer": {"notes": [38], "velocity": [20, 45]}
      }
    }
    ```

Regras:

- `name`, `family`, `summary`, `verified` sao obrigatorios.
- `parameters`: cada item tem `name` obrigatorio; `value`, `range`, `source`
  opcionais. Numero SEM fonte carrega `verified: false` no proprio objeto ou
  no proprio parametro (via `source: null`).
- `tools`: dict cuja chave e a ferramenta-alvo (`generic`, `superior_drummer`,
  `addictive_drums`, `logic_sampler`, ...). `generic` e o fallback.
- Nome canonico e `<family>.<name>` (ex.: `drums.ghost_notes`).
- Duplicata (mesmo `family.name` em dois manuais) FALHA ALTO.

Manual malformado FALHA ALTO — indice vazio silencioso faria o validador
aceitar qualquer coisa.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Regex do bloco: fenced code com linguagem 'technique'. Aceita
# whitespace antes de `technique` para tolerar indentacao em listas.
_FENCE_RE = re.compile(
    r"^```[ \t]*technique[ \t]*\r?\n(.*?)^```",
    re.MULTILINE | re.DOTALL,
)


class TechniqueError(ValueError):
    """Manual malformado ou tecnica com esquema invalido."""


@dataclass(frozen=True)
class TechniqueParameter:
    """Parametro de uma tecnica."""
    name: str
    value: Any = None
    range: tuple[float, float] | None = None
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"name": self.name, "value": self.value, "source": self.source}
        d["range"] = list(self.range) if self.range is not None else None
        return d


@dataclass(frozen=True)
class Technique:
    """Uma tecnica catalogada do manual.

    - `canonical`: `<family>.<name>` (ex.: `drums.ghost_notes`) — chave estavel.
    - `verified`: True quando toda a receita tem fonte. False quando qualquer
      parametro esta marcado como nao verificado ou o proprio bloco declarou
      `verified: false`.
    - `parameters`: lista ordenada; ordem preservada do manual.
    - `tools`: recipes por ferramenta-alvo. `generic` sempre existe (fallback).
    """
    canonical: str
    name: str
    family: str
    summary: str
    verified: bool
    description: str = ""
    parameters: tuple[TechniqueParameter, ...] = field(default_factory=tuple)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    source_manual: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical": self.canonical,
            "name": self.name,
            "family": self.family,
            "summary": self.summary,
            "verified": self.verified,
            "description": self.description,
            "parameters": [p.to_dict() for p in self.parameters],
            "tools": self.tools,
            "source_manual": self.source_manual,
        }


# --- parser -----------------------------------------------------------------

_REQUIRED = ("name", "family", "summary", "verified")


def _parse_parameter(raw: Any, ctx: str) -> TechniqueParameter:
    if not isinstance(raw, dict):
        raise TechniqueError(f"{ctx}: parametro precisa ser objeto, recebi {type(raw).__name__}")
    if "name" not in raw:
        raise TechniqueError(f"{ctx}: parametro sem `name`")
    name = raw["name"]
    if not isinstance(name, str) or not name.strip():
        raise TechniqueError(f"{ctx}: parametro.name precisa ser string nao vazia")
    rng = raw.get("range")
    if rng is not None:
        if not isinstance(rng, list) or len(rng) != 2:
            raise TechniqueError(f"{ctx}: parametro.range precisa ser [lo, hi], recebi {rng!r}")
        rng = (rng[0], rng[1])
    known = {"name", "value", "range", "source"}
    unknown = set(raw) - known
    if unknown:
        raise TechniqueError(
            f"{ctx}: parametro tem campos desconhecidos: {sorted(unknown)}",
        )
    return TechniqueParameter(
        name=name,
        value=raw.get("value"),
        range=rng,
        source=raw.get("source"),
    )


def _parse_block(block: str, manual: Path, block_index: int) -> Technique:
    ctx = f"{manual.name}#technique[{block_index}]"
    try:
        data = json.loads(block)
    except json.JSONDecodeError as exc:
        raise TechniqueError(
            f"{ctx}: bloco nao e JSON valido: {exc.msg} (linha {exc.lineno}, col {exc.colno})",
        ) from None

    if not isinstance(data, dict):
        raise TechniqueError(f"{ctx}: bloco precisa ser objeto JSON")

    for field_ in _REQUIRED:
        if field_ not in data:
            raise TechniqueError(f"{ctx}: campo obrigatorio ausente: {field_!r}")

    known = {
        "name", "family", "summary", "verified", "description",
        "parameters", "tools",
    }
    unknown = set(data) - known
    if unknown:
        raise TechniqueError(
            f"{ctx}: campos desconhecidos: {sorted(unknown)}. "
            f"Aceitos: {sorted(known)}",
        )

    name = data["name"]
    family = data["family"]
    if not isinstance(name, str) or not name.strip():
        raise TechniqueError(f"{ctx}: `name` precisa ser string nao vazia")
    if not isinstance(family, str) or not family.strip():
        raise TechniqueError(f"{ctx}: `family` precisa ser string nao vazia")
    if not isinstance(data["summary"], str) or not data["summary"].strip():
        raise TechniqueError(f"{ctx}: `summary` precisa ser string nao vazia")
    if not isinstance(data["verified"], bool):
        raise TechniqueError(f"{ctx}: `verified` precisa ser bool")

    parameters = tuple(
        _parse_parameter(p, ctx) for p in data.get("parameters", [])
    )
    tools = data.get("tools", {})
    if not isinstance(tools, dict):
        raise TechniqueError(f"{ctx}: `tools` precisa ser objeto")
    for tool_name, recipe in tools.items():
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise TechniqueError(f"{ctx}: chave em `tools` precisa ser string")
        if not isinstance(recipe, dict):
            raise TechniqueError(
                f"{ctx}: tools[{tool_name!r}] precisa ser objeto, recebi {type(recipe).__name__}",
            )

    description = data.get("description", "")
    if not isinstance(description, str):
        raise TechniqueError(f"{ctx}: `description` precisa ser string")

    # verified do bloco vale, e derrubado por qualquer parametro sem source.
    verified = bool(data["verified"])
    if verified and any(p.source is None and p.value is None and p.range is None
                        for p in parameters):
        # Parametro totalmente vazio nao tira o verified — nao ha numero para verificar.
        pass
    if verified and any(
        p.source is None and (p.value is not None or p.range is not None)
        for p in parameters
    ):
        verified = False

    return Technique(
        canonical=f"{family}.{name}",
        name=name,
        family=family,
        summary=data["summary"],
        verified=verified,
        description=description,
        parameters=parameters,
        tools=tools,
        source_manual=manual.name,
    )


def parse_manual(manual_path: str | Path) -> list[Technique]:
    """Extrai as tecnicas de um manual `.md`.

    Manual sem nenhum bloco `technique` FALHA — indice vazio nao pode passar
    despercebido, senao o validador aceita qualquer nome.
    """
    path = Path(manual_path)
    if not path.exists():
        raise TechniqueError(f"manual nao encontrado: {path}")
    text = path.read_text(encoding="utf-8")
    matches = list(_FENCE_RE.finditer(text))
    if not matches:
        raise TechniqueError(
            f"{path.name}: nenhum bloco ```technique encontrado. "
            f"Manual sem tecnica declarada faria o validador aceitar qualquer nome — "
            f"declare pelo menos uma tecnica ou remova o arquivo.",
        )
    techniques: list[Technique] = []
    for i, m in enumerate(matches):
        techniques.append(_parse_block(m.group(1), path, i))
    return techniques


# --- indice -----------------------------------------------------------------

DEFAULT_MANUALS_DIR = Path(__file__).resolve().parent.parent.parent / "knowledge" / "tecnicas"


@dataclass(frozen=True)
class TechniqueIndex:
    """Colecao de tecnicas indexadas por `canonical`."""
    techniques: tuple[Technique, ...]

    def by_family(self, family: str | None = None) -> tuple[Technique, ...]:
        if family is None:
            return self.techniques
        return tuple(t for t in self.techniques if t.family == family)

    def candidates(self, canonical_or_name: str) -> tuple[Technique, ...]:
        """Todas as tecnicas que casam com `canonical` exato ou com o nome cru.

        Nome cru pode casar em mais de uma familia: `ghost_notes` existe em
        bateria e em baixo. Quem chama precisa desambiguar.
        """
        exact = tuple(t for t in self.techniques if t.canonical == canonical_or_name)
        if exact:
            return exact
        return tuple(t for t in self.techniques if t.name == canonical_or_name)

    def get(self, canonical_or_name: str) -> Technique | None:
        """Devolve a tecnica quando a resolucao e inequivoca, senao `None`.

        Nome cru que casa em mais de uma familia devolve `None` DE PROPOSITO:
        escolher uma em silencio entregaria a receita de baixo para quem pediu
        bateria, e o MIDI sairia errado sem nenhum erro no caminho. Use
        `candidates()` e desambigue por familia ou por ferramenta-alvo.
        """
        found = self.candidates(canonical_or_name)
        return found[0] if len(found) == 1 else None

    def names(self) -> tuple[str, ...]:
        return tuple(t.canonical for t in self.techniques)


def build_index(manuals_dir: str | Path | None = None) -> TechniqueIndex:
    """Percorre `manuals_dir/*.md` e agrupa todas as tecnicas.

    Duplicata (`family.name` colidindo em dois manuais) FALHA ALTO. Diretorio
    inexistente FALHA — indice vazio derrubaria a validacao do plano em
    silencio, comportando um regresso invisivel.
    """
    d = Path(manuals_dir) if manuals_dir is not None else DEFAULT_MANUALS_DIR
    if not d.exists() or not d.is_dir():
        raise TechniqueError(f"diretorio de manuais nao existe: {d}")

    all_techniques: list[Technique] = []
    seen: dict[str, str] = {}
    for manual in sorted(d.glob("*.md")):
        for t in parse_manual(manual):
            if t.canonical in seen:
                raise TechniqueError(
                    f"tecnica duplicada {t.canonical!r}: aparece em "
                    f"{seen[t.canonical]} e {manual.name}",
                )
            seen[t.canonical] = manual.name
            all_techniques.append(t)

    if not all_techniques:
        raise TechniqueError(
            f"nenhuma tecnica encontrada em {d}. "
            f"Verifique se os manuais tem blocos ```technique.",
        )

    return TechniqueIndex(techniques=tuple(all_techniques))


__all__ = [
    "DEFAULT_MANUALS_DIR",
    "Technique",
    "TechniqueError",
    "TechniqueIndex",
    "TechniqueParameter",
    "build_index",
    "parse_manual",
]
