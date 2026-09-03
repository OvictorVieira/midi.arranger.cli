"""Schema e validador do arrangement-plan.json (US-009).

Fonte: secao 5 de tasks/midi-arranger-spec.md.

O plano e a fonte editavel da rodada: quem escreve o plano decide como o
MIDI vai soar. O renderer nao improvisa. Este modulo:

- Define os dataclasses do schema.
- Valida vocabularios fechados, invariantes de registro/layers/section
  reference e presenca de campos obrigatorios.
- Serializa (`dump`) e desserializa (`load`) com round-trip identidade.

Mensagens de erro carregam o caminho exato do campo invalido (ex.:
`elements[3].register[1]`) para que o usuario que edita o JSON a mao
consiga achar o campo sem ler o traceback inteiro.
"""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .constants import SYNC_ROLES
from .style_schema import (
    ISO_DATE_RE,
    MIDI_PITCH_MAX,
    MIDI_PITCH_MIN,
    STYLE_TECHNIQUE_STYLE_VALUES,
    find_style_musical_content,
    is_style_parameter_pair,
)

# --- vocabularios fechados (secao 5 do spec) --------------------------------

ROUTES = (
    "cinematica_emocional",
    "organica_inquietante",
    "hook_eletronico_pesado",
)

SECTION_KINDS = (
    "intro", "verse", "pre", "chorus",
    "breakdown", "bridge", "interlude", "outro",
)

SECTION_SOURCES = ("marker", "inferred")

PROTAGONISTS = (
    "vocal_hook", "guitar_riff", "electronic_riff",
    "drum_groove", "piano_motif", "texture",
)

ARTICULATIONS = ("ghost", "staccato", "tight", "open", "sustained", "let_ring")

HARMONY_MODES = ("follow_chords", "pedal", "free", "unison_guitar", "percussion")

EDIT_PROFILES = ("bass", "drums", "keys", "generic")
EDIT_INTENSITY_MIN = 0.0
EDIT_INTENSITY_MAX = 1.0

STYLE_FAMILIES = ("bass", "drums", "guitar", "keys")
STYLE_CONFIDENCE_LEVELS = ("high", "medium", "low", "default")

# --- issue #96 — sessao de trabalho ---------------------------------------
#
# O plano herda o bloco `session` do brief: mesmo vocabulario fechado de
# `intent`, mesmas famílias em `families_in_scope`. O plano nao inventa
# nem sobrescreve — carrega o snapshot para que o render/harness saibam
# em que rodada de trabalho o plano vive.
#
# Regra de coerencia do plano: quando `session.families_in_scope` esta
# declarado, nenhum elemento gerado (`plan.elements`) de familia fora do
# escopo entra, e nenhum `plan.style.<outra-familia>.techniques[]` pode
# aparecer. Tracks copiadas do MIDI em `plan.edits` sao livres porque
# saem byte-identicas quando nao recebem tecnica.
SESSION_INTENTS = ("edit", "create", "layer", "transition", "mixed")
SESSION_FIELDS = ("id", "intent", "families_in_scope", "created_at")
STYLE_FAMILY_REQUIRED_FIELDS = (
    "reference",
    "researched_at",
    "sources",
    "confidence",
    "techniques",
    "parameters",
)
STYLE_TECHNIQUE_FIELDS = ("name", "density", "rationale", "style")
DEFAULT_STYLE_REFERENCE = "persona base"
DEFAULT_STYLE_RESEARCHED_AT = "0001-01-01"
DEFAULT_STYLE_SOURCE = "knowledge/persona/persona_produtor_metal_moderno.md"
DEFAULT_STYLE_ASSUMPTION_TEMPLATE = (
    "Familia {family} sem style pesquisado; usando persona base como default."
)
ROLE_STYLE_FAMILIES = {
    "bass": "bass",
    "sub": "bass",
    "sub_drop": "bass",
    "growl_bass": "bass",
    "drums": "drums",
    "drum_groove": "drums",
    "hat_elec": "drums",
    "perc_elec": "drums",
    "impact": "drums",
    "snare_bomb": "drums",
    "guitar": "guitar",
    "rhythm_guitar": "guitar",
    "lead_guitar": "guitar",
    "shadow": "guitar",
    "pad": "keys",
    "piano": "keys",
    "rhodes": "keys",
    "strings": "keys",
    "choir": "keys",
    "drone": "keys",
    "arp": "keys",
    "arp_gated": "keys",
    "rhythmic_machine": "keys",
    "motor": "keys",
    "pluck": "keys",
    "riser": "keys",
    "lead_agressivo": "keys",
    "vox_chop": "keys",
}

ENERGY_AXES = ("densidade", "impacto", "largura", "altura", "instabilidade")
ENERGY_MIN = 0
ENERGY_MAX = 10

SCHEMA_VERSION = 1

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


# --- excecao ----------------------------------------------------------------

class PlanValidationError(ValueError):
    """Levantada quando o plano falha validacao.

    Atributo `path` carrega o JSON pointer exato do campo invalido (ex.
    `elements[3].register[1]`). Mensagem final combina path e razao.
    """

    def __init__(self, path: str, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


# --- dataclasses ------------------------------------------------------------

@dataclass
class SourceMidi:
    path: str
    sha256: str
    tempo: float | None = None
    key: str | None = None
    bars: int | None = None


@dataclass
class BriefRef:
    path: str
    sha256: str


@dataclass
class PlanSection:
    label: str
    kind: str
    start_bar: int
    end_bar: int
    source: str
    protagonist: str | None = None
    energy: dict[str, int] | None = None


@dataclass
class SourceAnnotation:
    """Anotacao textual do MIDI de origem que motivou este elemento (issue #32).

    Campos espelham `tools.analyze.Annotation` no que interessa para auditoria:
    o texto exato como o usuario escreveu e a posicao (tick/bar/track/tipo).
    Elemento que carrega `source_annotation` DEVE citar o texto no `rationale`
    — a validacao exige, para que a rastreabilidade da autoria seja auditavel.
    """
    text: str
    tick: int
    bar: int
    track: str
    event_type: str


@dataclass
class PlanAnnotation:
    """Anotacao declarada no plano com seu status de execucao (issue #32).

    Toda anotacao lida do MIDI de origem deve aparecer aqui — inclusive as
    que a IA leu e decidiu NAO acionar. Status:

    - `actioned`: virou elemento; `element_id` aponta para ele.
    - `declined`: a IA leu e decidiu nao acionar; `reason` diz por que.
    - `conflict`: a anotacao entra em conflito com uma restricao do brief
      (veto de familia/instrumento); nao foi executada e o `reason` nomeia
      os dois lados (o que a anotacao pediu e o que o brief veta).

    Anotacao com `status=actioned` precisa apontar um elemento existente; com
    `status` diferente, `element_id` fica None e `reason` e obrigatorio.
    """
    text: str
    tick: int
    bar: int
    track: str
    event_type: str
    status: str
    element_id: str | None = None
    reason: str | None = None


ANNOTATION_STATUSES = ("actioned", "declined", "conflict")


@dataclass
class Element:
    id: str
    role: str
    sections: list[str]
    register: list[int]
    layers: int
    sync_role: str
    articulation: str
    harmony: str
    pattern: dict[str, Any] | None = None
    degrees: list[int] | None = None
    dynamics: dict[str, Any] | None = None
    instrument: dict[str, Any] | None = None
    rationale: str | None = None
    is_protagonist: bool = False
    source_annotation: SourceAnnotation | None = None


@dataclass
class Transition:
    at_bar: int
    from_section: str
    to_section: str
    dimensions_changed: list[str]
    elements: list[str]
    technique: str


@dataclass
class PlanEdit:
    """Humanizacao opt-in de uma track existente do MIDI de origem (FR-28).

    - `track`: nome exato da track no MIDI de origem (meta track_name).
      Quando o nome aparece em varias tracks, a edit atinge todas elas.
    - `profile`: um de EDIT_PROFILES; determina os ranges dos motores.
    - `intensity`: 0.0 (intocado) ate 1.0 (ranges cheios) — escala a
      amplitude aplicada pelos motores.
    - `suggested_instrument`: sugestao de plugin/preset para a track existente,
      metadado puro (nao altera nota nenhuma). Chaves aceitas:
      `plugin` (str nao vazio), `preset` (str nao vazio), `verified` (bool,
      default False). Passa pelas mesmas regras de `tools/tracks.py`.
    - `tool`: ferramenta-alvo desta track para resolucao de receita de
      `style.<familia>.techniques[]` (ex.: "MODO Bass", "Superior Drummer").
      Normalizada igual a `instrument.plugin` de elemento gerado (ver
      `tools.render._normalize_tool_name`) — resolve a receita especifica do
      manual quando existir; ausencia cai em `generic` sem fallback
      artificial. SEPARADO de `suggested_instrument`: aquele e so metadado
      de exibicao, este muda qual receita a tecnica le (ex.: sem declarar
      `tool="modo_bass"`, `bass.attack_style` nao acha `keyswitch_dedo` na
      receita `generic` e vira no-op — a track nunca ganha o keyswitch que
      diz ao MODO BASS pra tocar com dedo). Quando declarado, `validate()`
      exige string nao vazia apos strip com pelo menos um caractere
      alfanumerico — valor so com separador/pontuacao (ex.: `"!!!"`)
      normalizaria para vazio em `_normalize_tool_name` e cairia em
      `generic` em silencio, recriando o mesmo no-op que este campo existe
      para evitar.
    """
    track: str
    profile: str
    intensity: float
    suggested_instrument: dict[str, Any] | None = None
    tool: str | None = None


@dataclass
class StyleTechnique:
    name: str
    density: float | None = None
    rationale: str | None = None
    style: str | None = None
    """Selecao de tecnica de execucao (dedo/palheta/slap em bass.attack_style
    e afins). Vocabulario FECHADO — ver STYLE_TECHNIQUE_STYLE_VALUES em
    style_schema.py. NAO e um campo de texto livre."""


@dataclass
class FamilyStyle:
    reference: str
    researched_at: str
    sources: list[str]
    confidence: str
    techniques: list[StyleTechnique]
    parameters: dict[str, float | list[float]]


@dataclass
class PlanSession:
    """Sessao de trabalho herdada do brief (issue #96).

    - `id`: string nao vazia (o brief usa UUID; o plano nao valida formato,
      so exige nao-vazio — o brief e a fonte de verdade).
    - `intent`: um de `SESSION_INTENTS`.
    - `families_in_scope`: subconjunto de STYLE_FAMILIES sem duplicatas.
      Vazio significa que o plano nao pode declarar tecnica nem elemento
      de familia nenhuma (raramente util; para nao restringir, omita o
      bloco inteiro).
    - `created_at`: ISO-8601 UTC (`YYYY-MM-DDTHH:MM:SS[.fff]Z`).
    """
    id: str
    intent: str
    families_in_scope: list[str]
    created_at: str


@dataclass
class ArrangementPlan:
    version: int
    seed: int
    source_midi: SourceMidi
    route: str
    sections: list[PlanSection]
    elements: list[Element]
    brief_ref: BriefRef | None = None
    assumptions: list[str] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    edits: list[PlanEdit] = field(default_factory=list)
    style: dict[str, FamilyStyle] | None = None
    annotations: list[PlanAnnotation] = field(default_factory=list)
    session: PlanSession | None = None


# --- validacao --------------------------------------------------------------

def _require_in(value: Any, allowed: tuple[str, ...], path: str) -> None:
    if value not in allowed:
        raise PlanValidationError(
            path,
            f"expected one of {list(allowed)}, got {value!r}",
        )


def _require_nonempty_str(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value:
        raise PlanValidationError(path, "must be non-empty string")


def _require_nonblank_str(value: Any, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PlanValidationError(path, "must be non-empty string after strip")


def _validate_energy(energy: Any, path: str) -> None:
    """Cada secao carrega os 5 eixos (0-10) do spec, sem chave extra e sem faltar."""
    if not isinstance(energy, dict):
        raise PlanValidationError(path, f"must be dict with axes {list(ENERGY_AXES)}, got {type(energy).__name__}")
    missing = [ax for ax in ENERGY_AXES if ax not in energy]
    if missing:
        raise PlanValidationError(path, f"missing axes {missing}")
    extra = [k for k in energy if k not in ENERGY_AXES]
    if extra:
        raise PlanValidationError(path, f"unknown axes {extra}")
    for ax in ENERGY_AXES:
        v = energy[ax]
        if not isinstance(v, int) or isinstance(v, bool):
            raise PlanValidationError(f"{path}.{ax}", f"must be int, got {type(v).__name__}")
        if v < ENERGY_MIN or v > ENERGY_MAX:
            raise PlanValidationError(f"{path}.{ax}", f"must be in {ENERGY_MIN}-{ENERGY_MAX}, got {v}")


def _build_techniques_index_for_style():
    from .techniques import TechniqueError, build_index

    try:
        return build_index()
    except TechniqueError as exc:
        raise PlanValidationError(
            "style.techniques",
            f"could not build techniques index: {exc}",
        ) from None


def _canonicalize_authorized_name(index, family: str, name: str) -> str | None:
    """Devolve o canonical (`familia.tecnica`) para um nome autorizado.

    Aceita apelido curto (`ghost_notes`) e canonical (`drums.ghost_notes`).
    Nome que nao casa e ignorado — a validacao do brief (US-001) ja errou nesse
    caso; aqui o objetivo e comparar a lista autorizada contra o plano.
    """
    resolved = index.get(name) or next(
        (t for t in index.candidates(name) if t.family == family), None
    )
    return resolved.canonical if resolved is not None else None


def _load_brief_authorized_techniques(
    plan: ArrangementPlan, plan_dir: Path | None,
) -> dict[str, set[str]]:
    """Le o brief apontado por `plan.brief_ref` e devolve as autorizacoes.

    Verifica `brief_ref.sha256` antes de confiar no conteudo — autorizacao
    editada depois de aprovada deixaria o hash divergir. Retorna um dict
    `{familia: {canonical, ...}}` para as quatro familias, mesmo que uma
    familia esteja ausente do brief (nesse caso, conjunto vazio).

    Levanta `PlanValidationError` com o path exato (`brief_ref.path` ou
    `brief_ref.sha256`) para brief inexistente, ilegivel, JSON invalido
    ou hash divergente.
    """
    from .brief_ref import brief_sha256

    ref = plan.brief_ref
    assert ref is not None  # chamada so quando brief_ref esta presente
    brief_path = Path(ref.path).expanduser()
    if not brief_path.is_absolute() and plan_dir is not None:
        brief_path = plan_dir / brief_path

    if not brief_path.is_file():
        raise PlanValidationError(
            "brief_ref.path",
            f"brief file not found at {brief_path}",
        )
    try:
        actual_sha = brief_sha256(brief_path)
    except OSError as exc:
        raise PlanValidationError(
            "brief_ref.path",
            f"could not read brief file at {brief_path}: {exc}",
        ) from None
    if actual_sha != ref.sha256:
        raise PlanValidationError(
            "brief_ref.sha256",
            (
                f"brief sha256 mismatch: plan declares {ref.sha256}, "
                f"brief at {brief_path} hashes to {actual_sha} — "
                "autorizacao pode ter sido editada apos aprovacao"
            ),
        )
    try:
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanValidationError(
            "brief_ref.path",
            f"could not parse brief file at {brief_path}: {exc}",
        ) from None
    if not isinstance(brief, dict):
        raise PlanValidationError(
            "brief_ref.path",
            f"brief at {brief_path} must be a JSON object",
        )

    style = brief.get("style")
    if not isinstance(style, dict):
        raise PlanValidationError(
            "brief_ref.path",
            f"brief at {brief_path} has no 'style' object",
        )

    index = _build_techniques_index_for_style()
    authorized: dict[str, set[str]] = {family: set() for family in STYLE_FAMILIES}
    for family in STYLE_FAMILIES:
        entry = style.get(family) or {}
        if not isinstance(entry, dict):
            continue
        raw = entry.get("authorized_techniques") or []
        if not isinstance(raw, list):
            continue
        for name in raw:
            if not isinstance(name, str):
                continue
            canonical = _canonicalize_authorized_name(index, family, name)
            if canonical is not None:
                authorized[family].add(canonical)
    return authorized


def load_brief_instrument_tuning(
    plan: ArrangementPlan, plan_dir: Path | None,
) -> dict[str, tuple[int, ...]]:
    """Le `brief.instruments.<familia>` (issue #44) e devolve a afinacao
    declarada, resolvida para MIDI ints grave->agudo, por familia de corda.

    Caminho MINIMO da integracao pedida pelo review do PR #64 (achado P1):
    a declaracao do usuario alimenta `TechniqueContext.parameters["tuning"]`
    (`tools/render.py`), que `tools/techniques/physical.py` ja sabe ler para
    checar plausibilidade fisica de ornamento — sem isso, `instruments` era
    validado e ignorado, o "parametro mentiroso" que o AGENTS.md proibe.

    So familia com `known=true` e `tuning` resolvivel entra no dict —
    familia ausente, `known=false` ou brief sem `instruments` nao aparecem
    (o chamador cai no default fisico de `physical.py` para essa familia,
    igual a hoje). Reusa o mesmo brief ja lido/validado por
    `_load_brief_authorized_techniques` (mesma checagem de sha256), mas
    fica FORA do escopo de aviso de conflito com a inferencia automatica de
    `tools/tuning.py` (#35) e de propagacao para todo ponto do render —
    isso fica para uma issue de acompanhamento; ver AGENTS.md sobre nao
    inventar arquitetura nova sob pressao de review.
    """
    from . import tuning as tuning_mod
    from .brief_ref import brief_sha256

    ref = plan.brief_ref
    if ref is None:
        return {}
    brief_path = Path(ref.path).expanduser()
    if not brief_path.is_absolute() and plan_dir is not None:
        brief_path = plan_dir / brief_path
    if not brief_path.is_file():
        return {}
    try:
        if brief_sha256(brief_path) != ref.sha256:
            return {}
        brief = json.loads(brief_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(brief, dict):
        return {}
    instruments = brief.get("instruments")
    if not isinstance(instruments, dict):
        return {}

    result: dict[str, tuple[int, ...]] = {}
    for family in ("guitar", "bass"):
        entry = instruments.get(family)
        if not isinstance(entry, dict) or entry.get("known") is not True:
            continue
        tuning = entry.get("tuning")
        strings = entry.get("strings")
        if not isinstance(tuning, dict) or not isinstance(strings, int):
            continue
        notes = tuning.get("notes")
        if isinstance(notes, list) and notes and all(
            isinstance(n, int) for n in notes
        ):
            result[family] = tuple(notes)
            continue
        name = tuning.get("name")
        if isinstance(name, str) and name:
            resolved = tuning_mod.resolve_tuning_name(name, strings)
            if resolved is not None:
                result[family] = resolved
    return result


def _reject_style_techniques_without_brief(plan_style: Any) -> None:
    """Sem `brief_ref` nenhuma tecnica pode ser autorizada.

    A regra e simetrica a `_load_brief_authorized_techniques`: a autorizacao
    vive no brief; sem brief nao ha autorizacao, logo qualquer
    `style.<familia>.techniques[]` nao vazia e erro. Falhar cedo aqui evita
    que `_validate_style` peca autorizacao para uma lista que nunca podera
    passar.
    """
    if not isinstance(plan_style, dict):
        return
    for family, entry in plan_style.items():
        if family not in STYLE_FAMILIES:
            continue
        if not isinstance(entry, FamilyStyle):
            continue
        techniques = entry.techniques
        if not isinstance(techniques, list) or not techniques:
            continue
        for i, technique in enumerate(techniques):
            name = getattr(technique, "name", None)
            path = f"style.{family}.techniques[{i}].name"
            raise PlanValidationError(
                path,
                (
                    f"technique {name!r} declared for family {family!r} but "
                    "plan has no brief_ref; sem brief_ref nao ha autorizacao "
                    "e nenhuma tecnica pode ser aplicada — tecnica so se "
                    "aplica se o usuario autorizou"
                ),
            )


def _resolve_style_technique(index, family: str, name: str, path: str):
    import difflib

    from .techniques import SUPPORTED_TECHNIQUES

    found = index.candidates(name)
    in_family = tuple(t for t in found if t.family == family)
    if in_family:
        technique = in_family[0]
        if technique.canonical in SUPPORTED_TECHNIQUES:
            return technique
        listing = (
            ", ".join(SUPPORTED_TECHNIQUES)
            if SUPPORTED_TECHNIQUES
            else "(nenhuma tecnica implementada)"
        )
        raise PlanValidationError(
            path,
            (
                f"technique {technique.canonical!r} exists in techniques index "
                "but is not implemented by the engine; implemented techniques: "
                f"{listing}"
            ),
        )

    if found:
        raise PlanValidationError(
            path,
            (
                f"technique {name!r} is not available for style family {family!r}; "
                f"candidates: {[t.canonical for t in found]}"
            ),
        )

    candidates = list(index.names()) + [t.name for t in index.techniques]
    matches = difflib.get_close_matches(name, candidates, n=5, cutoff=0.4)
    hint = f"; close candidates: {matches}" if matches else ""
    raise PlanValidationError(
        path,
        f"technique {name!r} does not exist in techniques index{hint}",
    )


def _style_parameter_values(value: float | list[float]) -> tuple[float, ...]:
    if is_style_parameter_pair(value):
        return float(value[0]), float(value[1])
    return (float(value),)


def _format_manual_range(lo: float, hi: float) -> str:
    return f"[{lo:g}, {hi:g}]"


def _validate_style_parameters_against_techniques(
    parameters: dict[str, float | list[float]],
    techniques: list[Any],
    base: str,
    warnings: list[str],
) -> None:
    for key, value in parameters.items():
        path = f"{base}.parameters.{key}"
        declarations = [
            (technique, parameter)
            for technique in techniques
            for parameter in technique.parameters
            if parameter.name == key
        ]
        for technique, parameter in declarations:
            if parameter.range is not None:
                lo, hi = float(parameter.range[0]), float(parameter.range[1])
                values = _style_parameter_values(value)
                if any(v < lo or v > hi for v in values):
                    raise PlanValidationError(
                        path,
                        (
                            f"value {value!r} outside expected range "
                            f"{_format_manual_range(lo, hi)} declared by "
                            f"{technique.canonical}.{parameter.name}"
                        ),
                    )
                continue

            if parameter.value is None and parameter.source is None:
                warnings.append(
                    f"{path}: parameter {key!r} is a source gap in "
                    f"{technique.canonical}; no manual range/source exists"
                )


def _raise_style_musical_content(path: str, reason: str) -> None:
    raise PlanValidationError(
        path,
        (
            f"{reason}; perfil de estilo aceita parametros de tecnica, "
            "nunca conteudo musical"
        ),
    )


def _reject_musical_content_in_style_value(
    value: Any,
    path: str,
    *,
    allow_parameter_pair: bool = False,
) -> None:
    violation = find_style_musical_content(
        value,
        path,
        allow_parameter_pair=allow_parameter_pair,
    )
    if violation is not None:
        _raise_style_musical_content(*violation)


def _family_style_content_scan_dict(entry: FamilyStyle) -> dict[str, Any]:
    techniques = entry.techniques
    if isinstance(techniques, list):
        techniques = [
            {
                "name": technique.name,
                "density": technique.density,
                "rationale": technique.rationale,
                "style": technique.style,
            }
            if isinstance(technique, StyleTechnique)
            else technique
            for technique in techniques
        ]
    return {
        "reference": entry.reference,
        "researched_at": entry.researched_at,
        "sources": entry.sources,
        "confidence": entry.confidence,
        "techniques": techniques,
        "parameters": entry.parameters,
    }


def _validate_style(
    plan_style: Any,
    brief_authorized: dict[str, set[str]] | None = None,
) -> list[str]:
    warnings: list[str] = []
    if plan_style is None:
        return warnings
    if not isinstance(plan_style, dict):
        raise PlanValidationError(
            "style",
            f"must be dict with families {list(STYLE_FAMILIES)}, got {type(plan_style).__name__}",
        )
    technique_index = (
        _build_techniques_index_for_style()
        if any(
            isinstance(entry, FamilyStyle) and entry.techniques
            for entry in plan_style.values()
        )
        else None
    )
    for family, entry in plan_style.items():
        base = f"style.{family}"
        if family not in STYLE_FAMILIES:
            raise PlanValidationError(
                base,
                f"unknown style family {family!r}; expected one of {list(STYLE_FAMILIES)}",
            )
        if not isinstance(entry, FamilyStyle):
            raise PlanValidationError(base, f"must be FamilyStyle, got {type(entry).__name__}")
        _reject_musical_content_in_style_value(_family_style_content_scan_dict(entry), base)
        _require_nonempty_str(entry.reference, f"{base}.reference")
        _require_nonempty_str(entry.researched_at, f"{base}.researched_at")
        # `date.fromisoformat` aceita `20260824` e `2026-W35-1`, que a fachada
        # JSON Schema recusa. Duas verdades sobre a mesma data e exatamente o
        # tipo de divergencia dominio/fachada que este projeto nao tolera.
        if not ISO_DATE_RE.match(entry.researched_at):
            raise PlanValidationError(
                f"{base}.researched_at",
                f"must be an ISO-8601 date in YYYY-MM-DD, got {entry.researched_at!r}",
            )
        try:
            date.fromisoformat(entry.researched_at)
        except ValueError:
            raise PlanValidationError(
                f"{base}.researched_at",
                f"must be a real calendar date, got {entry.researched_at!r}",
            ) from None
        if not isinstance(entry.sources, list) or not entry.sources:
            raise PlanValidationError(f"{base}.sources", "must be non-empty list of strings")
        for i, source in enumerate(entry.sources):
            _require_nonempty_str(source, f"{base}.sources[{i}]")
        _require_in(entry.confidence, STYLE_CONFIDENCE_LEVELS, f"{base}.confidence")
        if not isinstance(entry.techniques, list):
            raise PlanValidationError(
                f"{base}.techniques",
                f"must be list, got {type(entry.techniques).__name__}",
            )
        resolved_techniques: list[Any] = []
        for i, technique in enumerate(entry.techniques):
            technique_base = f"{base}.techniques[{i}]"
            if not isinstance(technique, StyleTechnique):
                raise PlanValidationError(
                    technique_base,
                    f"must be StyleTechnique, got {type(technique).__name__}",
                )
            _require_nonempty_str(technique.name, f"{technique_base}.name")
            if technique.density is not None:
                if not isinstance(technique.density, (int, float)) or isinstance(technique.density, bool):
                    raise PlanValidationError(
                        f"{technique_base}.density",
                        f"must be number, got {type(technique.density).__name__}",
                )
                if not 0.0 <= float(technique.density) <= 1.0:
                    raise PlanValidationError(
                        f"{technique_base}.density",
                        f"must be in 0.0-1.0, got {technique.density}",
                    )
            if technique.rationale is not None and not isinstance(technique.rationale, str):
                raise PlanValidationError(
                    f"{technique_base}.rationale",
                    f"must be string or null, got {type(technique.rationale).__name__}",
                )
            if technique.style is not None:
                _require_in(
                    technique.style,
                    tuple(sorted(STYLE_TECHNIQUE_STYLE_VALUES)),
                    f"{technique_base}.style",
                )
            if technique_index is not None:
                resolved = _resolve_style_technique(
                    technique_index,
                    family,
                    technique.name,
                    f"{technique_base}.name",
                )
                resolved_techniques.append(resolved)
                if brief_authorized is not None:
                    allowed = brief_authorized.get(family, set())
                    if resolved.canonical not in allowed:
                        raise PlanValidationError(
                            f"{technique_base}.name",
                            (
                                f"technique {technique.name!r} is not in "
                                f"authorized_techniques for family "
                                f"{family!r} (brief authorized: "
                                f"{sorted(allowed) or '[]'}); tecnica so se "
                                "aplica se o usuario autorizou"
                            ),
                        )
        if not isinstance(entry.parameters, dict):
            raise PlanValidationError(
                f"{base}.parameters",
                f"must be dict of numbers, got {type(entry.parameters).__name__}",
            )
        for key, value in entry.parameters.items():
            if not isinstance(key, str) or not key:
                raise PlanValidationError(f"{base}.parameters", "parameter names must be non-empty strings")
            if is_style_parameter_pair(value):
                continue
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise PlanValidationError(
                    f"{base}.parameters.{key}",
                    f"must be number or [min, max] pair, got {type(value).__name__}",
                )
        _validate_style_parameters_against_techniques(
            entry.parameters,
            resolved_techniques,
            base,
            warnings,
        )
    return warnings


def _style_family_for_role(role: str) -> str | None:
    if role in STYLE_FAMILIES:
        return role
    return ROLE_STYLE_FAMILIES.get(role)


def style_families_used_by_plan(plan: ArrangementPlan) -> tuple[str, ...]:
    """Familias de `style` que o plano efetivamente usa.

    A fronteira do schema tem quatro familias musicais, enquanto elementos
    usam roles de render. Este helper centraliza a traducao para que defaults
    de estilo, validadores futuros e fachadas falem a mesma lingua.
    """
    used: set[str] = set()
    for edit in plan.edits:
        if edit.profile in STYLE_FAMILIES:
            used.add(edit.profile)
    for element in plan.elements:
        family = _style_family_for_role(element.role)
        if family is not None:
            used.add(family)
    return tuple(family for family in STYLE_FAMILIES if family in used)


def _default_family_style() -> FamilyStyle:
    return FamilyStyle(
        reference=DEFAULT_STYLE_REFERENCE,
        researched_at=DEFAULT_STYLE_RESEARCHED_AT,
        sources=[DEFAULT_STYLE_SOURCE],
        confidence="default",
        techniques=[],
        parameters={},
    )


def normalize_style_defaults(plan: ArrangementPlan) -> ArrangementPlan:
    """Devolve copia do plano com `style` default para familias usadas.

    A funcao nao muta `plan`: o arquivo de origem e o objeto do chamador seguem
    representando exatamente o que foi escrito. A copia normalizada explicita
    quando uma familia caiu na persona base por falta de perfil pesquisado.
    """
    normalized = deepcopy(plan)
    used_families = style_families_used_by_plan(normalized)
    if not used_families:
        return normalized

    if normalized.style is None:
        normalized.style = {}

    for family in used_families:
        if family in normalized.style:
            continue
        normalized.style[family] = _default_family_style()
        assumption = DEFAULT_STYLE_ASSUMPTION_TEMPLATE.format(family=family)
        if assumption not in normalized.assumptions:
            normalized.assumptions.append(assumption)
    return normalized


SOURCE_ANNOTATION_FIELDS = ("text", "tick", "bar", "track", "event_type")
PLAN_ANNOTATION_FIELDS = (
    "text", "tick", "bar", "track", "event_type", "status", "element_id", "reason",
)
ANNOTATION_EVENT_TYPES = ("marker", "text", "cue_marker")


def _validate_source_annotation(data: Any, path: str) -> None:
    """Regras estruturais de `SourceAnnotation` (issue #32)."""
    if not isinstance(data, SourceAnnotation):
        raise PlanValidationError(
            path, f"must be SourceAnnotation, got {type(data).__name__}",
        )
    _require_nonblank_str(data.text, f"{path}.text")
    _require_nonblank_str(data.track, f"{path}.track")
    _require_in(data.event_type, ANNOTATION_EVENT_TYPES, f"{path}.event_type")
    if not isinstance(data.tick, int) or isinstance(data.tick, bool) or data.tick < 0:
        raise PlanValidationError(
            f"{path}.tick", f"must be non-negative int, got {data.tick!r}",
        )
    if not isinstance(data.bar, int) or isinstance(data.bar, bool) or data.bar < 0:
        raise PlanValidationError(
            f"{path}.bar", f"must be non-negative int, got {data.bar!r}",
        )


SUGGESTED_INSTRUMENT_FIELDS = ("plugin", "preset", "verified")


def _validate_suggested_instrument(
    data: Any, profile: str, path: str,
) -> None:
    """Regras de `tools/tracks.py` aplicadas a sugestao de instrumento em edit.

    Recusa plugin em `FORBIDDEN_PLUGINS`, exige plugin default de `SAMPLER_ROUTING`
    quando o profile tem um, e recusa Serum fora do escopo de FR-14.
    """
    from .tracks import (
        FORBIDDEN_PLUGINS,
        SERUM_ALLOWED_ROLES,
        SERUM_PLUGIN_NAME,
        default_plugin_for_role,
        is_ascii_safe,
    )

    if not isinstance(data, dict):
        raise PlanValidationError(
            path, f"must be object, got {type(data).__name__}",
        )
    extra = [k for k in data if k not in SUGGESTED_INSTRUMENT_FIELDS]
    if extra:
        raise PlanValidationError(
            path, f"unknown fields {extra}; allowed {list(SUGGESTED_INSTRUMENT_FIELDS)}",
        )
    plugin = data.get("plugin")
    preset = data.get("preset")
    verified = data.get("verified", False)
    if not isinstance(plugin, str) or not plugin.strip():
        raise PlanValidationError(f"{path}.plugin", "must be non-empty string")
    if not isinstance(preset, str) or not preset.strip():
        raise PlanValidationError(f"{path}.preset", "must be non-empty string")
    if not isinstance(verified, bool):
        raise PlanValidationError(
            f"{path}.verified", f"must be bool, got {type(verified).__name__}",
        )
    if not is_ascii_safe(plugin):
        raise PlanValidationError(
            f"{path}.plugin",
            f"must be ASCII (meta-evento SMF nao carrega encoding), got {plugin!r}",
        )
    if not is_ascii_safe(preset):
        raise PlanValidationError(
            f"{path}.preset",
            f"must be ASCII (meta-evento SMF nao carrega encoding), got {preset!r}",
        )
    if "|" in plugin or "|" in preset:
        raise PlanValidationError(
            path,
            "plugin/preset must not contain '|' — separador reservado do carimbo",
        )
    if plugin in FORBIDDEN_PLUGINS:
        raise PlanValidationError(
            f"{path}.plugin",
            f"plugin {plugin!r} is forbidden by FR-24 "
            "(Trigger_2/Addictive Trigger nunca sao sugeridos)",
        )
    default = default_plugin_for_role(profile)
    if default is not None and plugin != default:
        raise PlanValidationError(
            f"{path}.plugin",
            f"profile {profile!r} must use {default!r} per FR-24, got {plugin!r}",
        )
    if plugin == SERUM_PLUGIN_NAME and profile not in SERUM_ALLOWED_ROLES:
        raise PlanValidationError(
            f"{path}.plugin",
            f"Serum is not allowed for profile {profile!r} per FR-14 "
            f"(allowed: {sorted(SERUM_ALLOWED_ROLES)})",
        )


_ISO_DATETIME_UTC_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$"
)


def _validate_session(session: Any) -> None:
    """Valida o bloco `session` do plano (issue #96).

    Mesmos vocabularios fechados do brief — `intent` em SESSION_INTENTS,
    cada familia em STYLE_FAMILIES, sem duplicatas. Estrutura invalida vira
    `PlanValidationError` com path exato.
    """
    if not isinstance(session, PlanSession):
        raise PlanValidationError(
            "session", f"must be PlanSession, got {type(session).__name__}"
        )
    _require_nonempty_str(session.id, "session.id")
    _require_in(session.intent, SESSION_INTENTS, "session.intent")
    if not isinstance(session.families_in_scope, list):
        raise PlanValidationError(
            "session.families_in_scope",
            f"must be list, got {type(session.families_in_scope).__name__}",
        )
    for i, family in enumerate(session.families_in_scope):
        if family not in STYLE_FAMILIES:
            raise PlanValidationError(
                f"session.families_in_scope[{i}]",
                f"expected one of {list(STYLE_FAMILIES)}, got {family!r}",
            )
    if len(session.families_in_scope) != len(set(session.families_in_scope)):
        raise PlanValidationError(
            "session.families_in_scope",
            f"duplicate entries: {session.families_in_scope!r}",
        )
    if (
        not isinstance(session.created_at, str)
        or not _ISO_DATETIME_UTC_RE.match(session.created_at)
    ):
        raise PlanValidationError(
            "session.created_at",
            (
                f"must be ISO-8601 UTC (YYYY-MM-DDTHH:MM:SS[.fff]Z), "
                f"got {session.created_at!r}"
            ),
        )
    try:
        date.fromisoformat(session.created_at[:10])
        # tempo real do calendario: `datetime.fromisoformat` no 3.11+ aceita `Z`.
        from datetime import datetime as _datetime
        _datetime.fromisoformat(session.created_at.replace("Z", "+00:00"))
    except ValueError:
        raise PlanValidationError(
            "session.created_at",
            f"is not a real calendar date/time: {session.created_at!r}",
        ) from None


def _validate_session_scope(plan: ArrangementPlan) -> None:
    """Fronteira de escopo da sessao (issue #96).

    `plan.session.families_in_scope` recorta o que o plano pode fazer:
    - nenhum `plan.style.<familia>.techniques[]` fora do escopo pode ter item;
    - nenhum `plan.elements[]` cuja `role` mapeie para familia fora do escopo.

    `plan.edits[]` fica livre: tracks do MIDI que nao entram no escopo saem
    byte-identicas (sem receber tecnica), o que ja e o comportamento seguro
    de `render._apply_style_techniques_to_edit_tracks` para toda familia sem
    entrada em `plan.style`. Role sem mapeamento de familia
    (`_style_family_for_role` retorna None) tambem passa — o escopo cobre
    material das quatro familias musicais, o resto e neutro.
    """
    if plan.session is None:
        return
    scope = set(plan.session.families_in_scope)
    if isinstance(plan.style, dict):
        for family, entry in plan.style.items():
            if family not in STYLE_FAMILIES:
                continue
            if not isinstance(entry, FamilyStyle):
                continue
            if not entry.techniques:
                continue
            if family not in scope:
                raise PlanValidationError(
                    f"style.{family}.techniques",
                    (
                        f"family {family!r} declares techniques but is outside "
                        f"session.families_in_scope ({sorted(scope) or '[]'}); "
                        "sessao de trabalho recorta o escopo — tecnica so pode "
                        "aparecer para familia em escopo"
                    ),
                )
    for i, element in enumerate(plan.elements):
        family = _style_family_for_role(element.role)
        if family is None:
            continue
        if family not in scope:
            raise PlanValidationError(
                f"elements[{i}].role",
                (
                    f"role {element.role!r} belongs to family {family!r}, "
                    f"outside session.families_in_scope ({sorted(scope) or '[]'})"
                ),
            )


def validate(
    plan: ArrangementPlan, plan_dir: Path | str | None = None,
) -> list[str]:
    """Valida um `ArrangementPlan` e devolve avisos nao-bloqueantes.

    Ordem: campos raiz -> source_midi -> sections -> elements. Section
    labels sao coletados antes de validar elements para que a checagem
    de referencia funcione mesmo sem sections declaradas em ordem
    especifica.

    Quando `plan.brief_ref` esta presente, le o brief apontado por
    `brief_ref.path` (relativo a `plan_dir`, se dado; senao relativo ao
    diretorio corrente), confere que `brief_ref.sha256` casa com o hash real
    do arquivo (`tools.brief_ref.brief_sha256`) e exige que toda tecnica em
    `plan.style.<familia>.techniques[]` esteja em
    `brief.style.<familia>.authorized_techniques`. Ausencia de autorizacao
    significa NENHUMA tecnica — nunca "todas". Isso vale tambem quando o
    plano nao declara `brief_ref`: sem brief nao ha como saber o que o
    usuario autorizou, entao qualquer `style.<familia>.techniques[]` nao
    vazia e erro. Brief inexistente, ilegivel ou com hash divergente e erro
    explicito, nunca fallback silencioso.

    Retorna lista de avisos (strings). Avisos NAO bloqueiam. Erros
    bloqueiam via `PlanValidationError`.
    """
    warnings: list[str] = []

    if plan.version != SCHEMA_VERSION:
        raise PlanValidationError(
            "version",
            f"expected {SCHEMA_VERSION}, got {plan.version!r}",
        )
    if not isinstance(plan.seed, int) or isinstance(plan.seed, bool):
        raise PlanValidationError("seed", f"must be int, got {type(plan.seed).__name__}")

    if plan.session is not None:
        _validate_session(plan.session)

    _require_in(plan.route, ROUTES, "route")
    _require_nonempty_str(plan.source_midi.path, "source_midi.path")
    _require_nonempty_str(plan.source_midi.sha256, "source_midi.sha256")
    brief_authorized: dict[str, set[str]] | None = None
    if plan.brief_ref is not None:
        _require_nonempty_str(plan.brief_ref.path, "brief_ref.path")
        if not isinstance(plan.brief_ref.sha256, str) or not SHA256_RE.fullmatch(plan.brief_ref.sha256):
            raise PlanValidationError(
                "brief_ref.sha256",
                "must be 64 lowercase hexadecimal characters",
            )
        brief_authorized = _load_brief_authorized_techniques(
            plan, Path(plan_dir) if plan_dir is not None else None,
        )
    else:
        _reject_style_techniques_without_brief(plan.style)
    warnings.extend(_validate_style(plan.style, brief_authorized))

    section_labels: set[str] = set()
    for i, s in enumerate(plan.sections):
        base = f"sections[{i}]"
        _require_nonempty_str(s.label, f"{base}.label")
        _require_in(s.kind, SECTION_KINDS, f"{base}.kind")
        _require_in(s.source, SECTION_SOURCES, f"{base}.source")
        if s.protagonist is None:
            raise PlanValidationError(f"{base}.protagonist", "missing protagonist")
        _require_in(s.protagonist, PROTAGONISTS, f"{base}.protagonist")
        if s.energy is None:
            raise PlanValidationError(f"{base}.energy", "missing energy")
        _validate_energy(s.energy, f"{base}.energy")
        section_labels.add(s.label)

    for i, e in enumerate(plan.elements):
        base = f"elements[{i}]"
        _require_nonempty_str(e.id, f"{base}.id")
        if not e.role:
            raise PlanValidationError(f"{base}.role", "missing role")
        if not isinstance(e.role, str):
            raise PlanValidationError(f"{base}.role", "must be string")
        if not isinstance(e.layers, int) or isinstance(e.layers, bool) or e.layers < 1:
            raise PlanValidationError(
                f"{base}.layers",
                f"must be integer >= 1, got {e.layers!r}",
            )
        _require_in(e.sync_role, SYNC_ROLES, f"{base}.sync_role")
        _require_in(e.articulation, ARTICULATIONS, f"{base}.articulation")
        _require_in(e.harmony, HARMONY_MODES, f"{base}.harmony")
        _require_nonblank_str(e.rationale, f"{base}.rationale")

        if not isinstance(e.register, list) or len(e.register) != 2:
            raise PlanValidationError(
                f"{base}.register",
                f"must be a list [low, high], got {e.register!r}",
            )
        for j, v in enumerate(e.register):
            if not isinstance(v, int) or isinstance(v, bool):
                raise PlanValidationError(
                    f"{base}.register[{j}]",
                    f"must be int, got {type(v).__name__}",
                )
            if v < MIDI_PITCH_MIN or v > MIDI_PITCH_MAX:
                raise PlanValidationError(
                    f"{base}.register[{j}]",
                    f"must be in {MIDI_PITCH_MIN}-{MIDI_PITCH_MAX}, got {v}",
                )
        if e.register[0] > e.register[1]:
            raise PlanValidationError(
                f"{base}.register",
                f"low must be <= high, got {e.register}",
            )

        if not isinstance(e.is_protagonist, bool):
            raise PlanValidationError(
                f"{base}.is_protagonist",
                f"must be bool, got {type(e.is_protagonist).__name__}",
            )

        for j, label in enumerate(e.sections):
            if label not in section_labels:
                raise PlanValidationError(
                    f"{base}.sections[{j}]",
                    f"section {label!r} not declared in plan.sections",
                )

    # BLOQUEIO: `source_annotation` do elemento cita o texto no `rationale`.
    # Sem essa amarra a autoria da anotacao vira decorativa — mesma categoria
    # do `rationale` vazio ja rejeitado acima.
    element_ids_seen: set[str] = set()
    elements_by_id: dict[str, Element] = {}
    for i, e in enumerate(plan.elements):
        element_ids_seen.add(e.id)
        elements_by_id[e.id] = e
        if e.source_annotation is None:
            continue
        base = f"elements[{i}].source_annotation"
        _validate_source_annotation(e.source_annotation, base)
        text = e.source_annotation.text
        if not isinstance(e.rationale, str) or text not in e.rationale:
            raise PlanValidationError(
                f"elements[{i}].rationale",
                (
                    f"element carries source_annotation {text!r} but rationale "
                    "does not cite it verbatim; anotacao acionada precisa ser "
                    "referenciada no rationale para a autoria ser auditavel"
                ),
            )

    # BLOQUEIO: annotations do plano — status/refs consistentes.
    for i, annot in enumerate(plan.annotations):
        base = f"annotations[{i}]"
        if not isinstance(annot, PlanAnnotation):
            raise PlanValidationError(
                base,
                f"must be PlanAnnotation, got {type(annot).__name__}",
            )
        _require_nonblank_str(annot.text, f"{base}.text")
        _require_nonblank_str(annot.track, f"{base}.track")
        _require_in(annot.event_type, ANNOTATION_EVENT_TYPES, f"{base}.event_type")
        _require_in(annot.status, ANNOTATION_STATUSES, f"{base}.status")
        if not isinstance(annot.tick, int) or isinstance(annot.tick, bool) or annot.tick < 0:
            raise PlanValidationError(f"{base}.tick", f"must be non-negative int, got {annot.tick!r}")
        if not isinstance(annot.bar, int) or isinstance(annot.bar, bool) or annot.bar < 0:
            raise PlanValidationError(f"{base}.bar", f"must be non-negative int, got {annot.bar!r}")
        if annot.status == "actioned":
            if annot.element_id is None or not annot.element_id:
                raise PlanValidationError(
                    f"{base}.element_id",
                    "annotation with status='actioned' must reference an element_id",
                )
            if annot.element_id not in element_ids_seen:
                raise PlanValidationError(
                    f"{base}.element_id",
                    f"element_id {annot.element_id!r} not declared in plan.elements",
                )
            target = elements_by_id[annot.element_id]
            sa = target.source_annotation
            if sa is None:
                raise PlanValidationError(
                    f"{base}.element_id",
                    (
                        f"element {annot.element_id!r} has no source_annotation — "
                        "an actioned annotation must reference an element that "
                        "was actually built from it, for the audit trail to hold"
                    ),
                )
            mismatched = (
                sa.text != annot.text
                or sa.tick != annot.tick
                or sa.bar != annot.bar
                or sa.track != annot.track
                or sa.event_type != annot.event_type
            )
            if mismatched:
                raise PlanValidationError(
                    f"{base}.element_id",
                    (
                        f"element {annot.element_id!r}.source_annotation "
                        f"{_source_annotation_to_dict(sa)!r} does not match this "
                        f"annotation {_plan_annotation_to_dict(annot)!r} — actioned "
                        "annotation must point at the element it actually produced"
                    ),
                )
        else:
            if annot.element_id is not None:
                raise PlanValidationError(
                    f"{base}.element_id",
                    (
                        f"annotation with status={annot.status!r} must not carry "
                        "an element_id — it was not actioned"
                    ),
                )
            if not isinstance(annot.reason, str) or not annot.reason.strip():
                raise PlanValidationError(
                    f"{base}.reason",
                    (
                        f"annotation with status={annot.status!r} must carry a "
                        "non-empty reason explaining why it was not actioned"
                    ),
                )

    # BLOQUEIO: duas camadas na mesma secao nao podem ambas declarar protagonista.
    protagonists_by_section: dict[str, list[str]] = {}
    for e in plan.elements:
        if not e.is_protagonist:
            continue
        for label in e.sections:
            protagonists_by_section.setdefault(label, []).append(e.id)
    for label, ids in protagonists_by_section.items():
        if len(ids) > 1:
            raise PlanValidationError(
                f"sections[{label!r}].protagonist_conflict",
                f"multiple elements declare is_protagonist=True in section {label!r}: {ids}",
            )

    # BLOQUEIO: profile e intensity de cada edit dentro do vocabulario/range.
    # A checagem "track existe no MIDI" nao vive aqui — plan.py nao le o
    # source. E feita por `validate_edits_against_midi(plan, track_names)`,
    # que o renderer chama ao carregar o MIDI de origem.
    edit_tracks_seen: set[str] = set()
    for i, ed in enumerate(plan.edits):
        base = f"edits[{i}]"
        _require_nonempty_str(ed.track, f"{base}.track")
        if ed.track in edit_tracks_seen:
            raise PlanValidationError(
                f"{base}.track",
                f"duplicate edit for track {ed.track!r}",
            )
        edit_tracks_seen.add(ed.track)
        _require_in(ed.profile, EDIT_PROFILES, f"{base}.profile")
        if not isinstance(ed.intensity, (int, float)) or isinstance(ed.intensity, bool):
            raise PlanValidationError(
                f"{base}.intensity",
                f"must be number, got {type(ed.intensity).__name__}",
            )
        if not (EDIT_INTENSITY_MIN <= float(ed.intensity) <= EDIT_INTENSITY_MAX):
            raise PlanValidationError(
                f"{base}.intensity",
                f"must be in {EDIT_INTENSITY_MIN}-{EDIT_INTENSITY_MAX}, "
                f"got {ed.intensity}",
            )
        if ed.suggested_instrument is not None:
            _validate_suggested_instrument(
                ed.suggested_instrument, ed.profile, f"{base}.suggested_instrument",
            )
        if ed.tool is not None:
            _require_nonblank_str(ed.tool, f"{base}.tool")
            if not any(ch.isalnum() for ch in ed.tool):
                raise PlanValidationError(
                    f"{base}.tool",
                    f"must contain at least one alphanumeric character, "
                    f"got {ed.tool!r} (normalizes to no tool, which would "
                    "silently fall back to the generic recipe)",
                )
            # `edit.tool` vira `plugin` no carimbo (`_stamp_edit_tracks`,
            # tools/render.py) igual a `suggested_instrument.plugin` — mesma
            # checagem ASCII/sem '|' daquele campo, senao o erro so aparece
            # tarde no render (`_format_stamp`) em vez de aqui, na validacao.
            from .tracks import is_ascii_safe

            if not is_ascii_safe(ed.tool):
                raise PlanValidationError(
                    f"{base}.tool",
                    f"must be ASCII (meta-evento SMF nao carrega encoding), "
                    f"got {ed.tool!r}",
                )
            if "|" in ed.tool:
                raise PlanValidationError(
                    f"{base}.tool",
                    "must not contain '|' — separador reservado do carimbo",
                )

    # BLOQUEIO: transitions[].dimensions_changed so aceita string — plano em
    # memoria com item nao-string (ex.: via from_dict malformado) precisa
    # falhar aqui, e nao mais tarde com AttributeError em `.strip()` dentro
    # de `tools.validators.transitions._normalize_dimension_name`.
    for i, t in enumerate(plan.transitions):
        base = f"transitions[{i}]"
        # BLOQUEIO: plano em memoria (ou via from_dict malformado) pode
        # trazer `at_bar`/`from_section`/`to_section` fora do tipo — sem
        # esta checagem, `tools.validators.transitions._window_bounds`
        # (`at_bar - WINDOW_BARS`) so falharia tarde, com `TypeError` em vez
        # de `PlanValidationError`, depois de todo o pipeline de render ja
        # ter rodado.
        if not isinstance(t.at_bar, int) or isinstance(t.at_bar, bool):
            raise PlanValidationError(
                f"{base}.at_bar",
                f"must be int, got {type(t.at_bar).__name__}",
            )
        _require_nonblank_str(t.from_section, f"{base}.from_section")
        _require_nonblank_str(t.to_section, f"{base}.to_section")
        if not isinstance(t.dimensions_changed, list):
            raise PlanValidationError(
                f"{base}.dimensions_changed",
                f"must be list, got {type(t.dimensions_changed).__name__}",
            )
        for j, dim in enumerate(t.dimensions_changed):
            if not isinstance(dim, str):
                raise PlanValidationError(
                    f"{base}.dimensions_changed[{j}]",
                    f"must be str, got {type(dim).__name__}",
                )
        # BLOQUEIO: `transitions[].elements` e lido por
        # `tools.render._element_matches_only` com `element.id in
        # t.elements` — plano em memoria (fora da fachada JSON Schema, que
        # ja bloqueia isso) pode trazer `elements=None` (quebra o `in` com
        # `TypeError`) ou uma string bare como `"pad_main"` (`from_dict`
        # converte pra lista de caracteres, e o `in` casa silenciosamente
        # caractere por caractere em vez do ID inteiro). Falha aqui, cedo,
        # em vez de crashar ou misfiltrar tarde dentro do filtro `only`.
        if not isinstance(t.elements, list):
            raise PlanValidationError(
                f"{base}.elements",
                f"must be list, got {type(t.elements).__name__}",
            )
        for j, elem_id in enumerate(t.elements):
            if not isinstance(elem_id, str) or not elem_id.strip():
                raise PlanValidationError(
                    f"{base}.elements[{j}]",
                    f"must be non-empty str, got {elem_id!r}",
                )

    # BLOQUEIO: fronteira de escopo da sessao (issue #96).
    _validate_session_scope(plan)

    # AVISO: todos os 5 eixos sobem simultaneamente entre secoes consecutivas.
    for i in range(len(plan.sections) - 1):
        a = plan.sections[i]
        b = plan.sections[i + 1]
        if a.energy is None or b.energy is None:
            continue
        if all(b.energy[ax] > a.energy[ax] for ax in ENERGY_AXES):
            warnings.append(
                f"sections[{i}]->sections[{i + 1}] ({a.label!r}->{b.label!r}): "
                f"all 5 energy axes rise simultaneously — "
                f"consider dropping at least one axis for contrast"
            )

    return warnings


# --- serializacao -----------------------------------------------------------

def _source_midi_to_dict(s: SourceMidi) -> dict[str, Any]:
    return {
        "path": s.path,
        "sha256": s.sha256,
        "tempo": s.tempo,
        "key": s.key,
        "bars": s.bars,
    }


def _brief_ref_to_dict(ref: BriefRef) -> dict[str, Any]:
    return {
        "path": ref.path,
        "sha256": ref.sha256,
    }


def _session_to_dict(s: PlanSession) -> dict[str, Any]:
    return {
        "id": s.id,
        "intent": s.intent,
        "families_in_scope": list(s.families_in_scope),
        "created_at": s.created_at,
    }


def _section_to_dict(s: PlanSection) -> dict[str, Any]:
    return {
        "label": s.label,
        "kind": s.kind,
        "start_bar": s.start_bar,
        "end_bar": s.end_bar,
        "source": s.source,
        "protagonist": s.protagonist,
        "energy": s.energy,
    }


def _source_annotation_to_dict(sa: SourceAnnotation) -> dict[str, Any]:
    return {
        "text": sa.text,
        "tick": int(sa.tick),
        "bar": int(sa.bar),
        "track": sa.track,
        "event_type": sa.event_type,
    }


def _plan_annotation_to_dict(a: PlanAnnotation) -> dict[str, Any]:
    return {
        "text": a.text,
        "tick": int(a.tick),
        "bar": int(a.bar),
        "track": a.track,
        "event_type": a.event_type,
        "status": a.status,
        "element_id": a.element_id,
        "reason": a.reason,
    }


def _element_to_dict(e: Element) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": e.id,
        "role": e.role,
        "sections": list(e.sections),
        "register": list(e.register),
        "layers": e.layers,
        "sync_role": e.sync_role,
        "articulation": e.articulation,
        "harmony": e.harmony,
        "pattern": e.pattern,
        "degrees": list(e.degrees) if e.degrees is not None else None,
        "dynamics": e.dynamics,
        "instrument": e.instrument,
        "rationale": e.rationale,
        "is_protagonist": e.is_protagonist,
    }
    if e.source_annotation is not None:
        data["source_annotation"] = _source_annotation_to_dict(e.source_annotation)
    return data


def _edit_to_dict(e: PlanEdit) -> dict[str, Any]:
    data: dict[str, Any] = {
        "track": e.track,
        "profile": e.profile,
        "intensity": float(e.intensity),
    }
    if e.suggested_instrument is not None:
        data["suggested_instrument"] = dict(e.suggested_instrument)
    if e.tool is not None:
        data["tool"] = e.tool
    return data


def _style_technique_to_dict(t: StyleTechnique) -> dict[str, Any]:
    data: dict[str, Any] = {"name": t.name}
    if t.density is not None:
        data["density"] = float(t.density)
    if t.rationale is not None:
        data["rationale"] = t.rationale
    if t.style is not None:
        data["style"] = t.style
    return data


def _family_style_to_dict(s: FamilyStyle) -> dict[str, Any]:
    return {
        "reference": s.reference,
        "researched_at": s.researched_at,
        "sources": list(s.sources),
        "confidence": s.confidence,
        "techniques": [_style_technique_to_dict(t) for t in s.techniques],
        "parameters": dict(s.parameters),
    }


def _transition_to_dict(t: Transition) -> dict[str, Any]:
    return {
        "at_bar": t.at_bar,
        "from_section": t.from_section,
        "to_section": t.to_section,
        "dimensions_changed": list(t.dimensions_changed),
        "elements": list(t.elements),
        "technique": t.technique,
    }


def to_dict(plan: ArrangementPlan) -> dict[str, Any]:
    data = {
        "version": plan.version,
        "seed": plan.seed,
        "source_midi": _source_midi_to_dict(plan.source_midi),
        "route": plan.route,
        "assumptions": list(plan.assumptions),
        "sections": [_section_to_dict(s) for s in plan.sections],
        "elements": [_element_to_dict(e) for e in plan.elements],
        "transitions": [_transition_to_dict(t) for t in plan.transitions],
        "edits": [_edit_to_dict(ed) for ed in plan.edits],
        "annotations": [_plan_annotation_to_dict(a) for a in plan.annotations],
    }
    if plan.style is not None:
        data["style"] = {
            family: _family_style_to_dict(entry)
            for family, entry in plan.style.items()
        }
    if plan.brief_ref is not None:
        data["brief_ref"] = _brief_ref_to_dict(plan.brief_ref)
    if plan.session is not None:
        data["session"] = _session_to_dict(plan.session)
    return data


def _source_midi_from_dict(data: dict[str, Any]) -> SourceMidi:
    return SourceMidi(
        path=data["path"],
        sha256=data["sha256"],
        tempo=data.get("tempo"),
        key=data.get("key"),
        bars=data.get("bars"),
    )


def _brief_ref_from_dict(data: Any) -> BriefRef:
    if not isinstance(data, dict):
        raise PlanValidationError("brief_ref", f"must be object, got {type(data).__name__}")
    _reject_unknown_keys(data, ("path", "sha256"), "brief_ref")
    return BriefRef(
        path=_require_field(data, "path", "brief_ref"),
        sha256=_require_field(data, "sha256", "brief_ref"),
    )


def _session_from_dict(data: Any) -> PlanSession:
    if not isinstance(data, dict):
        raise PlanValidationError(
            "session", f"must be object, got {type(data).__name__}",
        )
    _reject_unknown_keys(data, SESSION_FIELDS, "session")
    families = _require_field(data, "families_in_scope", "session")
    if not isinstance(families, list):
        raise PlanValidationError(
            "session.families_in_scope",
            f"must be list, got {type(families).__name__}",
        )
    return PlanSession(
        id=_require_field(data, "id", "session"),
        intent=_require_field(data, "intent", "session"),
        families_in_scope=list(families),
        created_at=_require_field(data, "created_at", "session"),
    )


def _section_from_dict(data: dict[str, Any]) -> PlanSection:
    return PlanSection(
        label=data["label"],
        kind=data["kind"],
        start_bar=data["start_bar"],
        end_bar=data["end_bar"],
        source=data["source"],
        protagonist=data.get("protagonist"),
        energy=data.get("energy"),
    )


def _source_annotation_from_dict(data: Any, path: str) -> SourceAnnotation:
    if not isinstance(data, dict):
        raise PlanValidationError(path, f"must be object, got {type(data).__name__}")
    _reject_unknown_keys(data, SOURCE_ANNOTATION_FIELDS, path)
    return SourceAnnotation(
        text=_require_field(data, "text", path),
        tick=_require_field(data, "tick", path),
        bar=_require_field(data, "bar", path),
        track=_require_field(data, "track", path),
        event_type=_require_field(data, "event_type", path),
    )


def _plan_annotation_from_dict(data: Any, path: str) -> PlanAnnotation:
    if not isinstance(data, dict):
        raise PlanValidationError(path, f"must be object, got {type(data).__name__}")
    _reject_unknown_keys(data, PLAN_ANNOTATION_FIELDS, path)
    return PlanAnnotation(
        text=_require_field(data, "text", path),
        tick=_require_field(data, "tick", path),
        bar=_require_field(data, "bar", path),
        track=_require_field(data, "track", path),
        event_type=_require_field(data, "event_type", path),
        status=_require_field(data, "status", path),
        element_id=data.get("element_id"),
        reason=data.get("reason"),
    )


def _element_from_dict(data: dict[str, Any], path: str) -> Element:
    sa_raw = data.get("source_annotation")
    return Element(
        id=data["id"],
        role=data["role"],
        sections=list(data["sections"]),
        register=list(data["register"]),
        layers=data["layers"],
        sync_role=data["sync_role"],
        articulation=data["articulation"],
        harmony=data["harmony"],
        pattern=data.get("pattern"),
        degrees=list(data["degrees"]) if data.get("degrees") is not None else None,
        dynamics=data.get("dynamics"),
        instrument=data.get("instrument"),
        rationale=_require_field(data, "rationale", path),
        is_protagonist=data.get("is_protagonist", False),
        source_annotation=(
            _source_annotation_from_dict(sa_raw, f"{path}.source_annotation")
            if sa_raw is not None else None
        ),
    )


def _edit_from_dict(data: dict[str, Any]) -> PlanEdit:
    suggested = data.get("suggested_instrument")
    return PlanEdit(
        track=data["track"],
        profile=data["profile"],
        intensity=float(data["intensity"]),
        suggested_instrument=dict(suggested) if suggested is not None else None,
        tool=data.get("tool"),
    )


def _transition_from_dict(data: dict[str, Any]) -> Transition:
    return Transition(
        at_bar=data["at_bar"],
        from_section=data["from_section"],
        to_section=data["to_section"],
        dimensions_changed=list(data["dimensions_changed"]),
        elements=list(data["elements"]),
        technique=data["technique"],
    )


def _reject_unknown_keys(data: dict[str, Any], allowed: tuple[str, ...], path: str) -> None:
    for key in data:
        if key not in allowed:
            raise PlanValidationError(path if not path else f"{path}.{key}", f"unknown field {key!r}")


def _require_field(data: dict[str, Any], key: str, path: str) -> Any:
    if key not in data:
        raise PlanValidationError(f"{path}.{key}", "missing required field")
    return data[key]


def _style_technique_from_dict(data: dict[str, Any], path: str) -> StyleTechnique:
    if not isinstance(data, dict):
        raise PlanValidationError(path, f"must be object, got {type(data).__name__}")
    _reject_unknown_keys(data, STYLE_TECHNIQUE_FIELDS, path)
    return StyleTechnique(
        name=_require_field(data, "name", path),
        density=data.get("density"),
        rationale=data.get("rationale"),
        style=data.get("style"),
    )


def _family_style_from_dict(data: dict[str, Any], path: str) -> FamilyStyle:
    if not isinstance(data, dict):
        raise PlanValidationError(path, f"must be object, got {type(data).__name__}")
    _reject_unknown_keys(data, STYLE_FAMILY_REQUIRED_FIELDS, path)
    sources = _require_field(data, "sources", path)
    if not isinstance(sources, list):
        raise PlanValidationError(f"{path}.sources", f"must be list, got {type(sources).__name__}")
    techniques = _require_field(data, "techniques", path)
    if not isinstance(techniques, list):
        raise PlanValidationError(f"{path}.techniques", f"must be list, got {type(techniques).__name__}")
    parameters = _require_field(data, "parameters", path)
    if not isinstance(parameters, dict):
        raise PlanValidationError(f"{path}.parameters", f"must be dict, got {type(parameters).__name__}")
    return FamilyStyle(
        reference=_require_field(data, "reference", path),
        researched_at=_require_field(data, "researched_at", path),
        sources=list(sources),
        confidence=_require_field(data, "confidence", path),
        techniques=[
            _style_technique_from_dict(t, f"{path}.techniques[{i}]")
            for i, t in enumerate(techniques)
        ],
        parameters=dict(parameters),
    )


def _style_from_dict(data: Any) -> dict[str, FamilyStyle]:
    if not isinstance(data, dict):
        raise PlanValidationError("style", f"must be object, got {type(data).__name__}")
    _reject_musical_content_in_style_value(data, "style")
    return {
        family: _family_style_from_dict(entry, f"style.{family}")
        for family, entry in data.items()
    }


def from_dict(data: dict[str, Any]) -> ArrangementPlan:
    return ArrangementPlan(
        version=data["version"],
        seed=data["seed"],
        source_midi=_source_midi_from_dict(data["source_midi"]),
        route=data["route"],
        sections=[_section_from_dict(s) for s in data["sections"]],
        elements=[
            _element_from_dict(e, f"elements[{i}]")
            for i, e in enumerate(data["elements"])
        ],
        assumptions=list(data.get("assumptions", [])),
        transitions=[_transition_from_dict(t) for t in data.get("transitions", [])],
        edits=[_edit_from_dict(ed) for ed in data.get("edits", [])],
        style=_style_from_dict(data["style"]) if "style" in data else None,
        brief_ref=_brief_ref_from_dict(data["brief_ref"]) if "brief_ref" in data else None,
        annotations=[
            _plan_annotation_from_dict(a, f"annotations[{i}]")
            for i, a in enumerate(data.get("annotations", []))
        ],
        session=_session_from_dict(data["session"]) if "session" in data else None,
    )


# --- validacao contra o MIDI de origem --------------------------------------

def validate_edits_against_midi(
    plan: ArrangementPlan, track_names: list[str],
) -> None:
    """Rejeita edits que apontem para track inexistente no MIDI de origem.

    Roda quando o renderer ja tem `mido.MidiFile` do source em maos e passa
    a lista de nomes de track lidos das meta-messages. A mensagem de erro
    sugere o nome mais proximo via `difflib.get_close_matches` para que o
    usuario que digitou errado veja imediatamente a correcao.
    """
    import difflib

    available = [n for n in track_names if n]
    for i, ed in enumerate(plan.edits):
        if ed.track in available:
            continue
        suggestion = difflib.get_close_matches(ed.track, available, n=1, cutoff=0.4)
        hint = (
            f"; did you mean {suggestion[0]!r}?"
            if suggestion else
            f"; available tracks: {available!r}"
        )
        raise PlanValidationError(
            f"edits[{i}].track",
            f"track {ed.track!r} not found in source MIDI{hint}",
        )


# --- IO ---------------------------------------------------------------------

def dump(plan: ArrangementPlan, path: str | Path) -> None:
    """Valida o plano e serializa como JSON indentado em `path`.

    Falha da validacao aborta a escrita — nao existe half-written plan.
    """
    target = Path(path)
    validate(plan, plan_dir=target.parent)
    target.write_text(json.dumps(to_dict(plan), indent=2), encoding="utf-8")


def load(path: str | Path) -> ArrangementPlan:
    """Le e valida `arrangement-plan.json`.

    Falha da validacao aborta o load — quem chama recebe `PlanValidationError`
    com o path exato.
    """
    source = Path(path)
    data = json.loads(source.read_text(encoding="utf-8"))
    plan = from_dict(data)
    validate(plan, plan_dir=source.parent)
    return plan
