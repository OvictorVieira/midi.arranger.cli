"""Elementos ritmicos — arp, rhythmic machine, motor e shadow
(US-006, US-007).

Arp em uma frase: padrao de 1 ou 2 compassos em grade de semicolcheias com
lacuna memoravel, timing quase-fixo (arp e mecanico), notas escolhidas em
snake up-down sobre os tons do acorde do bar, muta a cada 4 ou 8 compassos
via `mutate_every_bars`, e opcionalmente interlocking com pausas da
guitarra da rodada 1. CC74 (filtro respirando) e ciclo de velocity dao a
sensacao de riff em vez de linha morta.

Rhythmic machine e o mesmo motor com role diferente — a diferenca de timbre
vive no plugin/preset, nao no gerador. Ambos vivem em registro `high` por
default (respiram acima do meio).

Motor em uma frase: repeticao em 1/8 ou 1/16 com lacuna caracteristica —
figura de apoio ritmico que da movimento sem competir com o riff. Pattern
fixo por chamada (sem mutacao); a lacuna e o unico "vazio" — evita virar
pulso 100% preenchido que a base de conhecimento condena.

Shadow em uma frase: dobra APENAS o final de cada frase de guitarra, uma
oitava acima ou abaixo, com envelope diferente (velocity menor + release
diferente). Fim de frase e detectado pelos ancoras de unisono de
guitarra da analise da rodada 1 — cada unisono demarca o inicio de uma
frase nova, e o shadow pega as ultimas N notas da frase anterior. Como
fallback, quando a secao nao tem unisono, uma lacuna >= X segundos entre
notas consecutivas conta como fim de frase.

Todos os geradores devolvem `RhythmicLayer` (ou lista destas) que o
renderer transforma em tracks. Motor + shadow reutilizam os dataclasses do
arp — a diferenca de comportamento fica no gerador, nao na estrutura de
saida.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from ..analyze import Analysis, BarAnalysis, Chord, GuitarNote
from ..constants import GATE_RATIOS, REGISTER_BANDS, VELOCITY_RANGES
from ..humanize import DurationEngine, DurationRequest
from ..plan import PlanSection
from ..rng import assert_traceable_seed
from ..style_profile import StyleProfile
from .harmonic import _chord_degrees, _parse_fade_in_bars, _sine_lfo_points

# --- vocabulario e constantes ----------------------------------------------

RHYTHMIC_ROLES: tuple[str, ...] = ("arp", "rhythmic_machine")
"""Roles atendidos por `generate_rhythmic`. Vocabulario fechado."""

STEPS_PER_BAR: int = 16
"""Grade de semicolcheias — o arp desta rodada trabalha em 16 subdivisoes
por compasso. Uma grade mais fina (32, 64) fica para rodadas futuras."""

DEFAULT_RHYTHMIC_REGISTER: tuple[int, int] = REGISTER_BANDS["high"]
"""AC: 'registro default banda high, configuravel' — high (C5+)."""

DEFAULT_RHYTHMIC_ARTICULATION: str = "staccato"
"""Arp default = staccato (gate 0.35-0.65) — nota curta que respira entre
ataques. Rhythmic machine tipico usa 'tight' ou 'ghost'."""

RHYTHMIC_BASE_VELOCITY_BUCKET: str = "normal"
"""Bucket 'normal' (82-105) — arp senta na mesma faixa do teclado, sem
mascarar acentos de guitarra/lead."""

RHYTHMIC_TIMING_JITTER_MS: tuple[float, float] = (2.5, 4.5)
"""AC: 'Timing quase fixo (arp e mecanico)'. Jitter pequeno o suficiente
para preservar o carater sintetico, mas fora da tolerancia de 2ms do
artifice.GRID_TOLERANCE_S — antes valia (0.5, 2.0) e todo onset cabia
inteiro dentro da tolerancia, o que fazia o validador certificar a track
como grade matematica perfeita. Base de conhecimento
(knowledge/persona/persona_produtor_metal_moderno.md:579-580): piano/Rhodes
±5-15ms; 'arp mecanico: timing quase fixo'. 'Quase fixo' e definido por
contraste como significativamente menor que os 5ms minimos do teclado —
por isso o teto de 4.5ms."""

RHYTHMIC_LAYER_ONSET_STAGGER_MS: tuple[float, float] = (2.0, 6.0)
"""Desencontro de ataque entre camadas — bem mais apertado que pad/teclado
(8-25ms). Duas patches de arp em paralelo somam corpo sem borrar o grid."""

RHYTHMIC_PATTERN_BARS_ALLOWED: tuple[int, ...] = (1, 2)
"""AC: 'padrao de 1 ou 2 compassos'."""

RHYTHMIC_DEFAULT_PATTERN_BARS: int = 1

RHYTHMIC_GAP_MIN_STEPS: int = 3
"""AC: 'lacuna memoravel — padrao sem lacuna e rejeitado'. Piso de 3
semicolcheias consecutivas em silencio = colcheia pontuada. Menos que isso
nao e lacuna, e microcortes de humanizacao."""

RHYTHMIC_MUTATE_ALLOWED: tuple[int, ...] = (4, 8)
"""AC: 'mutacao do padrao a cada 4 ou 8 compassos via mutate_every_bars'."""

RHYTHMIC_FILTER_CC: int = 74
"""CC74 (Brightness/Filter Cutoff) — filtro respirando do arp. Mesmo eixo
do drone."""

RHYTHMIC_FILTER_LO: int = 40
RHYTHMIC_FILTER_HI: int = 115
"""Faixa de CC74 do arp. Levemente mais aberta que drone (30-110) — arp
default aparece na mix como riff, precisa presenca."""

RHYTHMIC_FILTER_CYCLE_BARS_ALLOWED: tuple[int, ...] = (1, 2, 4, 8)
"""Ciclos permitidos para CC74. Mais opcoes que drone (2/4/8) porque arp
pode respirar mais rapido — 1 bar de ciclo ja da wah caracteristico."""

RHYTHMIC_DEFAULT_FILTER_CYCLE_BARS: int = 4

RHYTHMIC_FILTER_STEPS_PER_CYCLE: int = 8
"""Pontos de CC74 por ciclo — 8 pontos por ciclo cobre a senoide sem
inflar arquivo. DAW interpola."""

RHYTHMIC_VELOCITY_CYCLE_DEFAULT_BARS: int = 2
"""Ciclo default da modulacao de velocity: 2 bars — arp pulsa em dois
compassos por regra da knowledge base."""

RHYTHMIC_VELOCITY_CYCLE_AMPLITUDE: int = 12
"""Amplitude do ciclo de velocity — base +- 12. Suficiente para o ouvido
perceber pulsacao sem virar acento."""

RHYTHMIC_INTERLOCK_WINDOW_S: float = 0.025
"""Janela para detectar conflito com ataque de guitarra: 25ms cobre o
grid de semicolcheia a 240 bpm sem confundir eventos vizinhos."""

RHYTHMIC_INTERLOCK_MAX_SHIFT_STEPS: int = 1
"""Numero maximo de steps que a interlock pode empurrar um onset em
conflito. Alem disso o onset e descartado — melhor calar que estampar em
cima do riff."""

MIN_NOTE_DURATION_S: float = 0.001
"""Piso de duracao de nota — garante note_off depois de note_on mesmo em
gate agressivo. Mesmo valor usado no teclado."""


# --- catalogo de padroes ---------------------------------------------------

# Cada padrao e uma tupla de 0/1 com pattern_bars * STEPS_PER_BAR posicoes.
# Todos os padroes catalogados tem lacuna de RHYTHMIC_GAP_MIN_STEPS ou mais
# steps consecutivos em silencio — validado no import via
# `_assert_catalog_has_gaps` abaixo.

_PATTERNS_1BAR: tuple[tuple[int, ...], ...] = (
    # steps: 0 1 2 3  4 5 6 7  8 9 10 11 12 13 14 15
    (1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 0, 0, 1, 1, 0, 1),   # gap 9-11
    (1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 1, 1, 0, 1, 0),   # gap 7-10
    (1, 1, 1, 0, 0, 0, 1, 0, 1, 1, 0, 1, 0, 0, 0, 1),   # gap 3-5 and 12-14
    (1, 0, 1, 1, 0, 0, 0, 0, 1, 1, 0, 1, 0, 1, 1, 0),   # gap 4-7
)

_PATTERNS_2BAR: tuple[tuple[int, ...], ...] = (
    (
        1, 1, 0, 1, 0, 1, 1, 0, 1, 0, 0, 0, 1, 1, 0, 1,
        1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 1, 1, 0, 1, 0,
    ),
    (
        1, 0, 1, 0, 1, 1, 0, 0, 0, 0, 1, 0, 1, 0, 1, 1,
        1, 1, 0, 1, 0, 0, 0, 0, 1, 0, 1, 0, 1, 1, 0, 1,
    ),
)

RHYTHMIC_PATTERNS: dict[int, tuple[tuple[int, ...], ...]] = {
    1: _PATTERNS_1BAR,
    2: _PATTERNS_2BAR,
}


def _max_gap_run(steps: tuple[int, ...]) -> int:
    """Retorna o maior run de zeros consecutivos em `steps`."""
    best = 0
    run = 0
    for s in steps:
        if s == 0:
            run += 1
            if run > best:
                best = run
        else:
            run = 0
    return best


def _assert_catalog_has_gaps() -> None:
    """Import-time guard: todos os padroes catalogados tem lacuna >=
    RHYTHMIC_GAP_MIN_STEPS. Se um dia alguem editar o catalogo removendo a
    lacuna sem querer, o import quebra em vez de silenciosamente gerar
    padroes rejeitados pelo AC."""
    for bars, catalog in RHYTHMIC_PATTERNS.items():
        for i, pattern in enumerate(catalog):
            expected_len = bars * STEPS_PER_BAR
            if len(pattern) != expected_len:
                raise ValueError(
                    f"pattern {bars}bar[{i}] has {len(pattern)} steps; "
                    f"expected {expected_len}"
                )
            if _max_gap_run(pattern) < RHYTHMIC_GAP_MIN_STEPS:
                raise ValueError(
                    f"pattern {bars}bar[{i}] has no gap of "
                    f"{RHYTHMIC_GAP_MIN_STEPS}+ rest steps"
                )


_assert_catalog_has_gaps()


# --- schema de saida --------------------------------------------------------

@dataclass(frozen=True)
class RhythmicNote:
    """Nota individual do arp/rhythmic. `is_interlocked=True` marca notas
    que tiveram onset ajustado para evitar ataque de guitarra."""
    pitch: int
    velocity: int
    start_s: float
    end_s: float
    is_interlocked: bool = False


@dataclass(frozen=True)
class RhythmicCCEvent:
    """Evento de CC do arp (CC74 filtro). Mesma tupla time_s/cc/value do
    drone — plugavel no seam `cc_events` do renderer."""
    time_s: float
    cc: int
    value: int


@dataclass(frozen=True)
class RhythmicLayer:
    """Uma camada (track) de arp. Cada camada e uma track independente."""
    index: int
    notes: tuple[RhythmicNote, ...]
    cc_events: tuple[RhythmicCCEvent, ...] = ()


# --- helpers ---------------------------------------------------------------

def _rhythmic_base_velocity(velocity_ranges=VELOCITY_RANGES) -> int:
    lo, hi = velocity_ranges[RHYTHMIC_BASE_VELOCITY_BUCKET]
    return int(lo + hi) // 2


def _rhythmic_gate(articulation: str, gate_ratios=GATE_RATIOS) -> float:
    """Fator de gate para articulacao. Articulacoes fora do mapa caem para
    staccato — arp longo demais borra o grid."""
    ratios = gate_ratios.get(articulation)
    if ratios is None:
        ratios = gate_ratios[DEFAULT_RHYTHMIC_ARTICULATION]
    lo, hi = ratios
    return (lo + hi) / 2.0


def _bars_in_section(section: PlanSection, analysis: Analysis) -> list[BarAnalysis]:
    return [
        b for b in analysis.bars
        if section.start_bar <= b.index < section.end_bar
    ]


def _chord_pitches_in_register(
    chord: Chord, register: tuple[int, int],
) -> list[int]:
    """Notas do acorde dentro do registro, ordenadas crescentes.

    Empilha em oitavas ate estourar o teto — arp cobre a extensao inteira,
    diferente do teclado (uma oitava so). Se o registro nao acomodar nem
    a raiz, devolve `[lo]` como fallback nao-vazio."""
    lo, hi = register
    degrees = _chord_degrees(chord)
    root_base = lo + ((chord.root - lo) % 12)
    pitches: list[int] = []
    octave = 0
    # root_base >= lo por construcao (Python `%` positivo), entao p >= lo
    # sempre — nao ha guarda `p < lo` aqui.
    while True:
        emitted = False
        for d in degrees:
            p = root_base + d + octave * 12
            if p > hi:
                continue
            pitches.append(p)
            emitted = True
        if not emitted:
            break
        octave += 1
    if not pitches:
        return [lo]
    return sorted(set(pitches))


def _snake_index(step_ordinal: int, n_pitches: int) -> int:
    """Indice up-down para o snake do arp — 0, 1, ..., n-1, n-2, ..., 1, 0, 1, ...

    Com n_pitches <= 1 sempre devolve 0."""
    if n_pitches <= 1:
        return 0
    period = 2 * (n_pitches - 1)
    i = step_ordinal % period
    if i < n_pitches:
        return i
    return period - i


def _validate_custom_steps(
    steps: tuple[int, ...], pattern_bars: int,
) -> tuple[int, ...]:
    """Aceita padrao custom do usuario. Deve ter comprimento pattern_bars
    * STEPS_PER_BAR e conter lacuna >= RHYTHMIC_GAP_MIN_STEPS.

    AC: 'padrao sem lacuna e rejeitado'."""
    expected_len = pattern_bars * STEPS_PER_BAR
    if len(steps) != expected_len:
        raise ValueError(
            f"custom steps must have length {expected_len} "
            f"({pattern_bars} bar(s) * {STEPS_PER_BAR} steps); got {len(steps)}"
        )
    normalized = tuple(1 if s else 0 for s in steps)
    if _max_gap_run(normalized) < RHYTHMIC_GAP_MIN_STEPS:
        raise ValueError(
            f"custom pattern has no gap of {RHYTHMIC_GAP_MIN_STEPS}+ "
            f"consecutive rest steps — rejected per AC"
        )
    return normalized


def _pick_window_patterns(
    n_windows: int,
    catalog: tuple[tuple[int, ...], ...],
    rng: random.Random,
) -> list[int]:
    """Escolhe padrao por janela de mutacao. Evita repetir o padrao anterior
    imediatamente — mutacao deve mudar algo. Com catalogo de 1 padrao, so
    resta esse."""
    if not catalog:
        raise ValueError("empty pattern catalog")
    picks: list[int] = []
    last = -1
    for _ in range(max(1, n_windows)):
        if len(catalog) == 1:
            picks.append(0)
            last = 0
            continue
        candidates = [i for i in range(len(catalog)) if i != last]
        idx = rng.choice(candidates)
        picks.append(idx)
        last = idx
    return picks


def _guitar_attack_times_in_bar(
    analysis: Analysis, bar: BarAnalysis,
) -> list[float]:
    """Tempos de ataque de guitarra dentro do bar. Usa guitar_notes ja
    ordenados por start; para quando ultrapassa bar.end (lista ordenada)."""
    times: list[float] = []
    for gn in analysis.guitar_notes:
        if gn.start < bar.start:
            continue
        if gn.start >= bar.end:
            break
        times.append(gn.start)
    return times


def _resolve_interlock_onset(
    candidate: float,
    step_dur_s: float,
    bar: BarAnalysis,
    guitar_times: list[float],
) -> float | None:
    """Tenta reposicionar `candidate` para nao coincidir com ataque de
    guitarra. Permite ate RHYTHMIC_INTERLOCK_MAX_SHIFT_STEPS de shift para
    frente; se ainda houver conflito ou passar do fim do bar, devolve None
    (a nota e descartada)."""
    for shift in range(RHYTHMIC_INTERLOCK_MAX_SHIFT_STEPS + 1):
        pos = candidate + shift * step_dur_s
        if pos >= bar.end:
            return None
        if not any(
            abs(pos - t) < RHYTHMIC_INTERLOCK_WINDOW_S for t in guitar_times
        ):
            return pos
    return None


def _velocity_for_step(
    base: int,
    bar_pos_in_section: int,
    step_in_bar: int,
    velocity_cycle_bars: int,
    dynamics: dict,
    section_kind: str,
    velocity_ranges=VELOCITY_RANGES,
) -> int:
    """Velocity para um step: base + ciclo senoidal + forma dinamica
    compartilhada com o pad (fade_in_Nbars / open_at_chorus / hold).

    Ciclo senoidal cobre velocity_cycle_bars bars com amplitude
    RHYTHMIC_VELOCITY_CYCLE_AMPLITUDE."""
    entry = dynamics.get("entry")
    shape = dynamics.get("shape", "hold")

    v = base
    fade_bars = _parse_fade_in_bars(entry)
    if fade_bars > 0 and bar_pos_in_section < fade_bars:
        floor = velocity_ranges["ghost"][0]
        frac = (bar_pos_in_section + 1) / fade_bars
        v = round(floor + (base - floor) * frac)

    if shape == "open_at_chorus" and section_kind == "chorus":
        v += 8

    total_steps = velocity_cycle_bars * STEPS_PER_BAR
    step_ordinal = bar_pos_in_section * STEPS_PER_BAR + step_in_bar
    theta = 2 * math.pi * (step_ordinal / total_steps)
    v += int(round(RHYTHMIC_VELOCITY_CYCLE_AMPLITUDE * math.sin(theta)))
    return max(1, min(127, v))


def _filter_cc_curve(
    start_s: float,
    end_s: float,
    cycle_bars: int,
    bar_dur_s: float,
    phase: float,
    steps_per_cycle: int = RHYTHMIC_FILTER_STEPS_PER_CYCLE,
) -> tuple[RhythmicCCEvent, ...]:
    """LFO senoidal de CC74 ao longo de [start_s, end_s]. Ciclo em segundos
    = cycle_bars * bar_dur_s. Emite ~steps_per_cycle pontos por ciclo; DAW
    interpola."""
    if end_s - start_s <= 0:
        return ()
    points = _sine_lfo_points(
        start_s, end_s, cycle_bars * bar_dur_s,
        steps_per_cycle,
        RHYTHMIC_FILTER_LO, RHYTHMIC_FILTER_HI, phase,
    )
    return tuple(
        RhythmicCCEvent(time_s=t, cc=RHYTHMIC_FILTER_CC, value=v)
        for t, v in points
    )


def _empty_rhythmic_layers(layers: int) -> list[RhythmicLayer]:
    """Lista de camadas vazias — retorno padrao quando a secao nao tem
    bars. Compartilhado por generate_rhythmic, generate_motor e
    generate_shadow."""
    return [
        RhythmicLayer(index=i, notes=(), cc_events=())
        for i in range(layers)
    ]


def _layer_stagger_offsets(
    rng: random.Random,
    layers: int,
    stagger_range_ms: tuple[float, float],
) -> list[float]:
    """Deslocamento acumulado em segundos por camada. Layer 0 sempre em 0.0;
    cada layer seguinte adiciona `rng.uniform(*stagger_range_ms) / 1000.0`.
    Compartilhado por generate_rhythmic e generate_motor."""
    offsets: list[float] = [0.0]
    for _ in range(1, layers):
        step_ms = rng.uniform(*stagger_range_ms)
        offsets.append(offsets[-1] + step_ms / 1000.0)
    return offsets


def _emit_snake_step_note(
    *,
    bar: BarAnalysis,
    bar_pos: int,
    step_i: int,
    candidate_s: float,
    step_dur_s: float,
    pitches: list[int],
    step_ordinal: int,
    base_velocity: int,
    gate: float,
    velocity_cycle_bars: int,
    dyn: dict,
    section_kind: str,
    is_interlocked: bool = False,
    velocity_ranges=VELOCITY_RANGES,
) -> RhythmicNote | None:
    """Monta uma RhythmicNote no snake up-down. Devolve None quando o
    candidato ultrapassa o fim do bar. Compartilhado por generate_rhythmic
    e generate_motor — mesma politica de velocity + gate + duracao minima."""
    if candidate_s >= bar.end:
        return None
    p_idx = _snake_index(step_ordinal, len(pitches))
    pitch = pitches[p_idx]
    velocity = _velocity_for_step(
        base_velocity, bar_pos, step_i,
        velocity_cycle_bars, dyn, section_kind,
        velocity_ranges=velocity_ranges,
    )
    note_dur = step_dur_s * gate
    if note_dur < MIN_NOTE_DURATION_S:
        note_dur = MIN_NOTE_DURATION_S
    return RhythmicNote(
        pitch=pitch, velocity=velocity,
        start_s=candidate_s, end_s=candidate_s + note_dur,
        is_interlocked=is_interlocked,
    )


def _generate_snake_layers(
    *,
    bars: list[BarAnalysis],
    layers: int,
    seed: int,
    stagger_offsets_s: list[float],
    steps_for_bar,
    resolve_interlock,
    register: tuple[int, int],
    base_velocity: int,
    gate: float,
    articulation: str,
    velocity_cycle_bars: int,
    dyn: dict,
    section_kind: str,
    bar_dur_s: float,
    section_start_s: float,
    section_end_s: float,
    filter_cycle_bars: int,
    timing_jitter_ms: tuple[float, float],
    filter_steps_per_cycle: int = RHYTHMIC_FILTER_STEPS_PER_CYCLE,
    profile: StyleProfile | None = None,
) -> list[RhythmicLayer]:
    """Loop principal compartilhado por generate_rhythmic e generate_motor.
    `steps_for_bar(bar_pos)` devolve o padrao de STEPS_PER_BAR pares (0/1)
    daquele bar; `resolve_interlock` recebe (candidate, step_dur_s, bar) e
    devolve (candidate_ajustado, is_interlocked) ou None quando o
    candidato precisa ser descartado. Motor passa `resolve_interlock=None`
    porque nao interlock."""
    resolved_profile = profile or StyleProfile.default()
    result: list[RhythmicLayer] = []
    for layer_idx in range(layers):
        stagger_s = stagger_offsets_s[layer_idx]
        layer_rng = random.Random(seed + (layer_idx + 1) * 1_000_003)

        notes: list[RhythmicNote] = []
        step_ordinal_global = 0

        for bar_pos, bar in enumerate(bars):
            chord = bar.chord
            if chord is None:
                continue
            pitches = _chord_pitches_in_register(chord, register)
            step_dur_s = bar_dur_s / STEPS_PER_BAR
            steps = steps_for_bar(bar_pos)

            for step_i, active in enumerate(steps):
                if not active:
                    continue
                raw_onset = bar.start + step_i * step_dur_s + stagger_s
                jitter_s = layer_rng.uniform(*timing_jitter_ms) / 1000.0
                candidate = raw_onset + jitter_s
                is_interlocked = False
                if resolve_interlock is not None:
                    adjusted = resolve_interlock(candidate, step_dur_s, bar)
                    if adjusted is None:
                        continue
                    candidate, is_interlocked = adjusted
                note = _emit_snake_step_note(
                    bar=bar, bar_pos=bar_pos, step_i=step_i,
                    candidate_s=candidate, step_dur_s=step_dur_s,
                    pitches=pitches, step_ordinal=step_ordinal_global,
                    base_velocity=base_velocity, gate=gate,
                    velocity_cycle_bars=velocity_cycle_bars,
                    dyn=dyn, section_kind=section_kind,
                    is_interlocked=is_interlocked,
                    velocity_ranges=resolved_profile.velocity_ranges,
                )
                if note is None:
                    continue
                notes.append(note)
                step_ordinal_global += 1

        cc_events: tuple[RhythmicCCEvent, ...] = ()
        if notes:
            phase = layer_rng.random()
            cc_events = _filter_cc_curve(
                section_start_s, section_end_s,
                filter_cycle_bars, bar_dur_s, phase,
                filter_steps_per_cycle,
            )

        notes.sort(key=lambda n: (n.start_s, n.pitch))
        notes = _apply_duration_engine(
            notes,
            articulation=articulation,
            fallback_end_s=section_end_s,
            seed=seed + (layer_idx + 1) * 1_000_009,
            profile=resolved_profile,
        )
        result.append(RhythmicLayer(
            index=layer_idx, notes=tuple(notes), cc_events=cc_events,
        ))
    return result


def _apply_duration_engine(
    notes: list[RhythmicNote],
    *,
    articulation: str,
    fallback_end_s: float,
    seed: int,
    profile: StyleProfile | None = None,
) -> list[RhythmicNote]:
    """Reescreve `end_s` de cada nota da camada via `DurationEngine`.

    Sem esse pos-passo, `_emit_snake_step_note` fixava `step_dur_s * gate`
    igual para toda nota; validador de artificialidade certificava a camada
    inteira como `duration_uniform`. Aqui cada nota sorteia um valor dentro
    de `GATE_RATIOS[articulation]` proporcional ao gap real ate a proxima
    nota da camada (ou `fallback_end_s` na ultima). Articulacao fora de
    `GATE_RATIOS` cai para o default do rhythmic — mesma politica de
    `_rhythmic_gate`."""
    if not notes:
        return notes
    art = articulation if articulation in GATE_RATIOS else DEFAULT_RHYTHMIC_ARTICULATION
    engine = DurationEngine(seed=seed, profile=profile)
    result: list[RhythmicNote] = []
    for i, note in enumerate(notes):
        next_start = (
            notes[i + 1].start_s if i + 1 < len(notes) else fallback_end_s
        )
        gap_s = next_start - note.start_s
        gap_ms = gap_s * 1000.0 if gap_s > 0 else None
        dur_ms = engine.compute(
            DurationRequest(articulation=art, gap_ms=gap_ms),
        )
        dur_s = max(MIN_NOTE_DURATION_S, dur_ms / 1000.0)
        result.append(replace(note, end_s=note.start_s + dur_s))
    return result


# --- gerador ---------------------------------------------------------------

def generate_rhythmic(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "arp",
    register: tuple[int, int] = DEFAULT_RHYTHMIC_REGISTER,
    layers: int = 1,
    articulation: str = DEFAULT_RHYTHMIC_ARTICULATION,
    dynamics: dict | None = None,
    pattern_bars: int = RHYTHMIC_DEFAULT_PATTERN_BARS,
    mutate_every_bars: int | None = None,
    interlock: bool = False,
    filter_cycle_bars: int = RHYTHMIC_DEFAULT_FILTER_CYCLE_BARS,
    velocity_cycle_bars: int = RHYTHMIC_VELOCITY_CYCLE_DEFAULT_BARS,
    custom_steps: tuple[int, ...] | list[int] | None = None,
    seed: int = 0,
    profile: StyleProfile | None = None,
) -> list[RhythmicLayer]:
    """Gera camadas de arp / rhythmic machine para uma secao.

    Cada camada e uma track em unisono (mesmo padrao, offset pequeno de
    ataque). Notas caem em grade de STEPS_PER_BAR por bar; pitches sao tons
    do acorde do bar em snake up-down. Padrao base tem lacuna memoravel; se
    `mutate_every_bars` estiver setado, o padrao troca a cada N bars.
    `interlock=True` reposiciona onsets que coincidem com ataques de
    guitarra (rodada 1).

    Args:
      analysis: saida de analise da rodada 1.
      section: secao alvo.
      role: 'arp' ou 'rhythmic_machine'.
      register: (low, high) MIDI onde as notas vivem.
      layers: camadas em unisono, cada uma vira uma track.
      articulation: mapeado para gate. Default 'staccato'.
      dynamics: 'entry' e 'shape' (fade_in_Nbars, open_at_chorus, hold).
      pattern_bars: 1 ou 2 — comprimento do padrao em bars.
      mutate_every_bars: 4 ou 8, ou None para nao mutar. AC.
      interlock: True desloca onsets em conflito com ataque de guitarra.
      filter_cycle_bars: ciclo do CC74 (filtro respirando).
      velocity_cycle_bars: ciclo senoidal da modulacao de velocity.
      custom_steps: padrao custom (0/1) opcional — se dado, substitui o
        catalogo. Deve ter comprimento pattern_bars * STEPS_PER_BAR e conter
        lacuna >= RHYTHMIC_GAP_MIN_STEPS.
      seed: seed determinstica.
      profile: `StyleProfile` opcional — sobrepoe a faixa de velocity base
        e `gate_ratios[articulation]` lidos de `tools/constants.py`. Sem
        `profile`, usa `StyleProfile.default()` (chamador antigo continua
        byte-identico).

    Raises:
      ValueError: layers < 1, role fora de RHYTHMIC_ROLES, pattern_bars
        fora de RHYTHMIC_PATTERN_BARS_ALLOWED, mutate_every_bars fora de
        RHYTHMIC_MUTATE_ALLOWED (quando dado), filter_cycle_bars fora de
        RHYTHMIC_FILTER_CYCLE_BARS_ALLOWED, velocity_cycle_bars < 1, ou
        custom_steps sem lacuna.
    """
    assert_traceable_seed(seed, source="palette.rhythmic.generate_rhythmic")
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if role not in RHYTHMIC_ROLES:
        raise ValueError(
            f"role must be one of {list(RHYTHMIC_ROLES)}; got {role!r}"
        )
    if pattern_bars not in RHYTHMIC_PATTERN_BARS_ALLOWED:
        raise ValueError(
            f"pattern_bars must be one of "
            f"{list(RHYTHMIC_PATTERN_BARS_ALLOWED)}; got {pattern_bars}"
        )
    if mutate_every_bars is not None and mutate_every_bars not in RHYTHMIC_MUTATE_ALLOWED:
        raise ValueError(
            f"mutate_every_bars must be one of "
            f"{list(RHYTHMIC_MUTATE_ALLOWED)} or None; got {mutate_every_bars}"
        )
    if filter_cycle_bars not in RHYTHMIC_FILTER_CYCLE_BARS_ALLOWED:
        raise ValueError(
            f"filter_cycle_bars must be one of "
            f"{list(RHYTHMIC_FILTER_CYCLE_BARS_ALLOWED)}; "
            f"got {filter_cycle_bars}"
        )
    if velocity_cycle_bars < 1:
        raise ValueError(
            f"velocity_cycle_bars must be >= 1; got {velocity_cycle_bars}"
        )

    dyn = dynamics or {}
    rng = random.Random(seed)
    bars = _bars_in_section(section, analysis)
    if not bars:
        return _empty_rhythmic_layers(layers)

    resolved_profile = profile or StyleProfile.default()
    base = _rhythmic_base_velocity(resolved_profile.velocity_ranges)
    gate = _rhythmic_gate(articulation, resolved_profile.gate_ratios)

    # Catalogo / custom
    if custom_steps is not None:
        catalog: tuple[tuple[int, ...], ...] = (
            _validate_custom_steps(tuple(custom_steps), pattern_bars),
        )
    else:
        catalog = RHYTHMIC_PATTERNS[pattern_bars]

    # Escolha de padrao por janela de mutacao
    mutation_bars = mutate_every_bars if mutate_every_bars else max(len(bars), 1)
    n_windows = math.ceil(len(bars) / mutation_bars)
    window_patterns = _pick_window_patterns(n_windows, catalog, rng)

    layer_offsets_s = _layer_stagger_offsets(
        rng, layers, RHYTHMIC_LAYER_ONSET_STAGGER_MS,
    )

    section_start_s = bars[0].start
    section_end_s = bars[-1].end
    bar_dur_s = bars[0].end - bars[0].start

    def steps_for_bar(bar_pos: int) -> tuple[int, ...]:
        window_idx = bar_pos // mutation_bars
        pattern = catalog[window_patterns[window_idx]]
        pos_in_pattern_bar = (bar_pos % mutation_bars) % pattern_bars
        return pattern[
            pos_in_pattern_bar * STEPS_PER_BAR:
            (pos_in_pattern_bar + 1) * STEPS_PER_BAR
        ]

    resolver = None
    if interlock:
        def resolver(candidate, step_dur_s, bar):
            guitar_times = _guitar_attack_times_in_bar(analysis, bar)
            if not guitar_times:
                return (candidate, False)
            resolved = _resolve_interlock_onset(
                candidate, step_dur_s, bar, guitar_times,
            )
            if resolved is None:
                return None
            return (resolved, resolved != candidate)

    return _generate_snake_layers(
        bars=bars, layers=layers, seed=seed,
        stagger_offsets_s=layer_offsets_s,
        steps_for_bar=steps_for_bar,
        resolve_interlock=resolver,
        register=register, base_velocity=base, gate=gate,
        articulation=articulation,
        velocity_cycle_bars=velocity_cycle_bars,
        dyn=dyn, section_kind=section.kind,
        bar_dur_s=bar_dur_s,
        section_start_s=section_start_s, section_end_s=section_end_s,
        filter_cycle_bars=filter_cycle_bars,
        timing_jitter_ms=RHYTHMIC_TIMING_JITTER_MS,
        profile=resolved_profile,
    )


# --- motor (US-007) --------------------------------------------------------

MOTOR_ROLES: tuple[str, ...] = ("motor",)
"""Role atendido por `generate_motor`. Vocabulario fechado — motor e uma
figura ritmica de apoio, nao um segundo arp."""

MOTOR_SUBDIVISION_ALLOWED: tuple[str, ...] = ("eighth", "sixteenth")
"""AC: 'motor = repeticao em 1/8 ou 1/16'. Vocabulario fechado."""

MOTOR_DEFAULT_SUBDIVISION: str = "sixteenth"
"""Default 1/16 — a figura mais comum na knoledgebase para textura de
apoio. 1/8 fica para arranjos mais lentos."""

MOTOR_GAP_MIN_STEPS: int = 3
"""AC: 'lacuna caracteristica'. Piso de 3 semicolcheias consecutivas em
silencio no grid de STEPS_PER_BAR — mesma metrica do arp, para o motor
nunca colar como pulso 100% preenchido."""

MOTOR_DEFAULT_ARTICULATION: str = "staccato"
"""Motor default = staccato — nota curta, respiro entre ataques."""

MOTOR_BASE_VELOCITY_BUCKET: str = "tied_soft"
"""Bucket 'tied_soft' (50-75) — motor senta ABAIXO do arp/lead (bucket
'normal' 82-105) para nao competir com o riff. AC: 'da movimento sem
competir com o riff'."""

DEFAULT_MOTOR_REGISTER: tuple[int, int] = REGISTER_BANDS["mid"]
"""Registro default do motor: banda mid (C3-C5). Motor tipicamente cobre
faixa media — apoio ritmico entre baixo (grave) e arp/lead (alto)."""

MOTOR_TIMING_JITTER_MS: tuple[float, float] = (2.5, 4.5)
"""Motor mecanico — mesmo jitter do arp, e pela mesma razao: valor antigo
(0.5, 2.0) cabia inteiro dentro da tolerancia de 2ms do artifice
(GRID_TOLERANCE_S), certificando a track como grade matematica perfeita.
Motor e figura de apoio ritmico com pulso constante; a base
(knowledge/persona/persona_produtor_metal_moderno.md:580) enquadra este
tipo de elemento como 'timing quase fixo' — significativamente menor que
o teclado (±5-15ms), por isso o teto de 4.5ms."""

MOTOR_LAYER_ONSET_STAGGER_MS: tuple[float, float] = (2.0, 6.0)
"""Stagger apertado entre camadas — motor grosso e feito de duas patches
somando corpo sem borrar o grid, mesmo padrao do arp."""

MOTOR_FILTER_CYCLE_BARS_ALLOWED: tuple[int, ...] = (1, 2, 4, 8)
MOTOR_DEFAULT_FILTER_CYCLE_BARS: int = 4
MOTOR_FILTER_STEPS_PER_CYCLE: int = 8
"""Mesma faixa de ciclos de CC74 do arp — motor tambem respira, so mais
discreto. Comparte constante com o rhythmic default."""

# Catalogo de padroes de motor. Cada padrao ocupa STEPS_PER_BAR steps.
# Sub-grid 1/8 = so ativa nos steps pares (0,2,4,6,8,10,12,14).
# Sub-grid 1/16 = pode ativar em qualquer step.
# Toda entrada tem lacuna >= MOTOR_GAP_MIN_STEPS — validado no import por
# `_assert_motor_catalog_has_gaps` abaixo.

_MOTOR_PATTERNS_EIGHTH: tuple[tuple[int, ...], ...] = (
    # steps: 0 1 2 3  4 5 6 7  8 9 10 11 12 13 14 15
    (1, 0, 1, 0, 1, 0, 1, 0, 0, 0, 0, 0, 1, 0, 1, 0),   # gap 7-11 (5 steps)
    (1, 0, 1, 0, 0, 0, 1, 0, 1, 0, 1, 0, 1, 0, 0, 0),   # gap 3-5 e 13-15
)

_MOTOR_PATTERNS_SIXTEENTH: tuple[tuple[int, ...], ...] = (
    # steps: 0 1 2 3  4 5 6 7  8 9 10 11 12 13 14 15
    (1, 1, 1, 1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1),   # gap 6-8 (3 steps)
    (1, 1, 1, 0, 0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1),   # gap 3-5 (3 steps)
)

MOTOR_PATTERNS: dict[str, tuple[tuple[int, ...], ...]] = {
    "eighth": _MOTOR_PATTERNS_EIGHTH,
    "sixteenth": _MOTOR_PATTERNS_SIXTEENTH,
}


def _assert_motor_catalog_has_gaps() -> None:
    """Import-time guard: cada padrao de motor tem lacuna >= MOTOR_GAP_MIN_STEPS
    e comprimento STEPS_PER_BAR; padroes de subdivisao 'eighth' so ativam em
    steps pares. Guard identico ao do arp — o dia que alguem editar o
    catalogo removendo a lacuna sem querer, o import quebra."""
    for subdivision, catalog in MOTOR_PATTERNS.items():
        for i, pattern in enumerate(catalog):
            if len(pattern) != STEPS_PER_BAR:
                raise ValueError(
                    f"motor pattern {subdivision}[{i}] has {len(pattern)} "
                    f"steps; expected {STEPS_PER_BAR}"
                )
            if _max_gap_run(pattern) < MOTOR_GAP_MIN_STEPS:
                raise ValueError(
                    f"motor pattern {subdivision}[{i}] has no gap of "
                    f"{MOTOR_GAP_MIN_STEPS}+ rest steps"
                )
            if subdivision == "eighth":
                for step_idx, active in enumerate(pattern):
                    if active and step_idx % 2 != 0:
                        raise ValueError(
                            f"motor pattern eighth[{i}] activates odd step "
                            f"{step_idx} — 1/8 subdivision must land on "
                            f"even steps only"
                        )


_assert_motor_catalog_has_gaps()


def _motor_base_velocity(velocity_ranges=VELOCITY_RANGES) -> int:
    lo, hi = velocity_ranges[MOTOR_BASE_VELOCITY_BUCKET]
    return int(lo + hi) // 2


def _motor_gate(articulation: str, gate_ratios=GATE_RATIOS) -> float:
    """Gate para articulacao do motor. Articulacoes fora do mapa caem para
    staccato — mesma politica do arp."""
    ratios = gate_ratios.get(articulation)
    if ratios is None:
        ratios = gate_ratios[MOTOR_DEFAULT_ARTICULATION]
    lo, hi = ratios
    return (lo + hi) / 2.0


def _pick_motor_pattern(
    catalog: tuple[tuple[int, ...], ...],
    rng: random.Random,
) -> tuple[int, ...]:
    """Escolhe um unico padrao do catalogo. Motor nao muta ao longo da
    secao (AC: 'repeticao') — a lacuna caracteristica pede consistencia."""
    if not catalog:
        raise ValueError("empty motor pattern catalog")
    return catalog[rng.randrange(len(catalog))]


def generate_motor(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "motor",
    subdivision: str = MOTOR_DEFAULT_SUBDIVISION,
    register: tuple[int, int] = DEFAULT_MOTOR_REGISTER,
    layers: int = 1,
    articulation: str = MOTOR_DEFAULT_ARTICULATION,
    dynamics: dict | None = None,
    filter_cycle_bars: int = MOTOR_DEFAULT_FILTER_CYCLE_BARS,
    velocity_cycle_bars: int = RHYTHMIC_VELOCITY_CYCLE_DEFAULT_BARS,
    custom_steps: tuple[int, ...] | list[int] | None = None,
    seed: int = 0,
    profile: StyleProfile | None = None,
) -> list[RhythmicLayer]:
    """Gera camadas de motor (figura ritmica de apoio) para uma secao.

    O padrao repete inteiro a cada bar; motor nao muta (AC: 'repeticao').
    Notas caem em snake up-down sobre tons do acorde, igual ao arp. Timing
    e mecanico (jitter minusculo). CC74 respira ao longo dos bars.

    Args:
      analysis: saida de analise da rodada 1.
      section: secao alvo.
      role: 'motor'.
      subdivision: 'eighth' (1/8) ou 'sixteenth' (1/16). AC.
      register: (low, high) MIDI onde as notas vivem. Default mid.
      layers: camadas em unisono, cada uma vira uma track.
      articulation: mapeado para gate. Default 'staccato'.
      dynamics: 'entry' e 'shape' (fade_in_Nbars, open_at_chorus, hold).
      filter_cycle_bars: ciclo do CC74 (filtro respirando).
      velocity_cycle_bars: ciclo senoidal de velocity.
      custom_steps: padrao custom (0/1) opcional — comprimento STEPS_PER_BAR
        e lacuna >= MOTOR_GAP_MIN_STEPS. Para subdivision='eighth', so
        ativa steps pares.
      seed: seed determinstica.
      profile: `StyleProfile` opcional — sobrepoe a faixa de velocity base
        e `gate_ratios[articulation]` lidos de `tools/constants.py`. Sem
        `profile`, usa `StyleProfile.default()` (chamador antigo continua
        byte-identico).

    Raises:
      ValueError: layers < 1, role != 'motor', subdivision fora do
        vocabulario, filter_cycle_bars fora de MOTOR_FILTER_CYCLE_BARS_ALLOWED,
        velocity_cycle_bars < 1, ou custom_steps invalido.
    """
    assert_traceable_seed(seed, source="palette.rhythmic.generate_motor")
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if role not in MOTOR_ROLES:
        raise ValueError(
            f"role must be one of {list(MOTOR_ROLES)}; got {role!r}"
        )
    if subdivision not in MOTOR_SUBDIVISION_ALLOWED:
        raise ValueError(
            f"subdivision must be one of {list(MOTOR_SUBDIVISION_ALLOWED)}; "
            f"got {subdivision!r}"
        )
    if filter_cycle_bars not in MOTOR_FILTER_CYCLE_BARS_ALLOWED:
        raise ValueError(
            f"filter_cycle_bars must be one of "
            f"{list(MOTOR_FILTER_CYCLE_BARS_ALLOWED)}; got {filter_cycle_bars}"
        )
    if velocity_cycle_bars < 1:
        raise ValueError(
            f"velocity_cycle_bars must be >= 1; got {velocity_cycle_bars}"
        )

    dyn = dynamics or {}
    rng = random.Random(seed)
    bars = _bars_in_section(section, analysis)
    if not bars:
        return _empty_rhythmic_layers(layers)

    resolved_profile = profile or StyleProfile.default()
    base = _motor_base_velocity(resolved_profile.velocity_ranges)
    gate = _motor_gate(articulation, resolved_profile.gate_ratios)

    if custom_steps is not None:
        pattern = _validate_motor_custom_steps(
            tuple(custom_steps), subdivision,
        )
    else:
        catalog = MOTOR_PATTERNS[subdivision]
        pattern = _pick_motor_pattern(catalog, rng)

    layer_offsets_s = _layer_stagger_offsets(
        rng, layers, MOTOR_LAYER_ONSET_STAGGER_MS,
    )
    section_start_s = bars[0].start
    section_end_s = bars[-1].end
    bar_dur_s = bars[0].end - bars[0].start

    return _generate_snake_layers(
        bars=bars, layers=layers, seed=seed,
        stagger_offsets_s=layer_offsets_s,
        steps_for_bar=lambda _bar_pos: pattern,
        resolve_interlock=None,
        register=register, base_velocity=base, gate=gate,
        articulation=articulation,
        velocity_cycle_bars=velocity_cycle_bars,
        dyn=dyn, section_kind=section.kind,
        bar_dur_s=bar_dur_s,
        section_start_s=section_start_s, section_end_s=section_end_s,
        filter_cycle_bars=filter_cycle_bars,
        timing_jitter_ms=MOTOR_TIMING_JITTER_MS,
        filter_steps_per_cycle=MOTOR_FILTER_STEPS_PER_CYCLE,
        profile=resolved_profile,
    )


def _validate_motor_custom_steps(
    steps: tuple[int, ...], subdivision: str,
) -> tuple[int, ...]:
    """Aceita padrao custom de motor. Comprimento STEPS_PER_BAR, lacuna
    >= MOTOR_GAP_MIN_STEPS, e para subdivision='eighth' so ativa steps
    pares. Mesma disciplina do arp custom, especializada para motor."""
    if len(steps) != STEPS_PER_BAR:
        raise ValueError(
            f"custom motor steps must have length {STEPS_PER_BAR}; "
            f"got {len(steps)}"
        )
    normalized = tuple(1 if s else 0 for s in steps)
    if _max_gap_run(normalized) < MOTOR_GAP_MIN_STEPS:
        raise ValueError(
            f"custom motor pattern has no gap of {MOTOR_GAP_MIN_STEPS}+ "
            f"consecutive rest steps — rejected per AC"
        )
    if subdivision == "eighth":
        for i, active in enumerate(normalized):
            if active and i % 2 != 0:
                raise ValueError(
                    f"custom motor pattern activates odd step {i} — "
                    f"subdivision 'eighth' must land on even steps only"
                )
    return normalized


# --- shadow (US-007) -------------------------------------------------------

SHADOW_ROLES: tuple[str, ...] = ("shadow",)
"""Role atendido por `generate_shadow`. Vocabulario fechado."""

SHADOW_OCTAVE_SHIFT_ALLOWED: tuple[int, ...] = (-12, 12)
"""AC: 'uma oitava acima ou abaixo'. Vocabulario fechado — outras
transposicoes descaracterizam a figura shadow."""

SHADOW_DEFAULT_OCTAVE_SHIFT: int = 12
"""Default acima — brilho sutil sem competir com o grave da guitarra."""

SHADOW_DEFAULT_TAIL_NOTES: int = 2
"""Numero default de notas do FIM de cada frase que o shadow dobra. AC
usa plural ('ultimos EVENTOS de cada frase') — pegar so a ultima nota
subestima a intencao; 2 notas seguram o gesto de fechamento."""

SHADOW_PHRASE_END_GAP_S: float = 0.5
"""Fallback quando a secao nao tem unisono: gap >= 0.5s entre nota de
guitarra e a proxima conta como fim de frase. Meio segundo cobre uma
semibreve a 240bpm sem confundir semi-frases dentro de um riff denso.

Criterio de fim de frase (documentado no codigo, exigido pela AC):
  1) Se ha ancoras de unisono na secao: cada unisono delimita o INICIO
     de uma frase nova; entao a ultima nota de guitarra antes do proximo
     unisono e o fim da frase corrente. As `SHADOW_DEFAULT_TAIL_NOTES`
     ultimas notas de cada frase sao o "final" que o shadow dobra.
  2) Sem unisono na secao (fallback): uma nota de guitarra seguida por
     silencio >= SHADOW_PHRASE_END_GAP_S conta como fim de frase, e a
     mesma janela de tail_notes se aplica.
Fim de secao sempre encerra a frase corrente."""

SHADOW_DEFAULT_VELOCITY_OFFSET: int = -25
"""Shadow deve ter envelope diferente da guitarra — reduzir velocity
em 25 e a intervencao mais barata que respeita 'nunca dobra identico'
(AC). Rodada 3 pode configurar timbre distinto no plugin, mas mesmo com
o mesmo timbre a diferenca de velocity + duracao ja separa o shadow."""

SHADOW_MIN_VELOCITY: int = 30
SHADOW_MAX_VELOCITY: int = 100
"""Faixa protetora: shadow nunca some (>=30) nem sobe ao nivel de acento
(<=100). Vestigio do 'shadow como sombra' — presente, nao dominante."""

SHADOW_DEFAULT_DURATION_S: float = 0.35
"""Duracao default de cada nota do shadow — 350ms cobre um respiro sem
sobrar em riff denso. Se a proxima nota de guitarra chega antes, o
gerador corta em `next_gn.start - MIN_NOTE_DURATION_S`."""

SHADOW_DEFAULT_ARTICULATION: str = "sustained"
"""AC: envelope diferente. Sustained e o oposto do staccato tipico de
guitarra metal — release longo, o oposto do transiente de picking."""

DEFAULT_SHADOW_REGISTER: tuple[int, int] = REGISTER_BANDS["mid"]
"""Registro default do shadow: banda mid. Muitas guitarras vivem em low
(<C3); shadow +12 as coloca em mid. Configuravel por elemento."""


def _guitar_notes_in_section(
    analysis: Analysis, section: PlanSection,
) -> list[GuitarNote]:
    """Devolve GuitarNotes cujo onset caia dentro dos bars declarados na
    secao. Usa `_bars_in_section` para respeitar o mesmo criterio dos
    outros geradores."""
    bars = _bars_in_section(section, analysis)
    if not bars:
        return []
    start_s = bars[0].start
    end_s = bars[-1].end
    result: list[GuitarNote] = []
    for gn in analysis.guitar_notes:
        if gn.start < start_s:
            continue
        if gn.start >= end_s:
            break
        result.append(gn)
    return result


def _unisons_in_section(
    analysis: Analysis, section: PlanSection,
) -> list[float]:
    """Ancoras de unisono cujo tempo caia dentro do range dos bars da
    secao. Ordem preservada (analysis ja emite ordenado)."""
    bars = _bars_in_section(section, analysis)
    if not bars:
        return []
    start_s = bars[0].start
    end_s = bars[-1].end
    return [u for u in analysis.guitar_unison_positions if start_s <= u < end_s]


def _phrases_by_unisons(
    guitar_notes: list[GuitarNote],
    unison_positions: list[float],
    section_end_s: float,
) -> list[list[GuitarNote]]:
    """Fragmenta `guitar_notes` em frases usando ancoras de unisono.

    Criterio (documentado na constante `SHADOW_PHRASE_END_GAP_S`):
      - Notas com onset < primeiro unisono formam a "pre-frase" se
        existirem.
      - Entre unisonos u_i e u_{i+1}, notas em [u_i, u_{i+1}) formam
        uma frase; ultima frase estende ate section_end_s.
    Frases vazias sao descartadas — a lista final contem apenas
    frases com pelo menos uma nota."""
    if not guitar_notes:
        return []
    if not unison_positions:
        return []
    phrases: list[list[GuitarNote]] = []
    pre = [gn for gn in guitar_notes if gn.start < unison_positions[0]]
    if pre:
        phrases.append(pre)
    for i, u in enumerate(unison_positions):
        end = unison_positions[i + 1] if i + 1 < len(unison_positions) else section_end_s
        phrase = [gn for gn in guitar_notes if u <= gn.start < end]
        if phrase:
            phrases.append(phrase)
    return phrases


def _phrases_by_silence(
    guitar_notes: list[GuitarNote], min_gap_s: float,
) -> list[list[GuitarNote]]:
    """Fallback quando a secao nao tem unisono: quebra `guitar_notes` em
    frases quando o gap para a proxima nota >= min_gap_s. A ultima nota
    sempre fecha a frase corrente."""
    if not guitar_notes:
        return []
    phrases: list[list[GuitarNote]] = []
    current: list[GuitarNote] = [guitar_notes[0]]
    for prev, nxt in zip(guitar_notes, guitar_notes[1:], strict=False):
        if nxt.start - prev.start >= min_gap_s:
            phrases.append(current)
            current = [nxt]
        else:
            current.append(nxt)
    phrases.append(current)
    return phrases


def _clamp_pitch_to_register(pitch: int, register: tuple[int, int]) -> int | None:
    """Tenta encaixar `pitch` em `register` transpondo em oitavas.
    Devolve None quando nenhuma oitava cabe (registro estreito demais
    ate para uma unica pitch class do input)."""
    lo, hi = register
    p = pitch
    while p > hi:
        p -= 12
    while p < lo:
        p += 12
    if lo <= p <= hi:
        return p
    return None


def generate_shadow(
    analysis: Analysis,
    section: PlanSection,
    *,
    role: str = "shadow",
    register: tuple[int, int] = DEFAULT_SHADOW_REGISTER,
    layers: int = 1,
    articulation: str = SHADOW_DEFAULT_ARTICULATION,
    dynamics: dict | None = None,
    octave_shift: int = SHADOW_DEFAULT_OCTAVE_SHIFT,
    tail_notes: int = SHADOW_DEFAULT_TAIL_NOTES,
    phrase_end_gap_s: float = SHADOW_PHRASE_END_GAP_S,
    velocity_offset: int = SHADOW_DEFAULT_VELOCITY_OFFSET,
    note_duration_s: float = SHADOW_DEFAULT_DURATION_S,
    seed: int = 0,
    profile: StyleProfile | None = None,
) -> list[RhythmicLayer]:
    """Gera camadas de shadow — dobra o FIM das frases de guitarra.

    Args:
      analysis: saida de analise da rodada 1. Consome `guitar_notes` e
        `guitar_unison_positions`.
      section: secao alvo.
      role: 'shadow'.
      register: (low, high) MIDI. Se o pitch shiftado nao cabe, o gerador
        tenta oitavas alternativas antes de descartar a nota.
      layers: camadas em unisono, cada uma vira uma track.
      articulation: mapeado para gate. Default 'sustained' — parte da
        diferenca de envelope em relacao a guitarra.
      dynamics: nao usado no shadow (ignorado); presente por simetria com
        os outros generadores.
      octave_shift: +12 (acima) ou -12 (abaixo). AC.
      tail_notes: quantas notas do fim de cada frase o shadow dobra.
      phrase_end_gap_s: gap em segundos que define fim de frase quando
        nao ha unisono na secao (fallback).
      velocity_offset: delta em relacao a `SHADOW_MAX_VELOCITY`. Negativo
        significa envelope mais suave que a guitarra — AC 'envelope
        diferente'.
      note_duration_s: duracao base da nota do shadow. Corte se a proxima
        nota de guitarra chega antes.
      seed: seed determinstica.
      profile: `StyleProfile` opcional — sobrepoe `gate_ratios[articulation]`
        lido de `tools/constants.py`. Sem `profile`, usa
        `StyleProfile.default()` (chamador antigo continua byte-identico).

    Raises:
      ValueError: layers < 1, role != 'shadow', octave_shift fora de
        {-12, +12}, tail_notes < 1 ou phrase_end_gap_s <= 0.
    """
    assert_traceable_seed(seed, source="palette.rhythmic.generate_shadow")
    if layers < 1:
        raise ValueError(f"layers must be >= 1; got {layers}")
    if role not in SHADOW_ROLES:
        raise ValueError(
            f"role must be one of {list(SHADOW_ROLES)}; got {role!r}"
        )
    if octave_shift not in SHADOW_OCTAVE_SHIFT_ALLOWED:
        raise ValueError(
            f"octave_shift must be one of "
            f"{list(SHADOW_OCTAVE_SHIFT_ALLOWED)}; got {octave_shift}"
        )
    if tail_notes < 1:
        raise ValueError(f"tail_notes must be >= 1; got {tail_notes}")
    if phrase_end_gap_s <= 0:
        raise ValueError(
            f"phrase_end_gap_s must be > 0; got {phrase_end_gap_s}"
        )
    _ = dynamics  # parametro reservado para simetria com outros geradores

    bars = _bars_in_section(section, analysis)
    if not bars:
        return _empty_rhythmic_layers(layers)

    guitar_in_section = _guitar_notes_in_section(analysis, section)
    unisons = _unisons_in_section(analysis, section)
    section_end_s = bars[-1].end
    if unisons:
        phrases = _phrases_by_unisons(guitar_in_section, unisons, section_end_s)
    else:
        phrases = _phrases_by_silence(guitar_in_section, phrase_end_gap_s)

    if not phrases:
        return _empty_rhythmic_layers(layers)

    # Ultimas `tail_notes` notas de cada frase = fim de frase.
    tail_events: list[GuitarNote] = []
    for phrase in phrases:
        tail_events.extend(phrase[-tail_notes:])

    # Ordena por tempo — garante order-in / order-out preservado por track.
    tail_events.sort(key=lambda gn: gn.start)

    # Base velocity aplicada com clamp final; envelope diferente = velocity
    # menor + duracao maior por articulacao sustained.
    base_velocity = max(
        SHADOW_MIN_VELOCITY,
        min(SHADOW_MAX_VELOCITY, SHADOW_MAX_VELOCITY + velocity_offset),
    )
    resolved_profile = profile or StyleProfile.default()
    articulation_ratios = resolved_profile.gate_ratios.get(articulation)
    if articulation_ratios is None:
        articulation_ratios = resolved_profile.gate_ratios[SHADOW_DEFAULT_ARTICULATION]
    gate = (articulation_ratios[0] + articulation_ratios[1]) / 2.0

    rng = random.Random(seed)

    # Um proximo-onset por evento — usado para cortar a duracao se a
    # proxima guitarra chega antes de `note_duration_s`.
    guitar_starts_sorted = sorted(gn.start for gn in guitar_in_section)

    def _next_guitar_start_after(t: float) -> float:
        for s in guitar_starts_sorted:
            if s > t:
                return s
        return section_end_s

    result: list[RhythmicLayer] = []
    for layer_idx in range(layers):
        layer_rng = random.Random(seed + (layer_idx + 1) * 1_000_003)
        stagger_s = (
            0.0 if layer_idx == 0
            else layer_rng.uniform(*MOTOR_LAYER_ONSET_STAGGER_MS) / 1000.0
        )

        notes: list[RhythmicNote] = []
        for gn in tail_events:
            candidate_pitch = gn.pitch + octave_shift
            pitch = _clamp_pitch_to_register(candidate_pitch, register)
            if pitch is None:
                continue
            start = gn.start + stagger_s
            next_start = _next_guitar_start_after(gn.start)
            max_end = min(start + note_duration_s * gate + note_duration_s, next_start - MIN_NOTE_DURATION_S)
            # `gate` amplia (sustained) — usamos base * (1 + gate) para
            # exceder a duracao "curta" da guitarra e diferenciar envelope.
            desired_end = start + note_duration_s * (1.0 + gate)
            end = min(desired_end, max_end, section_end_s)
            if end <= start:
                end = start + MIN_NOTE_DURATION_S
            velocity = max(
                SHADOW_MIN_VELOCITY,
                min(SHADOW_MAX_VELOCITY, base_velocity + rng.randint(-3, 3)),
            )
            notes.append(RhythmicNote(
                pitch=pitch, velocity=velocity,
                start_s=start, end_s=end,
            ))

        notes.sort(key=lambda n: (n.start_s, n.pitch))
        result.append(RhythmicLayer(
            index=layer_idx, notes=tuple(notes), cc_events=(),
        ))

    return result


__all__ = [
    "DEFAULT_MOTOR_REGISTER",
    "DEFAULT_RHYTHMIC_ARTICULATION",
    "DEFAULT_RHYTHMIC_REGISTER",
    "DEFAULT_SHADOW_REGISTER",
    "MIN_NOTE_DURATION_S",
    "MOTOR_BASE_VELOCITY_BUCKET",
    "MOTOR_DEFAULT_ARTICULATION",
    "MOTOR_DEFAULT_FILTER_CYCLE_BARS",
    "MOTOR_DEFAULT_SUBDIVISION",
    "MOTOR_FILTER_CYCLE_BARS_ALLOWED",
    "MOTOR_FILTER_STEPS_PER_CYCLE",
    "MOTOR_GAP_MIN_STEPS",
    "MOTOR_LAYER_ONSET_STAGGER_MS",
    "MOTOR_PATTERNS",
    "MOTOR_ROLES",
    "MOTOR_SUBDIVISION_ALLOWED",
    "MOTOR_TIMING_JITTER_MS",
    "RHYTHMIC_BASE_VELOCITY_BUCKET",
    "RHYTHMIC_DEFAULT_FILTER_CYCLE_BARS",
    "RHYTHMIC_DEFAULT_PATTERN_BARS",
    "RHYTHMIC_FILTER_CC",
    "RHYTHMIC_FILTER_CYCLE_BARS_ALLOWED",
    "RHYTHMIC_FILTER_HI",
    "RHYTHMIC_FILTER_LO",
    "RHYTHMIC_FILTER_STEPS_PER_CYCLE",
    "RHYTHMIC_GAP_MIN_STEPS",
    "RHYTHMIC_INTERLOCK_MAX_SHIFT_STEPS",
    "RHYTHMIC_INTERLOCK_WINDOW_S",
    "RHYTHMIC_LAYER_ONSET_STAGGER_MS",
    "RHYTHMIC_MUTATE_ALLOWED",
    "RHYTHMIC_PATTERNS",
    "RHYTHMIC_PATTERN_BARS_ALLOWED",
    "RHYTHMIC_ROLES",
    "RHYTHMIC_TIMING_JITTER_MS",
    "RHYTHMIC_VELOCITY_CYCLE_AMPLITUDE",
    "RHYTHMIC_VELOCITY_CYCLE_DEFAULT_BARS",
    "RhythmicCCEvent",
    "RhythmicLayer",
    "RhythmicNote",
    "SHADOW_DEFAULT_ARTICULATION",
    "SHADOW_DEFAULT_DURATION_S",
    "SHADOW_DEFAULT_OCTAVE_SHIFT",
    "SHADOW_DEFAULT_TAIL_NOTES",
    "SHADOW_DEFAULT_VELOCITY_OFFSET",
    "SHADOW_MAX_VELOCITY",
    "SHADOW_MIN_VELOCITY",
    "SHADOW_OCTAVE_SHIFT_ALLOWED",
    "SHADOW_PHRASE_END_GAP_S",
    "SHADOW_ROLES",
    "STEPS_PER_BAR",
    "generate_motor",
    "generate_rhythmic",
    "generate_shadow",
]
