"""Renderer (US-016).

Consome `ArrangementPlan` + MIDI de origem e produz o MIDI de saida como
arquivo novo. Nunca sobrescreve o source. Preserva tempo, formula de
compasso, marcadores e as notas originais nota-a-nota (copia verbatim das
tracks do source via mido). Cada elemento do plano vira uma ou mais tracks
novas nomeadas pela convencao de US-013.

Nesta rodada (R1 — Motor) so o role `pad` gera tracks. Roles que ainda nao
existem no motor sao ignorados sem falha: entram no relatorio com nota
explicando que ficaram para rodadas seguintes, e nenhuma track e emitida
para eles. Isso mantem o pipeline end-to-end validavel antes da paleta
completa.

Determinismo:
- Copiar tracks do source verbatim (mensagem por mensagem) garante que as
  notas originais saem identicas.
- `mido.MidiFile.save` e deterministica dada a mesma representacao em
  memoria — dois renders com o mesmo plano e mesmo source produzem bytes
  identicos.
- Cada chamada de `generate_pad` recebe uma seed derivada de
  `sha256(plan.seed | element.id | section_label)`, entao mudanca de secao
  ou de elemento nao correlaciona o stagger entre eles, mas cada (plano,
  elemento, secao) e reprodutivel.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import mido
import pretty_midi

from .analyze import Analysis, analyze
from .edits import EditReport, apply_edits, collect_track_names
from .palette.harmonic import (
    DRONE_ROLES,
    KEYBOARD_ROLES,
    STRINGS_GHOST_RATIO,
    STRINGS_ROLES,
    STRINGS_TUTTI_MAX_VOICES,
    DroneNote,
    KeyboardNote,
    PadNote,
    StringsNote,
    check_tutti_uniqueness,
    generate_drone,
    generate_keyboard,
    generate_pad,
    generate_strings,
)
from .palette.rhythmic import (
    MOTOR_ROLES,
    RHYTHMIC_ROLES,
    SHADOW_ROLES,
    RhythmicNote,
    generate_motor,
    generate_rhythmic,
    generate_shadow,
)
from .plan import (
    ArrangementPlan,
    Element,
    PlanSection,
    load,
    validate_edits_against_midi,
)
from .plan import (
    validate as validate_plan,
)
from .tracks import name_for_element
from .validators.artifice import ArtificeIssue, validate_artifice
from .validators.artifice import format_issues as format_artifice_issues
from .validators.collision import CollisionReport, validate_collisions
from .validators.harmony import (
    HarmonyIssue,
    RenderedNote,
    RenderedTrack,
    format_issues,
    validate_harmony,
)
from .validators.persona import PersonaIssue, validate_persona
from .validators.persona import format_issues as format_persona_issues
from .validators.placement import PlacementIssue, validate_placement
from .validators.placement import format_issues as format_placement_issues

# --- constantes -------------------------------------------------------------

DEFAULT_OUTPUT_SUFFIX = "_arranged.mid"
DEFAULT_OUTPUT_DIRNAME = "Desktop"
PAD_CHANNEL = 0
KEYBOARD_CHANNEL = 0
STRINGS_CHANNEL = 0
DRONE_CHANNEL = 0
RHYTHMIC_CHANNEL = 0
MOTOR_CHANNEL = 0
SHADOW_CHANNEL = 0
SUSTAIN_CC = 64
EXPRESSION_CC = 11
KEYBOARD_PATTERN_FIELDS: frozenset[str] = frozenset({"use_sustain_cc64"})
STRINGS_PATTERN_FIELDS: frozenset[str] = frozenset({"tutti", "ghost_ratio"})
DRONE_PATTERN_FIELDS: frozenset[str] = frozenset({
    "pedal", "pedal_pitch", "filter_cycle_bars", "modulation_bars",
})
RHYTHMIC_PATTERN_FIELDS: frozenset[str] = frozenset({
    "pattern_bars", "mutate_every_bars", "interlock",
    "filter_cycle_bars", "velocity_cycle_bars", "custom_steps",
})
MOTOR_PATTERN_FIELDS: frozenset[str] = frozenset({
    "subdivision", "filter_cycle_bars", "velocity_cycle_bars", "custom_steps",
})
SHADOW_PATTERN_FIELDS: frozenset[str] = frozenset({
    "octave_shift", "tail_notes", "phrase_end_gap_s", "velocity_offset",
    "note_duration_s",
})


# --- excecoes ---------------------------------------------------------------

class RenderError(Exception):
    """Falha de render que nao pode ser silenciada."""


# --- dataclasses do relatorio ----------------------------------------------

@dataclass
class ElementRationale:
    """Justificativa musical de um elemento no relatorio final (FR-22)."""
    element_id: str
    role: str
    rationale: str
    plugin: str
    preset: str
    verified: bool
    layers: int
    sections: tuple[str, ...]
    rendered: bool
    note: str = ""


@dataclass
class RenderReport:
    output_path: Path
    source_sha256: str
    seed: int
    collision: CollisionReport
    elements: list[ElementRationale] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    harmony_issues: list[HarmonyIssue] = field(default_factory=list)
    placement_issues: list[PlacementIssue] = field(default_factory=list)
    artifice_issues: list[ArtificeIssue] = field(default_factory=list)
    persona_issues: list[PersonaIssue] = field(default_factory=list)
    edits: list[EditReport] = field(default_factory=list)


# --- IO helpers -------------------------------------------------------------

def sha256_of_file(path: Path) -> str:
    """SHA-256 do conteudo do arquivo em hex."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_source_path(plan: ArrangementPlan) -> Path:
    return Path(plan.source_midi.path).expanduser()


def _default_output_path(source: Path) -> Path:
    # `Path.home()` lido no momento da chamada — permite override via HOME em teste.
    return Path.home() / DEFAULT_OUTPUT_DIRNAME / f"{source.stem}{DEFAULT_OUTPUT_SUFFIX}"


def _section_by_label(plan: ArrangementPlan, label: str) -> PlanSection | None:
    for s in plan.sections:
        if s.label == label:
            return s
    return None


def _element_seed(plan_seed: int, element_id: str, section_label: str) -> int:
    """Seed determinstica por (plano, elemento, secao). Evita correlacao
    de stagger entre secoes de um mesmo elemento e entre elementos que
    dividem uma secao."""
    payload = f"{plan_seed}|{element_id}|{section_label}".encode()
    digest = hashlib.sha256(payload).digest()
    return int.from_bytes(digest[:8], "big", signed=False)


def _element_track_name(element: Element, layer_index: int, layers: int) -> str:
    """Nome de track — comum a pad e teclado. Reaproveita as guardas de
    FR-14/FR-24 e ASCII do name_for_element."""
    inst = element.instrument or {}
    plugin = inst.get("plugin")
    preset = inst.get("preset")
    verified = bool(inst.get("verified", False))
    if not plugin or not preset:
        raise RenderError(
            f"element {element.id!r} missing instrument.plugin/preset — cannot name track"
        )
    display = element.id if layers == 1 else f"{element.id} L{layer_index + 1}"
    return name_for_element(display, element.role, str(plugin), str(preset), verified)


def _pattern_fields_for_role(role: str) -> frozenset[str]:
    if role == "pad":
        return frozenset()
    if role in KEYBOARD_ROLES:
        return KEYBOARD_PATTERN_FIELDS
    if role in STRINGS_ROLES:
        return STRINGS_PATTERN_FIELDS
    if role in DRONE_ROLES:
        return DRONE_PATTERN_FIELDS
    if role in RHYTHMIC_ROLES:
        return RHYTHMIC_PATTERN_FIELDS
    if role in MOTOR_ROLES:
        return MOTOR_PATTERN_FIELDS
    if role in SHADOW_ROLES:
        return SHADOW_PATTERN_FIELDS
    return frozenset()


def _unsupported_pattern_warnings(element: Element) -> list[str]:
    """Avisos para campos de `element.pattern` que o renderer nao consome.

    O plano e editavel a mao; um campo sem efeito precisa aparecer no
    relatorio em vez de ser aceito em silencio.
    """
    pattern = element.pattern or {}
    known = _pattern_fields_for_role(element.role)
    return [
        f"{element.id}: element.pattern.{key} is not supported for "
        f"role {element.role!r}; ignored"
        for key in sorted(pattern)
        if key not in known
    ]


# --- conversao note -> mido -------------------------------------------------

def _notes_to_track(
    notes: (
        list[PadNote] | list[KeyboardNote] | list[StringsNote]
        | list[DroneNote] | list[RhythmicNote]
    ),
    pm: pretty_midi.PrettyMIDI,
    track_name: str,
    channel: int,
    cc_events: list[tuple[float, int, int]] | None = None,
) -> mido.MidiTrack:
    """Converte lista de notas (segundos absolutos) em `mido.MidiTrack`
    com deltas em ticks. Aceita notas duck-typed com `pitch`, `velocity`,
    `start_s`, `end_s` (PadNote, KeyboardNote, StringsNote e DroneNote
    satisfazem).
    Determinstica: ordena eventos por (tick, tipo, cc/pitch, valor).

    `cc_events` e lista de `(time_s, cc_number, value)` — CC64 do teclado
    e CC11 das strings vem por aqui. CC vem ANTES de note_on e DEPOIS de
    note_off no mesmo tick, para o motor de sustain do plugin nao
    capturar a nota que estava tentando finalizar."""
    tr = mido.MidiTrack()
    tr.append(mido.MetaMessage("track_name", name=track_name, time=0))

    # kind: 0 = note_off, 1 = cc, 2 = note_on. Ordem no mesmo tick:
    # note_off (fecha nota anterior) -> cc (mudanca de estado) -> note_on
    # (dispara nova nota). Isso evita que um CC64 down engula o note_off
    # da nota que acabou de fechar.
    events: list[tuple[int, int, int, int]] = []
    for n in notes:
        start_tick = int(round(pm.time_to_tick(n.start_s)))
        end_tick = int(round(pm.time_to_tick(n.end_s)))
        if end_tick <= start_tick:
            end_tick = start_tick + 1
        events.append((start_tick, 2, int(n.pitch), int(n.velocity)))
        events.append((end_tick, 0, int(n.pitch), 0))
    if cc_events:
        for time_s, cc_num, value in cc_events:
            tick = int(round(pm.time_to_tick(time_s)))
            events.append((tick, 1, int(cc_num), int(value)))
    events.sort()

    prev_tick = 0
    for tick, kind, pitch_or_cc, vel_or_value in events:
        delta = tick - prev_tick
        if kind == 2:
            tr.append(mido.Message(
                "note_on", channel=channel, note=pitch_or_cc,
                velocity=vel_or_value, time=delta,
            ))
        elif kind == 1:
            tr.append(mido.Message(
                "control_change", channel=channel, control=pitch_or_cc,
                value=vel_or_value, time=delta,
            ))
        else:
            tr.append(mido.Message(
                "note_off", channel=channel, note=pitch_or_cc,
                velocity=0, time=delta,
            ))
        prev_tick = tick
    return tr


def _clone_source_tracks(src: mido.MidiFile) -> list[mido.MidiTrack]:
    """Copia cada track do source em `MidiTrack` novo com mensagens copiadas.
    Preserva ordem e conteudo — as tracks originais saem intactas."""
    out: list[mido.MidiTrack] = []
    for tr in src.tracks:
        new = mido.MidiTrack()
        for msg in tr:
            new.append(msg.copy())
        out.append(new)
    return out


# --- geradores por role -----------------------------------------------------

def _layers_to_tracks(
    element: Element,
    layer_notes: (
        list[list[PadNote]]
        | list[list[KeyboardNote]]
        | list[list[StringsNote]]
        | list[list[DroneNote]]
        | list[list[RhythmicNote]]
    ),
    pm: pretty_midi.PrettyMIDI,
    channel: int,
    layer_ccs: list[list[tuple[float, int, int]]] | None = None,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Converte as notas acumuladas por layer em MidiTrack + RenderedTrack.

    Compartilhado entre pad, teclado e strings — a diferenca esta so em
    `layer_ccs` (CC64 do teclado, CC11 das strings, ambos como
    `(time_s, cc, value)`). Assinatura duck-typed: qualquer nota com
    pitch/velocity/start_s/end_s serve."""
    midi_tracks: list[mido.MidiTrack] = []
    rendered: list[RenderedTrack] = []
    for i, notes in enumerate(layer_notes):
        name = _element_track_name(element, i, element.layers)
        ccs = layer_ccs[i] if layer_ccs is not None else None
        midi_tracks.append(_notes_to_track(
            notes, pm, name, channel, cc_events=ccs or None,
        ))
        rendered.append(RenderedTrack(
            element_id=element.id,
            track_name=name,
            notes=tuple(
                RenderedNote(
                    pitch=n.pitch, velocity=n.velocity,
                    start_s=n.start_s, end_s=n.end_s,
                )
                for n in notes
            ),
        ))
    return midi_tracks, rendered


def _iter_element_sections(
    element: Element, plan: ArrangementPlan,
) -> list[tuple[PlanSection, int]]:
    """Devolve pares (secao, seed) para cada label de `element.sections` que
    resolve para uma secao declarada no plano. Guarda defensiva — o
    plan.validate ja rejeita labels desconhecidos, mas o renderer ignora em
    silencio caso o plano tenha sido mutado em teste."""
    result: list[tuple[PlanSection, int]] = []
    for label in element.sections:
        section = _section_by_label(plan, label)
        if section is None:
            continue
        seed = _element_seed(plan.seed, element.id, label)
        result.append((section, seed))
    return result


def _render_keyboard_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de piano ou Rhodes. Uma track por layer; notas de todas
    as secoes concatenadas na mesma layer. CC64 opcional por elemento via
    `element.pattern['use_sustain_cc64']` (default False — spec 'nao e
    default')."""
    layer_notes: list[list[KeyboardNote]] = [[] for _ in range(element.layers)]
    layer_ccs: list[list[tuple[float, int, int]]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}
    pattern = element.pattern or {}
    use_sustain = bool(pattern.get("use_sustain_cc64", False))

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_keyboard(
            analysis,
            section,
            role=element.role,
            register=register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=dyn,
            use_sustain_cc64=use_sustain,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)
            for p in layer.pedal_events:
                layer_ccs[i].append((p.time_s, SUSTAIN_CC, p.value))

    return _layers_to_tracks(element, layer_notes, pm, channel, layer_ccs)


def _render_strings_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de strings ou choir. Cada layer e uma voz independente
    da linha — a AC pede attack diferente por voz + CC11 por voz. Voice
    count = `element.layers` (para strings o vocabulario 'layers' significa
    'vozes independentes', nao unisono). `tutti` e `ghost_ratio` sao lidos
    de `element.pattern`."""
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}
    pattern = element.pattern or {}
    tutti = bool(pattern.get("tutti", False))
    voices = min(element.layers, STRINGS_TUTTI_MAX_VOICES) if tutti else element.layers
    layer_notes: list[list[StringsNote]] = [[] for _ in range(voices)]
    layer_ccs: list[list[tuple[float, int, int]]] = [[] for _ in range(voices)]
    ghost_ratio = float(pattern.get("ghost_ratio", STRINGS_GHOST_RATIO))

    for section, seed in _iter_element_sections(element, plan):
        section_voices = generate_strings(
            analysis,
            section,
            role=element.role,
            register=register,
            voices=voices,
            articulation=element.articulation,
            dynamics=dyn,
            tutti=tutti,
            ghost_ratio=ghost_ratio,
            seed=seed,
        )
        for i, voice in enumerate(section_voices):
            layer_notes[i].extend(voice.notes)
            for ev in voice.expression_events:
                layer_ccs[i].append((ev.time_s, EXPRESSION_CC, ev.value))

    return _layers_to_tracks(element, layer_notes, pm, channel, layer_ccs)


def _strings_tutti_layer_warning(element: Element) -> str | None:
    pattern = element.pattern or {}
    if (
        element.role in STRINGS_ROLES
        and bool(pattern.get("tutti", False))
        and element.layers > STRINGS_TUTTI_MAX_VOICES
    ):
        return (
            f"{element.id}: element.layers={element.layers} reduced to "
            f"{STRINGS_TUTTI_MAX_VOICES} for strings tutti"
        )
    return None


def _accumulate_cc_layers(
    layer_notes: list[list],
    layer_ccs: list[list[tuple[float, int, int]]],
    layers,
) -> None:
    """Estende `layer_notes` e `layer_ccs` in-place com as notas e CC events
    de `layers`. Compartilhado entre drone e rhythmic — ambos produzem
    `Layer(notes, cc_events)` com eventos ja carregando (time_s, cc, value)."""
    for i, layer in enumerate(layers):
        layer_notes[i].extend(layer.notes)
        for ev in layer.cc_events:
            layer_ccs[i].append((ev.time_s, ev.cc, ev.value))


@dataclass
class _RhythmicElementContext:
    """Prelude compartilhado por _render_rhythmic_element e
    _render_motor_element — mesmo shape de buckets, mesmo unpack de
    `element` (register/dyn/pattern) e mesmos defaults dos knobs comuns
    de padrao (filter_cycle_bars, velocity_cycle_bars, custom_steps)."""
    layer_notes: list[list[RhythmicNote]]
    layer_ccs: list[list[tuple[float, int, int]]]
    register: tuple[int, int]
    dyn: dict
    pattern: dict
    filter_cycle_bars: int
    velocity_cycle_bars: int
    custom_steps: object


def _rhythmic_element_context(element: Element) -> _RhythmicElementContext:
    pattern = element.pattern or {}
    return _RhythmicElementContext(
        layer_notes=[[] for _ in range(element.layers)],
        layer_ccs=[[] for _ in range(element.layers)],
        register=(int(element.register[0]), int(element.register[1])),
        dyn=element.dynamics or {},
        pattern=pattern,
        filter_cycle_bars=int(pattern.get("filter_cycle_bars", 4)),
        velocity_cycle_bars=int(pattern.get("velocity_cycle_bars", 2)),
        custom_steps=pattern.get("custom_steps"),
    )


def _drive_rhythmic_layers(
    ctx: _RhythmicElementContext,
    element: Element,
    plan: ArrangementPlan,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
    per_section,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Loop compartilhado de rhythmic/motor: itera secoes, chama
    `per_section(section, seed)` (deve devolver a lista de layers do
    gerador), acumula CCs e finaliza via `_layers_to_tracks`."""
    for section, seed in _iter_element_sections(element, plan):
        layers = per_section(section, seed)
        _accumulate_cc_layers(ctx.layer_notes, ctx.layer_ccs, layers)
    return _layers_to_tracks(
        element, ctx.layer_notes, pm, channel, ctx.layer_ccs,
    )


def _render_drone_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de drone / nota-pedal. Uma track por layer; notas
    unicas sustentadas por secao. Modo pedal via `element.pattern['pedal']`
    desativa CC74/CC11. `pattern['pedal_pitch']` seleciona tonica ou
    quinta; `pattern['filter_cycle_bars']` e `pattern['modulation_bars']`
    controlam os ciclos dos CCs."""
    layer_notes: list[list[DroneNote]] = [[] for _ in range(element.layers)]
    layer_ccs: list[list[tuple[float, int, int]]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    pattern = element.pattern or {}
    pedal = bool(pattern.get("pedal", False))
    pedal_pitch = str(pattern.get("pedal_pitch", "tonic"))
    filter_cycle_bars = int(
        pattern.get("filter_cycle_bars", 4),
    )
    modulation_bars = int(
        pattern.get("modulation_bars", 16),
    )

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_drone(
            analysis,
            section,
            register=register,
            layers=element.layers,
            pedal=pedal,
            pedal_pitch=pedal_pitch,
            filter_cycle_bars=filter_cycle_bars,
            modulation_bars=modulation_bars,
            seed=seed,
        )
        _accumulate_cc_layers(layer_notes, layer_ccs, layers)

    return _layers_to_tracks(element, layer_notes, pm, channel, layer_ccs)


def _render_rhythmic_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de arp / rhythmic_machine. Uma track por layer; notas de
    todas as secoes concatenadas na mesma layer. Controles via
    `element.pattern`: `pattern_bars` (1 ou 2), `mutate_every_bars` (4/8 ou
    None), `interlock` (bool), `filter_cycle_bars`, `velocity_cycle_bars`,
    `custom_steps` (padrao 0/1 opcional)."""
    ctx = _rhythmic_element_context(element)
    pattern_bars = int(ctx.pattern.get("pattern_bars", 1))
    mutate_raw = ctx.pattern.get("mutate_every_bars")
    mutate_every_bars = int(mutate_raw) if mutate_raw is not None else None
    interlock = bool(ctx.pattern.get("interlock", False))

    def per_section(section, seed):
        return generate_rhythmic(
            analysis, section,
            role=element.role,
            register=ctx.register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=ctx.dyn,
            pattern_bars=pattern_bars,
            mutate_every_bars=mutate_every_bars,
            interlock=interlock,
            filter_cycle_bars=ctx.filter_cycle_bars,
            velocity_cycle_bars=ctx.velocity_cycle_bars,
            custom_steps=ctx.custom_steps,
            seed=seed,
        )

    return _drive_rhythmic_layers(ctx, element, plan, pm, channel, per_section)


def _render_motor_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de motor. Uma track por layer; notas de todas as
    secoes concatenadas na mesma layer. Controles via `element.pattern`:
    `subdivision` (eighth/sixteenth), `filter_cycle_bars`,
    `velocity_cycle_bars`, `custom_steps` (padrao 0/1 opcional)."""
    ctx = _rhythmic_element_context(element)
    subdivision = str(ctx.pattern.get("subdivision", "sixteenth"))

    def per_section(section, seed):
        return generate_motor(
            analysis, section,
            role=element.role,
            subdivision=subdivision,
            register=ctx.register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=ctx.dyn,
            filter_cycle_bars=ctx.filter_cycle_bars,
            velocity_cycle_bars=ctx.velocity_cycle_bars,
            custom_steps=ctx.custom_steps,
            seed=seed,
        )

    return _drive_rhythmic_layers(ctx, element, plan, pm, channel, per_section)


def _render_shadow_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera tracks de shadow — dobra o fim de cada frase de guitarra.
    Controles via `element.pattern`: `octave_shift` (+/-12),
    `tail_notes`, `phrase_end_gap_s`, `velocity_offset`,
    `note_duration_s`."""
    layer_notes: list[list[RhythmicNote]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}
    pattern = element.pattern or {}
    octave_shift = int(pattern.get("octave_shift", 12))
    tail_notes = int(pattern.get("tail_notes", 2))
    phrase_end_gap_s = float(pattern.get("phrase_end_gap_s", 0.5))
    velocity_offset = int(pattern.get("velocity_offset", -25))
    note_duration_s = float(pattern.get("note_duration_s", 0.35))

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_shadow(
            analysis,
            section,
            role=element.role,
            register=register,
            layers=element.layers,
            articulation=element.articulation,
            dynamics=dyn,
            octave_shift=octave_shift,
            tail_notes=tail_notes,
            phrase_end_gap_s=phrase_end_gap_s,
            velocity_offset=velocity_offset,
            note_duration_s=note_duration_s,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


def _render_pad_element(
    element: Element,
    plan: ArrangementPlan,
    analysis: Analysis,
    pm: pretty_midi.PrettyMIDI,
    channel: int,
) -> tuple[list[mido.MidiTrack], list[RenderedTrack]]:
    """Gera as tracks de um elemento pad. Uma track por layer; notas de todas
    as secoes concatenadas na mesma layer.

    Devolve tambem `RenderedTrack`s espelhando cada track emitida — insumo
    do validador harmonico (`validate_harmony`)."""
    layer_notes: list[list[PadNote]] = [[] for _ in range(element.layers)]
    register = (int(element.register[0]), int(element.register[1]))
    dyn = element.dynamics or {}

    for section, seed in _iter_element_sections(element, plan):
        layers = generate_pad(
            analysis,
            section,
            register=register,
            layers=element.layers,
            dynamics=dyn,
            seed=seed,
        )
        for i, layer in enumerate(layers):
            layer_notes[i].extend(layer.notes)

    return _layers_to_tracks(element, layer_notes, pm, channel)


_RenderElementFn = Callable[
    [Element, ArrangementPlan, Analysis, pretty_midi.PrettyMIDI, int],
    tuple[list[mido.MidiTrack], list[RenderedTrack]],
]


@dataclass(frozen=True)
class _RoleRenderer:
    render: _RenderElementFn
    channel: int


def _build_role_renderers() -> dict[str, _RoleRenderer]:
    return {
        "pad": _RoleRenderer(_render_pad_element, PAD_CHANNEL),
        **{
            role: _RoleRenderer(_render_keyboard_element, KEYBOARD_CHANNEL)
            for role in KEYBOARD_ROLES
        },
        **{
            role: _RoleRenderer(_render_strings_element, STRINGS_CHANNEL)
            for role in STRINGS_ROLES
        },
        **{
            role: _RoleRenderer(_render_drone_element, DRONE_CHANNEL)
            for role in DRONE_ROLES
        },
        **{
            role: _RoleRenderer(_render_rhythmic_element, RHYTHMIC_CHANNEL)
            for role in RHYTHMIC_ROLES
        },
        **{
            role: _RoleRenderer(_render_motor_element, MOTOR_CHANNEL)
            for role in MOTOR_ROLES
        },
        **{
            role: _RoleRenderer(_render_shadow_element, SHADOW_CHANNEL)
            for role in SHADOW_ROLES
        },
    }


_ROLE_RENDERERS = _build_role_renderers()
SUPPORTED_ROLES: frozenset[str] = frozenset(_ROLE_RENDERERS)


# --- API principal ----------------------------------------------------------

PlanArg = ArrangementPlan | str | Path


def render(
    plan: PlanArg,
    output_path: str | Path | None = None,
    *,
    source_path: str | Path | None = None,
    strict_persona: bool = False,
) -> RenderReport:
    """Renderiza `plan` sobre o MIDI de origem em um arquivo novo.

    Args:
      plan: `ArrangementPlan` ja carregado ou caminho para `arrangement-plan.json`.
      output_path: caminho de saida. Default: `~/Desktop/<name>_arranged.mid`.
      source_path: override do caminho de origem. Default: `plan.source_midi.path`.

    Raises:
      RenderError: source inexistente, output apontaria para o source, ou
        elemento pad sem instrument.plugin/preset.
      PlanValidationError: quando `plan` e invalido, vindo de caminho ou
        construido em memoria.

    Efeitos: cria diretorio-pai do output se nao existir. Nunca modifica o
    source. Pode mutar `Element.register` do plano em memoria via validator
    de colisao (mesma semantica de `validate_collisions`).
    """
    if not isinstance(plan, ArrangementPlan):
        plan = load(plan)
    validate_plan(plan)

    src = Path(source_path).expanduser() if source_path else _resolve_source_path(plan)
    if not src.exists():
        raise RenderError(f"source MIDI not found: {src}")

    out_path = Path(output_path).expanduser() if output_path else _default_output_path(src)
    if out_path.resolve() == src.resolve():
        raise RenderError(f"output would overwrite source: {out_path}")

    source_hash = sha256_of_file(src)
    warnings: list[str] = []
    if plan.source_midi.sha256 and plan.source_midi.sha256 != source_hash:
        warnings.append(
            f"source_midi.sha256 mismatch (plan={plan.source_midi.sha256[:12]}..., "
            f"file={source_hash[:12]}...); rendering anyway"
        )

    pm = pretty_midi.PrettyMIDI(str(src))
    analysis = analyze(str(src))

    collision_report = validate_collisions(plan)

    # charset: usa o default do mido (latin-1), que e o que a spec MIDI 1.0
    # assume para meta-texto. Forcar utf-8 aqui gravava bytes >127 que o
    # decoder do DAW nao e obrigado a entender — ver o bloco de constantes
    # em tracks.py. Todo nome emitido por name_for_element ja e ASCII.
    src_mid = mido.MidiFile(str(src))
    out_mid = mido.MidiFile(
        ticks_per_beat=src_mid.ticks_per_beat,
        type=src_mid.type,
    )
    out_mid.tracks.extend(_clone_source_tracks(src_mid))

    # Edits opt-in (FR-28): humaniza tracks nomeadas em `plan.edits` in-place
    # nas tracks ja clonadas. Tracks nao nomeadas ficam byte-identicas.
    # Validacao de existencia de track roda AQUI (nao em plan.py) porque
    # so aqui temos o MIDI carregado.
    edit_reports: list[EditReport] = []
    if plan.edits:
        validate_edits_against_midi(plan, collect_track_names(out_mid.tracks))
        edit_reports = apply_edits(
            list(out_mid.tracks), plan.edits, plan.seed, pm,
        )

    element_reports: list[ElementRationale] = []
    rendered_tracks: list[RenderedTrack] = []
    for e in plan.elements:
        warnings.extend(_unsupported_pattern_warnings(e))
        layer_warning = _strings_tutti_layer_warning(e)
        if layer_warning is not None:
            warnings.append(layer_warning)
        inst = e.instrument or {}
        report_entry = ElementRationale(
            element_id=e.id,
            role=e.role,
            rationale=e.rationale or "",
            plugin=str(inst.get("plugin", "")),
            preset=str(inst.get("preset", "")),
            verified=bool(inst.get("verified", False)),
            layers=e.layers,
            sections=tuple(e.sections),
            rendered=False,
        )
        role_renderer = _ROLE_RENDERERS.get(e.role)
        if role_renderer is not None:
            midi_tracks, rendered = role_renderer.render(
                e, plan, analysis, pm, role_renderer.channel,
            )
            out_mid.tracks.extend(midi_tracks)
            rendered_tracks.extend(rendered)
            report_entry.rendered = True
        else:
            msg = (
                f"role {e.role!r} not implemented in R1 motor — element skipped "
                f"(no track emitted)"
            )
            report_entry.note = msg
            warnings.append(f"{e.id}: {msg}")
        element_reports.append(report_entry)

    warnings.extend(check_tutti_uniqueness(plan))

    harmony_issues = validate_harmony(rendered_tracks, plan, analysis)
    placement_issues = validate_placement(rendered_tracks, plan, analysis)
    artifice_issues = validate_artifice(rendered_tracks, plan, analysis)
    persona_issues = validate_persona(
        plan, rendered_tracks, analysis, strict=strict_persona,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_mid.save(str(out_path))

    return RenderReport(
        output_path=out_path,
        source_sha256=source_hash,
        seed=plan.seed,
        collision=collision_report,
        elements=element_reports,
        warnings=warnings,
        harmony_issues=harmony_issues,
        placement_issues=placement_issues,
        artifice_issues=artifice_issues,
        persona_issues=persona_issues,
        edits=edit_reports,
    )


# --- relatorio pretty-print (consumido pelo CLI de US-017) -----------------

def format_render_report(report: RenderReport) -> str:
    """Pretty-print do relatorio, com rationale por elemento (FR-22) e o
    resultado do validador de colisao."""
    lines = [
        f"Rendered: {report.output_path}",
        f"Source sha256: {report.source_sha256}",
        f"Seed: {report.seed}",
        "",
        "Elements:",
    ]
    for r in report.elements:
        marker = "✓" if r.verified else "?"
        status = "rendered" if r.rendered else "skipped"
        lines.append(
            f"  - {r.element_id} ({r.role}, {status}, layers={r.layers}): "
            f"{r.plugin} / {r.preset} {marker}"
        )
        if r.rationale:
            lines.append(f"      rationale: {r.rationale}")
        if r.note:
            lines.append(f"      note: {r.note}")
    if report.collision.relocations:
        lines.append("")
        lines.append("Collision relocations:")
        for rel in report.collision.relocations:
            lines.append(
                f"  - {rel.element_id} @ {rel.section_label}: "
                f"{rel.from_register} -> {rel.to_register} ({rel.reason})"
            )
    if report.collision.warnings:
        lines.append("")
        lines.append("Collision warnings:")
        for w in report.collision.warnings:
            lines.append(
                f"  - {' + '.join(w.element_ids)} @ {w.section_label} "
                f"bars {w.bar_range} ({w.band}): {w.reason}"
            )
    if report.edits:
        lines.append("")
        lines.append("Edits applied:")
        for ed in report.edits:
            tracks_label = (
                "track"
                if ed.tracks_matched == 1
                else "tracks"
            )
            lines.append(
                f"  - {ed.track} (profile={ed.profile}, intensity={ed.intensity:.2f}): "
                f"{ed.notes_touched} notes across {ed.tracks_matched} {tracks_label}, "
                f"mean offset {ed.mean_offset_ms:+.2f}ms"
            )
    if report.warnings:
        lines.append("")
        lines.append("Render warnings:")
        for w in report.warnings:
            lines.append(f"  - {w}")
    lines.append("")
    lines.append(format_issues(report.harmony_issues))
    lines.append("")
    lines.append(format_placement_issues(report.placement_issues))
    lines.append("")
    lines.append(format_artifice_issues(report.artifice_issues))
    lines.append("")
    lines.append(format_persona_issues(report.persona_issues))
    return "\n".join(lines)


__all__ = [
    "DEFAULT_OUTPUT_SUFFIX",
    "ElementRationale",
    "PAD_CHANNEL",
    "RenderError",
    "RenderReport",
    "SUPPORTED_ROLES",
    "format_render_report",
    "render",
    "sha256_of_file",
]
