"""`influence.compile` — dicionario deterministico de tracos -> tecnicas (issue #73).

## Onde isto encaixa

`tools/influence.py` define o formato do que a pesquisa (IA do usuario) traz
de volta: `InfluenceProfile`, uma lista de `InfluenceFinding` sobre
COMPORTAMENTO (nunca conteudo musical), por familia e por dimensao.

Este modulo faz o passo seguinte, TAMBEM deterministico: le esses achados e
sugere, para cada um que reconhece, qual `style.<familia>.techniques[].name`
do motor (`tools.techniques.engine.SUPPORTED_TECHNIQUES`) reproduz aquele
comportamento — com intensidade, parametros e `rationale`. Achado que a
pesquisa levantou mas o motor ainda nao sabe executar sai em
`unmapped_findings`, nunca descartado e nunca virando sugestao de mentirinha.

## Regras invioláveis deste modulo

- Sem LLM, sem rede, sem relogio, sem `random` sem seed — na pratica, sem
  `random` nenhum: o mapeamento e uma tabela fixa, resolvida por
  correspondencia de texto determinística. Mesma entrada + mesma versao do
  dicionario (`INFLUENCE_MAPPING_VERSION`) = saida byte-identica.
- Só emite `name` presente em `SUPPORTED_TECHNIQUES` — garantido em tempo de
  import por `_assert_rules_reference_supported_techniques()`, nao so por
  convencao de quem escreve a regra nova.
- Todo `MappingSuggestion` carrega `finding_ids`, `name` (tecnica canonica),
  `family`, `intensity`, `rationale` e `mapping_version` — nunca sugestao
  orfa de evidencia.
- Achado que nao bate com nenhuma regra (ou que a pesquisa ja marcou como
  `unmapped_findings` na origem) sai em `unmapped_findings` da saida — o
  compilador NUNCA descarta achado nem inventa tecnica generica para
  "resolver" o que nao mapeia.
- Achado com `intensity == "off"` que bate com uma regra NAO vira sugestao
  (a referencia explicitamente nao usa aquele comportamento), mas tambem nao
  e descartado: sai em `not_recommended`, com a tecnica que bateria e o
  motivo, para auditoria.
- O compilador NUNCA aplica tecnica nenhuma — so traduz achado em candidato.
  A autorizacao (`style.<familia>.authorized_techniques[]`, ver
  `tools/brief_schema.py`) e decisao humana, feita depois, em outra camada.
- Quando `target_tools[family]` aponta uma ferramenta sem receita especifica
  para a tecnica sugerida, a sugestao cai para a receita `generic` do manual
  e a saida carrega warning `W_NO_TOOL_RECIPE` — mesmo codigo que
  `tools/techniques/engine.py` ja usa no despacho de verdade, para nao criar
  um segundo vocabulario de warning para a mesma situacao.
- Intensidade semantica (`off|subtle|medium|strong`, vocabulario fechado de
  `tools.influence.INFLUENCE_INTENSITIES`) e traduzida para o float 0.0-1.0
  de `StyleTechnique.intensity` (`tools/plan.py`) por uma tabela CONVENCAO
  fixa (`_INTENSITY_TO_FLOAT`) — a MESMA escala que `StyleTechnique.intensity`
  ja documenta (0.0 desliga, 1.0 e a faixa cheia); nenhum outro numero
  (velocity, gate, ms) e inventado aqui. Numero de execucao continua vindo
  do manual da tecnica no momento do render, nunca deste modulo.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .influence import InfluenceFinding, InfluenceProfile
from .style_schema import STYLE_TECHNIQUE_STYLE_VALUES
from .techniques.engine import SUPPORTED_TECHNIQUES
from .techniques.index import Technique, TechniqueIndex, build_index

# --- versao ------------------------------------------------------------

INFLUENCE_MAPPING_VERSION = "1.0.0"
"""Muda quando `MAPPING_RULES` muda de forma observavel (regra nova, regra
removida, palavra-chave alterada, intensidade CONVENCAO recalibrada). E o
que permite auditar "essa sugestao veio de qual versao do dicionario"."""

# --- intensidade semantica -> float da StyleTechnique -------------------

# CONVENCAO — reaproveita a escala 0.0-1.0 ja documentada em
# `tools.plan.StyleTechnique.intensity` (issue #72: 0.0 desliga, 1.0 e a
# faixa cheia). Os tres pontos intermediarios sao um passo linear simples
# (0.25/0.55/0.85) sem pretensao de medir nada alem de "pouco/medio/muito";
# nao vem de manual nenhum porque nao ha manual que quantifique adjetivo de
# pesquisa em numero de motor — e exatamente esse de-para que este modulo
# existe para fixar em UM lugar so, em vez de cada modelo inventar o dele.
_INTENSITY_TO_FLOAT: dict[str, float] = {
    "subtle": 0.25,
    "medium": 0.55,
    "strong": 0.85,
}


def _normalize(text: str) -> str:
    """minusculo, sem acento, espacos colapsados — para casar palavra-chave
    independente de acentuacao/caixa que a pesquisa venha a usar."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", stripped.lower()).strip()


# --- regra de mapeamento -------------------------------------------------


@dataclass(frozen=True)
class MappingRule:
    """Uma linha do dicionario traco semantico -> tecnica.

    - `keywords`: qualquer uma bate (substring, apos `_normalize`) em
      `semantic_value` OU `summary` do achado — e o suficiente para
      disparar a regra.
    - `dimensions`: em quais `INFLUENCE_DIMENSIONS` esta regra pode disparar
      (achado fora dessas dimensoes nao casa, mesmo com a palavra-chave
      presente — dimensao errada e sinal de leitura errada do achado).
    - `style_value`: quando a tecnica tem campo `style` fechado
      (`STYLE_TECHNIQUE_STYLE_VALUES`, ex.: `bass.attack_style`), o valor
      fixo que esta regra resolve. `None` para tecnica sem esse campo.
    - `rationale_template`: `str.format(finding=..., technique=...)`.
    """

    id: str
    family: str
    technique: str
    dimensions: tuple[str, ...]
    keywords: tuple[str, ...]
    rationale_template: str
    style_value: str | None = None
    parameters: dict[str, float | list[float]] = field(default_factory=dict)

    def matches(self, finding: InfluenceFinding) -> bool:
        if finding.family != self.family:
            return False
        if finding.dimension not in self.dimensions:
            return False
        haystack = _normalize(f"{finding.semantic_value} {finding.summary}")
        return any(_normalize(kw) in haystack for kw in self.keywords)


# --- o dicionario --------------------------------------------------------
#
# Ordem e a ordem de desempate: para um achado que bata mais de uma regra,
# a PRIMEIRA da tupla vence — deterministico e auditavel sem precisar de
# heuristica de "melhor match".

MAPPING_RULES: tuple[MappingRule, ...] = (
    # --- drums ------------------------------------------------------
    MappingRule(
        id="drums_timing_feel_microtiming",
        family="drums",
        technique="drums.microtiming",
        dimensions=("timing_feel",),
        keywords=(
            "laid back", "laid-back", "laidback", "atrasa", "atrasada",
            "atrasado", "atras da batida", "atras do click", "empurra",
            "puxa pra tras", "na frente da batida", "adiantada", "adiantado",
            "rushed", "ansiosa", "ansioso",
        ),
        rationale_template=(
            "Achado {finding_ids} descreve sensacao de tempo ({dimension}) "
            "em drums — drums.microtiming reproduz atraso/adiantamento de "
            "grade sem mudar pitch nem contagem de nota."
        ),
    ),
    MappingRule(
        id="drums_articulation_ghost_notes",
        family="drums",
        technique="drums.ghost_notes",
        dimensions=("articulation", "density"),
        keywords=(
            "ghost note", "ghost notes", "nota fantasma", "notas fantasmas",
            "esparsa", "esparsas", "esparso",
        ),
        rationale_template=(
            "Achado {finding_ids} descreve articulacao esparsa de baixa "
            "pressao em drums — drums.ghost_notes e a tecnica registrada "
            "para isso no motor."
        ),
    ),
    MappingRule(
        id="drums_articulation_flam",
        family="drums",
        technique="drums.flam",
        dimensions=("articulation",),
        keywords=("flam", "nota de apoio", "grace note curta"),
        rationale_template=(
            "Achado {finding_ids} descreve ornamento de apoio antes do "
            "golpe principal em drums — drums.flam."
        ),
    ),
    MappingRule(
        id="drums_articulation_buzz_roll",
        family="drums",
        technique="drums.buzz_roll",
        dimensions=("articulation",),
        keywords=("buzz roll", "rufo", "drag"),
        rationale_template=(
            "Achado {finding_ids} descreve rufo/buzz roll em drums — "
            "drums.buzz_roll."
        ),
    ),
    MappingRule(
        id="drums_articulation_cymbal_choke",
        family="drums",
        technique="drums.cymbal_choke",
        dimensions=("articulation",),
        keywords=("choke", "abafa o prato", "cymbal choke"),
        rationale_template=(
            "Achado {finding_ids} descreve prato abafado logo apos o "
            "ataque em drums — drums.cymbal_choke."
        ),
    ),
    MappingRule(
        id="drums_dynamics_accent_hierarchy",
        family="drums",
        technique="drums.accent_hierarchy",
        dimensions=("dynamics",),
        keywords=(
            "hierarquia de acento", "hierarquia de dinamica",
            "acentua fortemente", "camadas de pressao",
        ),
        rationale_template=(
            "Achado {finding_ids} descreve camadas de acento/dinamica em "
            "drums — drums.accent_hierarchy."
        ),
    ),
    MappingRule(
        id="drums_section_behavior_accented_roll",
        family="drums",
        technique="drums.accented_roll",
        dimensions=("section_behavior",),
        keywords=(
            "virada elaborada", "fill mais denso", "rufo na virada",
            "roll na virada",
        ),
        rationale_template=(
            "Achado {finding_ids} descreve comportamento de virada mais "
            "denso em drums — drums.accented_roll."
        ),
    ),
    # --- bass ---------------------------------------------------------
    MappingRule(
        id="bass_execution_attack_pick",
        family="bass",
        technique="bass.attack_style",
        dimensions=("execution_technique", "articulation"),
        keywords=("palheta", "picked", "ataque de palheta", "attack de palheta"),
        rationale_template=(
            "Achado {finding_ids} descreve ataque de baixo com palheta "
            "({dimension}) — bass.attack_style com style=palheta."
        ),
        style_value="palheta",
    ),
    MappingRule(
        id="bass_execution_attack_finger",
        family="bass",
        technique="bass.attack_style",
        dimensions=("execution_technique", "articulation"),
        keywords=("dedo", "fingered", "dedilhado"),
        rationale_template=(
            "Achado {finding_ids} descreve ataque de baixo com dedo "
            "({dimension}) — bass.attack_style com style=dedo."
        ),
        style_value="dedo",
    ),
    MappingRule(
        id="bass_execution_attack_slap",
        family="bass",
        technique="bass.attack_style",
        dimensions=("execution_technique", "articulation"),
        keywords=("slap",),
        rationale_template=(
            "Achado {finding_ids} descreve ataque de baixo em slap "
            "({dimension}) — bass.attack_style com style=slap."
        ),
        style_value="slap",
    ),
    MappingRule(
        id="bass_articulation_ghost_notes",
        family="bass",
        technique="bass.ghost_notes",
        dimensions=("articulation", "density"),
        keywords=(
            "ghost note", "ghost notes", "nota fantasma", "notas fantasmas",
            "esparsa", "esparsas", "esparso",
        ),
        rationale_template=(
            "Achado {finding_ids} descreve articulacao esparsa de baixa "
            "pressao em bass — bass.ghost_notes."
        ),
    ),
    MappingRule(
        id="bass_articulation_palm_mute",
        family="bass",
        technique="bass.palm_mute",
        dimensions=("articulation",),
        keywords=("palm mute", "abafado com a mao", "mutado com a mao"),
        rationale_template=(
            "Achado {finding_ids} descreve nota abafada com a mao em bass "
            "— bass.palm_mute."
        ),
    ),
    MappingRule(
        id="bass_articulation_let_ring",
        family="bass",
        technique="bass.let_ring",
        dimensions=("articulation",),
        keywords=("let ring", "deixa soar", "deixa ressoar"),
        rationale_template=(
            "Achado {finding_ids} descreve nota deixada soar/ressoar em "
            "bass — bass.let_ring."
        ),
    ),
    MappingRule(
        id="bass_articulation_hammer_pull",
        family="bass",
        technique="bass.hammer_pull",
        dimensions=("articulation",),
        keywords=("hammer-on", "hammer on", "pull-off", "pull off", "legato de baixo"),
        rationale_template=(
            "Achado {finding_ids} descreve legato de hammer-on/pull-off em "
            "bass — bass.hammer_pull."
        ),
    ),
    MappingRule(
        id="bass_register_string_selection",
        family="bass",
        technique="bass.string_selection",
        dimensions=("register",),
        keywords=("corda especifica", "corda grave", "escolha de corda", "string selection"),
        rationale_template=(
            "Achado {finding_ids} descreve preferencia de registro/corda "
            "em bass — bass.string_selection."
        ),
    ),
    MappingRule(
        id="bass_dynamics_velocity_contour",
        family="bass",
        technique="bass.velocity_contour",
        dimensions=("dynamics",),
        keywords=("contorno de dinamica", "dinamica em arco", "crescendo na frase"),
        rationale_template=(
            "Achado {finding_ids} descreve contorno de dinamica ao longo "
            "da frase em bass — bass.velocity_contour."
        ),
    ),
    # --- keys -----------------------------------------------------------
    MappingRule(
        id="keys_articulation_damper_pedal",
        family="keys",
        technique="keys.damper_pedal",
        dimensions=("articulation",),
        keywords=("pedal de sustain", "damper pedal", "segura o pedal"),
        rationale_template=(
            "Achado {finding_ids} descreve uso de pedal de sustain em keys "
            "— keys.damper_pedal."
        ),
    ),
    MappingRule(
        id="keys_dynamics_expression",
        family="keys",
        technique="keys.expression",
        dimensions=("dynamics",),
        keywords=("expression pedal", "cc11", "expressao continua"),
        rationale_template=(
            "Achado {finding_ids} descreve dinamica continua via CC11 em "
            "keys — keys.expression."
        ),
    ),
    MappingRule(
        id="keys_articulation_modulation",
        family="keys",
        technique="keys.modulation",
        dimensions=("articulation",),
        keywords=("modulation wheel", "cc1", "roda de modulacao"),
        rationale_template=(
            "Achado {finding_ids} descreve modulacao via CC1 em keys — "
            "keys.modulation."
        ),
    ),
    MappingRule(
        id="keys_articulation_pitch_bend",
        family="keys",
        technique="keys.pitch_bend",
        dimensions=("articulation",),
        keywords=("pitch bend", "bend suave", "curva de pitch"),
        rationale_template=(
            "Achado {finding_ids} descreve curva de pitch bend em keys — "
            "keys.pitch_bend."
        ),
    ),
)


def _assert_rules_reference_supported_techniques() -> None:
    """FALHA ALTO no import se alguma regra apontar tecnica que o motor nao
    sabe aplicar — nunca sugestao para tecnica fantasma."""
    supported = set(SUPPORTED_TECHNIQUES)
    bad = sorted(
        {rule.id: rule.technique for rule in MAPPING_RULES if rule.technique not in supported}.items()
    )
    if bad:
        raise AssertionError(
            f"MAPPING_RULES aponta tecnica fora de SUPPORTED_TECHNIQUES: {bad!r}"
        )
    for rule in MAPPING_RULES:
        if rule.style_value is not None and rule.style_value not in STYLE_TECHNIQUE_STYLE_VALUES:
            raise AssertionError(
                f"MAPPING_RULES[{rule.id!r}].style_value {rule.style_value!r} "
                f"fora de STYLE_TECHNIQUE_STYLE_VALUES"
            )


_assert_rules_reference_supported_techniques()


# --- resultado -------------------------------------------------------------


@dataclass(frozen=True)
class MappingSuggestion:
    family: str
    name: str
    finding_ids: tuple[str, ...]
    intensity: float
    rationale: str
    mapping_version: str
    parameters: dict[str, float | list[float]] = field(default_factory=dict)
    style: str | None = None
    tool: str = "generic"
    requested_tool: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "name": self.name,
            "finding_ids": list(self.finding_ids),
            "intensity": self.intensity,
            "rationale": self.rationale,
            "mapping_version": self.mapping_version,
            "parameters": dict(self.parameters),
            "style": self.style,
            "tool": self.tool,
            "requested_tool": self.requested_tool,
        }


@dataclass(frozen=True)
class NotRecommended:
    """Achado que bateu regra mas tem `intensity == "off"` — a referencia
    explicitamente NAO usa esse comportamento. Auditavel, nunca descartado,
    nunca virou sugestao."""

    family: str
    finding_id: str
    technique: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "finding_id": self.finding_id,
            "technique": self.technique,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CompileResult:
    mapping_version: str
    suggestions: tuple[MappingSuggestion, ...]
    unmapped_findings: tuple[InfluenceFinding, ...]
    not_recommended: tuple[NotRecommended, ...]
    warnings: tuple[dict[str, Any], ...]

    def to_dict(self, finding_to_dict) -> dict[str, Any]:
        return {
            "mapping_version": self.mapping_version,
            "suggestions": [s.to_dict() for s in self.suggestions],
            "unmapped_findings": [finding_to_dict(f) for f in self.unmapped_findings],
            "not_recommended": [n.to_dict() for n in self.not_recommended],
        }


def _resolve_tool(
    technique: Technique,
    requested_tool: str | None,
    rule_id: str,
) -> tuple[str, tuple[dict[str, Any], ...]]:
    """Mesma logica de fallback de `tools.techniques.engine._recipe_for_tool`,
    reimplementada aqui (nao importada) porque aquela e privada ao modulo do
    motor — este modulo so PRECISA saber se ha receita especifica, nao
    aplicar nada. Devolve `(tool_usado, warnings)`."""
    if not requested_tool or requested_tool == "generic":
        return "generic", ()
    if requested_tool in technique.tools:
        return requested_tool, ()
    if "generic" in technique.tools:
        return "generic", ({
            "code": "W_NO_TOOL_RECIPE",
            "message": (
                f"tecnica {technique.canonical!r} nao tem receita para "
                f"tool={requested_tool!r}; usando fallback generico "
                f"(regra {rule_id!r}). Disponiveis: "
                f"{sorted(technique.tools.keys())!r}"
            ),
            "path": f"target_tools.{technique.family}",
        },)
    # Nem a ferramenta pedida nem `generic` tem receita — a propria tecnica
    # esta mal catalogada no manual; isso e erro de dado do manual, nao algo
    # que este modulo deveria silenciar.
    raise AssertionError(
        f"tecnica {technique.canonical!r} nao tem receita 'generic' no "
        f"manual — indice malformado"
    )


def compile_influence(
    profile: InfluenceProfile,
    *,
    target_tools: dict[str, str] | None = None,
    index: TechniqueIndex | None = None,
) -> CompileResult:
    """Traduz `profile.findings` em sugestoes de tecnica canonica.

    `target_tools`: `{familia: ferramenta}` — instrumento/plugin-alvo por
    familia (issue #73: "instrumento alvo"). Ausente ou `"generic"` usa a
    receita generica direto, sem tentar nada mais especifico.
    `index`: injeta um `TechniqueIndex` (teste); default reconstroi do
    manual via `build_index()`.
    """
    idx = index if index is not None else build_index()
    tools_by_family = dict(target_tools or {})

    suggestions: list[MappingSuggestion] = []
    not_recommended: list[NotRecommended] = []
    unmapped: list[InfluenceFinding] = list(profile.unmapped_findings)
    warnings: list[dict[str, Any]] = []

    for finding in profile.findings:
        rule = next((r for r in MAPPING_RULES if r.matches(finding)), None)
        if rule is None:
            unmapped.append(finding)
            continue

        if finding.intensity == "off":
            not_recommended.append(
                NotRecommended(
                    family=finding.family,
                    finding_id=finding.id,
                    technique=rule.technique,
                    reason=(
                        f"achado {finding.id!r} declara intensity=off para "
                        f"{rule.technique!r} — referencia nao usa este "
                        f"comportamento, nenhuma sugestao gerada"
                    ),
                )
            )
            continue

        technique = idx.get(rule.technique)
        if technique is None:
            # Regra aponta tecnica fora do indice de manuais (nao so fora do
            # motor) — sinal de manual/registro dessincronizados. Falha alto
            # em vez de fingir que nao encontrou nada.
            raise AssertionError(
                f"regra {rule.id!r} aponta {rule.technique!r}, ausente do "
                f"indice de manuais (build_index())"
            )

        requested_tool = tools_by_family.get(finding.family)
        resolved_tool, tool_warnings = _resolve_tool(technique, requested_tool, rule.id)
        warnings.extend(tool_warnings)

        suggestions.append(
            MappingSuggestion(
                family=finding.family,
                name=rule.technique,
                finding_ids=(finding.id,),
                intensity=_INTENSITY_TO_FLOAT[finding.intensity],
                rationale=rule.rationale_template.format(
                    finding_ids=finding.id,
                    dimension=finding.dimension,
                ),
                mapping_version=INFLUENCE_MAPPING_VERSION,
                parameters=dict(rule.parameters),
                style=rule.style_value,
                tool=resolved_tool,
                requested_tool=requested_tool,
            )
        )

    return CompileResult(
        mapping_version=INFLUENCE_MAPPING_VERSION,
        suggestions=tuple(suggestions),
        unmapped_findings=tuple(unmapped),
        not_recommended=tuple(not_recommended),
        warnings=tuple(warnings),
    )


__all__ = [
    "INFLUENCE_MAPPING_VERSION",
    "MAPPING_RULES",
    "CompileResult",
    "MappingRule",
    "MappingSuggestion",
    "NotRecommended",
    "compile_influence",
]
