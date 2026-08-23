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
from dataclasses import dataclass, field
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

ENERGY_AXES = ("densidade", "impacto", "largura", "altura", "instabilidade")
ENERGY_MIN = 0
ENERGY_MAX = 10

SCHEMA_VERSION = 1

MIDI_PITCH_MIN = 0
MIDI_PITCH_MAX = 127


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
class ArrangementPlan:
    version: int
    seed: int
    source_midi: SourceMidi
    route: str
    sections: list[PlanSection]
    elements: list[Element]
    assumptions: list[str] = field(default_factory=list)
    transitions: list[Transition] = field(default_factory=list)
    edits: list[PlanEdit] = field(default_factory=list)


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
    return {
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


def _source_midi_from_dict(data: dict[str, Any]) -> SourceMidi:
    return SourceMidi(
        path=data["path"],
        sha256=data["sha256"],
        tempo=data.get("tempo"),
        key=data.get("key"),
        bars=data.get("bars"),
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


def _element_from_dict(data: dict[str, Any]) -> Element:
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
        rationale=data.get("rationale"),
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


def from_dict(data: dict[str, Any]) -> ArrangementPlan:
    return ArrangementPlan(
        version=data["version"],
        seed=data["seed"],
        source_midi=_source_midi_from_dict(data["source_midi"]),
        route=data["route"],
        sections=[_section_from_dict(s) for s in data["sections"]],
        elements=[_element_from_dict(e) for e in data["elements"]],
        assumptions=list(data.get("assumptions", [])),
        transitions=[_transition_from_dict(t) for t in data.get("transitions", [])],
        edits=[_edit_from_dict(ed) for ed in data.get("edits", [])],
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
