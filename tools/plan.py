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

HARMONY_MODES = ("follow_chords", "pedal", "free", "unison_guitar")

EDIT_PROFILES = ("bass", "drums", "keys", "generic")
EDIT_INTENSITY_MIN = 0.0
EDIT_INTENSITY_MAX = 1.0

STYLE_FAMILIES = ("bass", "drums", "guitar", "keys")
STYLE_CONFIDENCE_LEVELS = ("high", "medium", "low", "default")
STYLE_FAMILY_REQUIRED_FIELDS = (
    "reference",
    "researched_at",
    "sources",
    "confidence",
    "techniques",
    "parameters",
)
STYLE_TECHNIQUE_FIELDS = ("name", "density", "rationale")
STYLE_MUSICAL_CONTENT_KEYS = (
    "notes",
    "pattern",
    "riff",
    "melody",
    "groove",
    "sequence",
    "midi",
    "phrase",
    "lick",
    "motif",
)
STYLE_PITCH_KEYS = ("pitch", "note", "midi_note", "note_number")
STYLE_TIME_KEYS = ("time", "start", "start_tick", "tick", "ticks", "position", "offset")
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

MIDI_PITCH_MIN = 0
MIDI_PITCH_MAX = 127
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
    """
    track: str
    profile: str
    intensity: float


@dataclass
class StyleTechnique:
    name: str
    density: float | None = None
    rationale: str | None = None


@dataclass
class FamilyStyle:
    reference: str
    researched_at: str
    sources: list[str]
    confidence: str
    techniques: list[StyleTechnique]
    parameters: dict[str, float | list[float]]


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


def _resolve_style_technique(index, family: str, name: str, path: str):
    import difflib

    found = index.candidates(name)
    in_family = tuple(t for t in found if t.family == family)
    if in_family:
        return in_family[0]

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
    if _is_parameter_pair(value):
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


def _is_parameter_pair(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)
    )


def _raise_style_musical_content(path: str, reason: str) -> None:
    raise PlanValidationError(
        path,
        (
            f"{reason}; perfil de estilo aceita parametros de tecnica, "
            "nunca conteudo musical"
        ),
    )


def _object_has_pitch_and_time_keys(value: dict[str, Any]) -> bool:
    keys = set(value)
    return bool(keys.intersection(STYLE_PITCH_KEYS)) and bool(keys.intersection(STYLE_TIME_KEYS))


def _reject_musical_content_in_style_value(
    value: Any,
    path: str,
    *,
    allow_parameter_pair: bool = False,
) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            if key in STYLE_MUSICAL_CONTENT_KEYS:
                _raise_style_musical_content(
                    child_path,
                    f"campo de conteudo musical proibido {key!r}",
                )
            _reject_musical_content_in_style_value(
                child,
                child_path,
                allow_parameter_pair=path.endswith(".parameters"),
            )
        return

    if isinstance(value, list):
        if allow_parameter_pair and _is_parameter_pair(value):
            return
        if (
            len(value) >= 3
            and all(
                isinstance(item, int)
                and not isinstance(item, bool)
                and MIDI_PITCH_MIN <= item <= MIDI_PITCH_MAX
                for item in value
            )
        ):
            _raise_style_musical_content(
                path,
                "sequencia de tres ou mais inteiros em faixa MIDI proibida",
            )
        if any(isinstance(item, dict) and _object_has_pitch_and_time_keys(item) for item in value):
            _raise_style_musical_content(
                path,
                "array de eventos com altura e tempo proibido",
            )
        for i, item in enumerate(value):
            _reject_musical_content_in_style_value(item, f"{path}[{i}]")


def _validate_style(plan_style: Any) -> list[str]:
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
        _reject_musical_content_in_style_value(_family_style_to_dict(entry), base)
        _require_nonempty_str(entry.reference, f"{base}.reference")
        _require_nonempty_str(entry.researched_at, f"{base}.researched_at")
        try:
            date.fromisoformat(entry.researched_at)
        except ValueError:
            raise PlanValidationError(
                f"{base}.researched_at",
                f"must be ISO-8601 date string, got {entry.researched_at!r}",
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
            if technique_index is not None:
                resolved_techniques.append(_resolve_style_technique(
                    technique_index,
                    family,
                    technique.name,
                    f"{technique_base}.name",
                ))
        if not isinstance(entry.parameters, dict):
            raise PlanValidationError(
                f"{base}.parameters",
                f"must be dict of numbers, got {type(entry.parameters).__name__}",
            )
        for key, value in entry.parameters.items():
            if not isinstance(key, str) or not key:
                raise PlanValidationError(f"{base}.parameters", "parameter names must be non-empty strings")
            if _is_parameter_pair(value):
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


def validate(plan: ArrangementPlan) -> list[str]:
    """Valida um `ArrangementPlan` e devolve avisos nao-bloqueantes.

    Ordem: campos raiz -> source_midi -> sections -> elements. Section
    labels sao coletados antes de validar elements para que a checagem
    de referencia funcione mesmo sem sections declaradas em ordem
    especifica.

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

    _require_in(plan.route, ROUTES, "route")
    _require_nonempty_str(plan.source_midi.path, "source_midi.path")
    _require_nonempty_str(plan.source_midi.sha256, "source_midi.sha256")
    if plan.brief_ref is not None:
        _require_nonempty_str(plan.brief_ref.path, "brief_ref.path")
        if not isinstance(plan.brief_ref.sha256, str) or not SHA256_RE.fullmatch(plan.brief_ref.sha256):
            raise PlanValidationError(
                "brief_ref.sha256",
                "must be 64 lowercase hexadecimal characters",
            )
    warnings.extend(_validate_style(plan.style))

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


def _element_to_dict(e: Element) -> dict[str, Any]:
    return {
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


def _edit_to_dict(e: PlanEdit) -> dict[str, Any]:
    return {
        "track": e.track,
        "profile": e.profile,
        "intensity": float(e.intensity),
    }


def _style_technique_to_dict(t: StyleTechnique) -> dict[str, Any]:
    data: dict[str, Any] = {"name": t.name}
    if t.density is not None:
        data["density"] = float(t.density)
    if t.rationale is not None:
        data["rationale"] = t.rationale
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
    }
    if plan.style is not None:
        data["style"] = {
            family: _family_style_to_dict(entry)
            for family, entry in plan.style.items()
        }
    if plan.brief_ref is not None:
        data["brief_ref"] = _brief_ref_to_dict(plan.brief_ref)
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


def _element_from_dict(data: dict[str, Any], path: str) -> Element:
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
    )


def _edit_from_dict(data: dict[str, Any]) -> PlanEdit:
    return PlanEdit(
        track=data["track"],
        profile=data["profile"],
        intensity=float(data["intensity"]),
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
    validate(plan)
    Path(path).write_text(json.dumps(to_dict(plan), indent=2), encoding="utf-8")


def load(path: str | Path) -> ArrangementPlan:
    """Le e valida `arrangement-plan.json`.

    Falha da validacao aborta o load — quem chama recebe `PlanValidationError`
    com o path exato.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    plan = from_dict(data)
    validate(plan)
    return plan
