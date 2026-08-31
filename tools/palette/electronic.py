"""Geradores de elementos ritmicos eletronicos (issue #22).

`hat_elec`, `sub` e `sub_drop`. Todo numero que caracteriza o comportamento
vem de `knowledge/tecnicas/tecnicas_eletronico_midi.md`, lido via
`tools.techniques.index.build_index()` — nunca hardcoded aqui (AGENTS.md:
"parametro mentiroso" e a mesma categoria de vicio que `_identity_apply`).

`perc_elec` e `vox_chop` (tambem previstos na issue #22) NAO estao
implementados nesta rodada — ver corpo do PR para o motivo do corte de
escopo. Nao ha bloco de placeholder para eles neste modulo nem no manual.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, replace

from ..analyze import Analysis, BarAnalysis, find_bar
from ..constants import VELOCITY_RANGES
from ..plan import PlanSection
from ..techniques.index import TechniqueError, build_index
from ..validators.harmony import degrees_pcs

# --- schema de saida ---------------------------------------------------------
# Reexportado de .rhythmic para nao duplicar a dataclass — mesma nota
# (pitch/velocity/start_s/end_s) que arp/motor/shadow ja usam no seam de
# render (`_notes_to_track` e duck-typed sobre esses quatro campos).
from .rhythmic import MIN_NOTE_DURATION_S, STEPS_PER_BAR, RhythmicLayer, RhythmicNote

HAT_ELEC_ROLES: tuple[str, ...] = ("hat_elec",)
SUB_ROLES: tuple[str, ...] = ("sub",)
SUB_DROP_ROLES: tuple[str, ...] = ("sub_drop",)

HAT_PATTERN_MODES: tuple[str, ...] = ("sixteenth", "gaps", "half_time")
SUB_FOLLOW_MODES: tuple[str, ...] = ("tonic", "kick", "riff")

# Grade de STEPS_PER_BAR (16) passos por bar, 1 = hit ativo.
_HAT_SIXTEENTH_PATTERN: tuple[int, ...] = (1,) * STEPS_PER_BAR
# Lacuna no ultimo dezesseis-avos de cada tempo — "16as com lacunas" do AC.
_HAT_GAPS_PATTERN: tuple[int, ...] = (1, 1, 1, 0) * 4
# Colcheias: metade da densidade do continuo — "half-time" do AC.
_HAT_HALF_TIME_PATTERN: tuple[int, ...] = (1, 0) * 8


class ElectronicGeneratorError(ValueError):
    """Parametro de plano invalido para um gerador eletronico."""


def _hat_pattern(mode: str) -> tuple[int, ...]:
    if mode == "sixteenth":
        return _HAT_SIXTEENTH_PATTERN
    if mode == "gaps":
        return _HAT_GAPS_PATTERN
    if mode == "half_time":
        return _HAT_HALF_TIME_PATTERN
    raise ElectronicGeneratorError(
        f"pattern_mode must be one of {HAT_PATTERN_MODES}; got {mode!r}"
    )


def _technique_params(canonical: str) -> dict[str, object]:
    """Le os parametros de uma tecnica do manual pelo indice — nunca
    hardcoded. `value` tem precedencia sobre `range` quando os dois
    existirem (nenhum parametro deste manual declara os dois)."""
    idx = build_index()
    t = idx.get(canonical)
    if t is None:
        raise TechniqueError(
            f"manual de tecnicas nao declara {canonical!r} — "
            f"knowledge/tecnicas/tecnicas_eletronico_midi.md esta ausente ou incompleto"
        )
    out: dict[str, object] = {}
    for p in t.parameters:
        out[p.name] = p.value if p.value is not None else p.range
    return out


def bars_in_section(section: PlanSection, analysis: Analysis) -> list[BarAnalysis]:
    """Bars de `analysis` cobertos por `section`. Compartilhado pelos tres
    geradores deste modulo e por `tools.render` (fronteira de secao do
    sub_drop e o inicio do primeiro bar aqui devolvido)."""
    return [
        b for b in analysis.bars
        if section.start_bar <= b.index < section.end_bar
    ]


def _enforce_monophony(notes: list[RhythmicNote]) -> list[RhythmicNote]:
    """Corta o fim de cada nota para nunca ultrapassar o onset da proxima.

    Offset por nota pode empurrar dois hits um perto do outro; a garantia
    de 'zero overlap' e ESTRUTURAL aqui (clamp determinístico), nao apenas
    estatistica (offset pequeno o bastante para raramente colidir)."""
    out: list[RhythmicNote] = []
    for i, n in enumerate(notes):
        end = n.end_s
        if i + 1 < len(notes):
            next_start = notes[i + 1].start_s
            if end > next_start:
                end = max(n.start_s + MIN_NOTE_DURATION_S, next_start - MIN_NOTE_DURATION_S)
        out.append(replace(n, end_s=end))
    return out


# --- hat_elec -----------------------------------------------------------------

def generate_hat_elec(
    analysis: Analysis,
    section: PlanSection,
    *,
    layers: int = 1,
    pattern_mode: str = "sixteenth",
    seed: int,
) -> list[RhythmicLayer]:
    """Hi-hat eletronico: pitch fixo, gate de semicolcheia escalando com o
    BPM real do arquivo, velocity ~N(95, 8) clampada em [79, 113], offset
    ~N(-4, 8)ms clampado em +-20ms. 100% monofonico — ver `_enforce_monophony`.
    """
    params = _technique_params("drums.hat_elec")
    pitch = int(params["pitch"])
    reference_bpm = float(params["reference_bpm"])
    gate_lo_ms, gate_hi_ms = params["gate_ms_at_reference_bpm"]
    vel_lo, vel_hi = params["velocity_range"]
    vel_mean = float(params["velocity_mean"])
    vel_stdev = float(params["velocity_stdev"])
    off_lo, off_hi = params["offset_range_ms"]
    off_bias = float(params["offset_bias_ms"])
    off_stdev = float(params["offset_stdev_ms"])

    # Passo de referencia (16as a `reference_bpm`) em ms; o gate medido e
    # expresso como fracao desse passo e reaplicado ao passo REAL do
    # arquivo — e assim que o gate escala com o BPM real (AC da issue #22).
    reference_step_ms = 60_000.0 / reference_bpm / 4.0
    gate_ratio_lo = gate_lo_ms / reference_step_ms
    gate_ratio_hi = gate_hi_ms / reference_step_ms

    pattern = _hat_pattern(pattern_mode)
    bars = bars_in_section(section, analysis)

    result: list[RhythmicLayer] = []
    for layer_idx in range(max(1, layers)):
        rng = random.Random(seed + (layer_idx + 1) * 7_919)
        notes: list[RhythmicNote] = []
        for bar in bars:
            bar_dur_s = bar.end - bar.start
            step_dur_s = bar_dur_s / STEPS_PER_BAR
            for step_i, active in enumerate(pattern):
                if not active:
                    continue
                raw_onset = bar.start + step_i * step_dur_s
                offset_ms = rng.gauss(off_bias, off_stdev)
                offset_ms = max(off_lo, min(off_hi, offset_ms))
                onset = max(0.0, raw_onset + offset_ms / 1000.0)
                gate_ratio = rng.uniform(gate_ratio_lo, gate_ratio_hi)
                dur_s = max(MIN_NOTE_DURATION_S, step_dur_s * gate_ratio)
                velocity_f = rng.gauss(vel_mean, vel_stdev)
                velocity = int(round(max(vel_lo, min(vel_hi, velocity_f))))
                notes.append(RhythmicNote(
                    pitch=pitch, velocity=velocity,
                    start_s=onset, end_s=onset + dur_s,
                ))
        notes.sort(key=lambda n: n.start_s)
        notes = _enforce_monophony(notes)
        result.append(RhythmicLayer(index=layer_idx, notes=tuple(notes)))
    return result


# --- sub ------------------------------------------------------------------

def _root_pitch_in_register(pitch_class: int, register: tuple[int, int]) -> int:
    """Menor pitch dentro de `register` cuja classe e `pitch_class % 12`."""
    lo, hi = register
    p = lo + ((pitch_class - lo) % 12)
    if p > hi:
        p -= 12
    return max(lo, min(hi, p))


def generate_sub(
    analysis: Analysis,
    section: PlanSection,
    *,
    register: tuple[int, int],
    follow: str = "tonic",
    degrees: tuple[int, ...] | None = None,
    seed: int,
) -> list[RhythmicLayer]:
    """Sub-bass monofonico de breakdown. `follow`:

    - `tonic`: uma nota pedal por bar (tom global ou raiz do acorde).
    - `kick`: onset em cada `analysis.kick_positions` da secao — o padrao
      do kick aproxima o ritmo do riff.
    - `riff`: usa `degrees` (mesma lista de graus que arp/rhythmic_machine
      ja aceitam), um grau por beat.

    Nota unica sempre — nunca acorde, sem excecao nem flag: cada branch
    abaixo emite no maximo uma nota por onset, sempre a mesma unica layer.
    O primeiro impacto da secao recebe `first_impact_velocity_boost`.
    """
    if follow not in SUB_FOLLOW_MODES:
        raise ElectronicGeneratorError(
            f"follow must be one of {SUB_FOLLOW_MODES}; got {follow!r}"
        )
    params = _technique_params("bass.sub")
    boost = int(params["first_impact_velocity_boost"])
    jitter = int(params["repeat_velocity_jitter"])
    base_lo, base_hi = VELOCITY_RANGES["normal"]
    base_velocity = (base_lo + base_hi) // 2
    jitter_rng = random.Random(seed)

    bars = bars_in_section(section, analysis)
    notes: list[RhythmicNote] = []
    first = True

    def _emit(pitch: int, start: float, end: float) -> None:
        nonlocal first
        vel = base_velocity + (boost if first else jitter_rng.randint(-jitter, jitter))
        vel = max(1, min(127, vel))
        if end <= start:
            end = start + MIN_NOTE_DURATION_S
        notes.append(RhythmicNote(pitch=pitch, velocity=vel, start_s=start, end_s=end))
        first = False

    if follow == "tonic":
        for bar in bars:
            root_pc = bar.chord.root if bar.chord is not None else analysis.key_root
            pitch = _root_pitch_in_register(root_pc, register)
            _emit(pitch, bar.start, bar.end - MIN_NOTE_DURATION_S)
    elif follow == "kick" and bars:
        section_start_s, section_end_s = bars[0].start, bars[-1].end
        raw_kicks = sorted(
            t for t in analysis.kick_positions
            if section_start_s <= t < section_end_s
        )
        # Bateria em camadas (duas tracks de kick soando no mesmo instante)
        # preserva as duas ocorrencias em `analysis.kick_positions` — sem
        # deduplicar aqui, `_enforce_monophony` nao resolve starts iguais
        # (a duracao minima fica positiva) e o sub sai polifonico apesar da
        # garantia de monofonia estrita deste gerador (AGENTS.md).
        kicks: list[float] = []
        for t in raw_kicks:
            if kicks and t - kicks[-1] < MIN_NOTE_DURATION_S:
                continue
            kicks.append(t)
        pitch = _root_pitch_in_register(analysis.key_root, register)
        for i, k in enumerate(kicks):
            next_t = kicks[i + 1] if i + 1 < len(kicks) else section_end_s
            _emit(pitch, k, next_t - MIN_NOTE_DURATION_S)
    elif follow == "riff":
        # Convencao do plano inteiro (docs/arquitetura.md, harmony.degrees_pcs):
        # grau de escala 1-based sobre a escala do tom global, nunca semitom
        # somado direto — e o mesmo calculo que o validador harmonico usa
        # para checar `harmony=follow_chords`/`free` com `degrees` declarado.
        degs = degrees if degrees else (1,)
        for bar in bars:
            beat_dur = (bar.end - bar.start) / 4.0
            for beat_i in range(4):
                deg = degs[beat_i % len(degs)]
                pcs = degrees_pcs(analysis.key_root, (deg,))
                pc = next(iter(pcs)) if pcs else analysis.key_root % 12
                pitch = _root_pitch_in_register(pc, register)
                start = bar.start + beat_i * beat_dur
                _emit(pitch, start, start + beat_dur - MIN_NOTE_DURATION_S)

    notes.sort(key=lambda n: n.start_s)
    notes = _enforce_monophony(notes)
    return [RhythmicLayer(index=0, notes=tuple(notes))]


# --- sub_drop ---------------------------------------------------------------

@dataclass(frozen=True)
class PitchBendEvent:
    """Um valor de pitchwheel bruto (-8192..8191) num instante absoluto."""
    time_s: float
    value: int


@dataclass(frozen=True)
class SubDropEvent:
    """Um evento pontual de sub-drop: nota unica + curva de pitch bend."""
    note: RhythmicNote
    pitch_bend: tuple[PitchBendEvent, ...]


def generate_sub_drop(
    analysis: Analysis,
    boundary_s: float,
    *,
    register: tuple[int, int],
    seed: int,
) -> SubDropEvent:
    """Evento pontual de sub-drop na fronteira `boundary_s` (inicio de
    secao). Nota unica no fundo de `register`, com curva de pitch bend
    MONOTONICA descendente de 0 ate -8192 ao longo de
    `pitch_bend_curve_ms`, seguida de um reset final para 0 (pitch bend e
    estado persistente de CANAL — sem reset, todo evento seguinte no mesmo
    canal soaria desafinado). Nunca gera acorde — e sempre exatamente uma
    nota, por construcao (nao ha branch que emita mais de uma)."""
    params = _technique_params("bass.sub_drop")
    duration_beats = float(params["duration_beats"])
    curve_ms = float(params["pitch_bend_curve_ms"])
    steps = max(2, int(params["pitch_bend_curve_steps"]))

    bar = find_bar(analysis, boundary_s)
    beat_dur_s = (bar.end - bar.start) / 4.0 if bar is not None else 0.5
    dur_s = max(MIN_NOTE_DURATION_S, duration_beats * beat_dur_s)

    pitch = register[0]
    velocity_lo, velocity_hi = VELOCITY_RANGES["accent"]
    velocity = (velocity_lo + velocity_hi) // 2
    note = RhythmicNote(
        pitch=pitch, velocity=velocity,
        start_s=boundary_s, end_s=boundary_s + dur_s,
    )

    curve: list[PitchBendEvent] = []
    for i in range(steps):
        frac = i / (steps - 1)
        t = boundary_s + frac * (curve_ms / 1000.0)
        value = int(round(-8192 * frac))
        value = max(-8192, min(8191, value))
        curve.append(PitchBendEvent(time_s=t, value=value))

    # Pitch bend e estado persistente de CANAL, nao da nota — sem reset, a
    # curva termina em -8192 e todo evento seguinte no mesmo canal
    # (SUB_DROP_CHANNEL e compartilhado com outros roles gerados) continua
    # desafinado ao maximo. Centraliza no fim da nota, nao no fim da curva,
    # para nunca soar o reset ANTES do drop terminar de descer.
    reset_t = max(curve[-1].time_s, note.end_s) if curve else note.end_s
    curve.append(PitchBendEvent(time_s=reset_t, value=0))

    return SubDropEvent(note=note, pitch_bend=tuple(curve))


__all__ = [
    "HAT_ELEC_ROLES",
    "HAT_PATTERN_MODES",
    "SUB_DROP_ROLES",
    "SUB_FOLLOW_MODES",
    "SUB_ROLES",
    "ElectronicGeneratorError",
    "PitchBendEvent",
    "SubDropEvent",
    "bars_in_section",
    "generate_hat_elec",
    "generate_sub",
    "generate_sub_drop",
]
