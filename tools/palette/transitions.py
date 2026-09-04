"""Geradores de eventos de transicao (issue #23): riser, downer, impact e
reverse/meia-lua — os eventos que costuram uma secao na seguinte. Todo
numero que caracteriza o comportamento vem de
`knowledge/tecnicas/tecnicas_transicoes_midi.md`, lido via
`tools.techniques.index.build_index()` — nunca hardcoded aqui (AGENTS.md:
"parametro mentiroso" e a mesma categoria de vicio que `_identity_apply`).

## O que este modulo entrega

- `riser`/`downer`: rampa mono-track de nota+CC (filtro CC74, expression
  CC11) que so SOBE (riser) ou so DESCE (downer), terminando ANTES do
  downbeat da secao seguinte — nunca no downbeat, nunca depois.
- `impact`: hit alinhado no downbeat, em camadas com caudas divergentes,
  ciclando por tres intensidades deterministicas (nunca sorteio sem
  origem) para o mesmo impacto nao se repetir identico ao longo da
  musica.
- `reverse`: swell/cauda reversa que RESOLVE exatamente no downbeat, com
  CC de volume (CC7) e filtro (CC74) em formato de meia-lua — sobe E
  desce, ao contrario do riser (so sobe). Suporta `freeze_pitch`/
  `freeze_velocity` para congelar o ultimo evento da secao anterior como
  fonte (modo `freeze` da issue).

## O que este modulo NAO faz nesta rodada

`false_downbeat`, `subdivision_flip` e `half_time_magnifier` (issue #23,
ultima secao) sao mecanicas menores, cada uma coberta aqui por uma funcao
pura e testada (`false_downbeat_delay_s`, `generate_subdivision_flip`,
`half_time_drum_pattern`) — mas NAO estao amarradas a um role/campo de
`element.pattern` novo no plano nesta rodada (mesmo corte de escopo que
`electronic.py` ja documenta para `perc_elec`/`vox_chop`: entregar a
mecanica testada e determinística agora, sem inventar um schema de plano
que nenhum consumidor real ainda usa). Nao ha bloco de placeholder pra
elas no manual nem role fantasma em `tools/render.py`.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from ..analyze import Analysis, find_bar
from ..rng import assert_traceable_seed
from ..techniques.index import TechniqueError, build_index
from ..validators.harmony import degrees_pcs
from .electronic import bars_in_section
from .rhythmic import MIN_NOTE_DURATION_S, RhythmicNote

RISER_ROLES: tuple[str, ...] = ("riser",)
DOWNER_ROLES: tuple[str, ...] = ("downer",)
IMPACT_ROLES: tuple[str, ...] = ("impact",)
REVERSE_ROLES: tuple[str, ...] = ("reverse",)

CC_EXPRESSION: int = 11
CC_VOLUME: int = 7
CC_FILTER: int = 74

INTENSITY_LEVELS: tuple[str, ...] = ("soft", "medium", "hard")
"""Vocabulario fechado de `impact.intensity` — ciclo deterministico por
`occurrence_index % len(INTENSITY_LEVELS)`, nunca sorteio."""

SUBDIVISION_STEPS_PER_BEAT: dict[str, int] = {
    "eighth": 2,
    "triplet": 3,
    "sixteenth": 4,
}
"""Vocabulario fechado de subdivisao para `generate_subdivision_flip`."""


class TransitionGeneratorError(ValueError):
    """Parametro de plano invalido para um gerador de transicao."""


def _technique_params(canonical: str) -> dict[str, object]:
    """Le os parametros de uma tecnica do manual pelo indice — nunca
    hardcoded. `value` tem precedencia sobre `range` quando os dois
    existirem (nenhum parametro deste manual declara os dois)."""
    idx = build_index()
    t = idx.get(canonical)
    if t is None:
        raise TechniqueError(
            f"manual de tecnicas nao declara {canonical!r} — "
            f"knowledge/tecnicas/tecnicas_transicoes_midi.md esta ausente ou incompleto"
        )
    out: dict[str, object] = {}
    for p in t.parameters:
        out[p.name] = p.value if p.value is not None else p.range
    return out


def _bar_duration_s(analysis: Analysis, boundary_s: float) -> float:
    """Duracao em segundos do compasso que contem `boundary_s`, ou do
    ultimo compasso da grade quando `boundary_s` cai exatamente na borda
    final (fronteira da ultima secao — `find_bar` so acha compasso que
    CONTEM o instante). Sem grade nenhuma (analise sintetica vazia em
    teste), cai num fallback de 2s (120bpm em 4/4) que nunca e alcancado
    pelo motor de verdade — `analyze()` sempre devolve `bars` a partir de
    um MIDI real."""
    bar = find_bar(analysis, boundary_s)
    if bar is not None:
        return bar.end - bar.start
    if analysis.bars:
        last = analysis.bars[-1]
        return last.end - last.start
    return 2.0


def _pitch_near_target(pitch_class: int, target: float, register: tuple[int, int]) -> int:
    """Pitch mais proximo de `target` cuja classe e `pitch_class % 12`,
    dentro de `register`. Mesma ideia de `electronic._root_pitch_in_register`,
    generalizada para um alvo movel (a rampa de riser/downer sobe/desce
    dentro do registro, nao fica presa numa unica altura)."""
    lo, hi = register
    base = int(round(target))
    candidate = base - ((base - pitch_class) % 12)
    while candidate < lo:
        candidate += 12
    while candidate > hi:
        candidate -= 12
    return max(lo, min(hi, candidate))


# --- eventos ------------------------------------------------------------

@dataclass(frozen=True)
class CCEvent:
    """Um valor de control change num instante absoluto."""
    time_s: float
    cc: int
    value: int


@dataclass(frozen=True)
class RampEvent:
    """Notas + CC de uma rampa de riser/downer."""
    notes: tuple[RhythmicNote, ...]
    cc_events: tuple[CCEvent, ...]


@dataclass(frozen=True)
class ImpactEvent:
    """Camadas alinhadas de um impacto, com a intensidade escolhida."""
    notes: tuple[RhythmicNote, ...]
    intensity: str


@dataclass(frozen=True)
class ReverseEvent:
    """Nota + CC de um swell/cauda reversa em formato de meia-lua."""
    notes: tuple[RhythmicNote, ...]
    cc_events: tuple[CCEvent, ...]


# --- riser / downer -------------------------------------------------------

def _generate_ramp(
    analysis: Analysis,
    boundary_s: float,
    *,
    register: tuple[int, int],
    direction: int,
    duration_bars: float | None,
    degrees: tuple[int, ...] | None,
    seed: int,
    source: str,
) -> RampEvent:
    assert_traceable_seed(seed, source=source)
    params = _technique_params("transitions.riser")
    dur_lo, dur_hi = params["duration_bars_range"]
    gap_s = float(params["gap_before_boundary_ms"]) / 1000.0
    notes_per_bar = float(params["notes_per_bar"])
    vel_lo, vel_hi = params["velocity_range"]
    filt_lo, filt_hi = params["cc_filter_range"]
    expr_lo, expr_hi = params["cc_expression_range"]
    cc_steps = max(2, int(params["cc_steps"]))

    bars_dur = float(duration_bars) if duration_bars is not None else float(dur_hi)
    bars_dur = max(float(dur_lo), min(float(dur_hi), bars_dur))
    bar_s = _bar_duration_s(analysis, boundary_s)
    total_dur_s = max(MIN_NOTE_DURATION_S, bars_dur * bar_s)
    start_s = boundary_s - total_dur_s
    if start_s < 0.0:
        # Fronteira perto demais do inicio da grade (ex.: primeira secao do
        # plano comecando em t=0): a rampa nao pode olhar pra tempo
        # negativo. Encolhe a duracao efetiva em vez de estourar/crashar —
        # degrada para um evento mais curto, nunca inventa tempo antes do
        # inicio da musica.
        start_s = 0.0
        total_dur_s = max(MIN_NOTE_DURATION_S, boundary_s - start_s)
    end_s = boundary_s - gap_s
    if end_s <= start_s:
        end_s = start_s + MIN_NOTE_DURATION_S

    degs = tuple(degrees) if degrees else (1,)
    n_notes = max(1, round(notes_per_bar * bars_dur))
    step_s = total_dur_s / n_notes
    lo, hi = register

    notes: list[RhythmicNote] = []
    for i in range(n_notes):
        frac = i / max(1, n_notes - 1)
        pitch_frac = frac if direction > 0 else (1.0 - frac)
        vel_frac = frac if direction > 0 else (1.0 - frac)

        target = lo + pitch_frac * (hi - lo)
        deg = degs[i % len(degs)]
        pcs = degrees_pcs(analysis.key_root, (deg,))
        pc = next(iter(pcs)) if pcs else analysis.key_root % 12
        pitch = _pitch_near_target(pc, target, register)

        onset = min(start_s + frac * total_dur_s, end_s - MIN_NOTE_DURATION_S)
        note_dur = max(MIN_NOTE_DURATION_S, step_s * 0.85)
        note_end = min(onset + note_dur, end_s)
        if note_end <= onset:
            note_end = onset + MIN_NOTE_DURATION_S

        velocity_f = vel_lo + vel_frac * (vel_hi - vel_lo)
        velocity = int(round(max(1, min(127, velocity_f))))
        notes.append(RhythmicNote(pitch=pitch, velocity=velocity, start_s=onset, end_s=note_end))

    notes.sort(key=lambda n: n.start_s)

    cc_events: list[CCEvent] = []
    for i in range(cc_steps):
        frac = i / (cc_steps - 1)
        t = min(start_s + frac * total_dur_s, end_s)
        cc_frac = frac if direction > 0 else (1.0 - frac)
        filt_val = filt_lo + cc_frac * (filt_hi - filt_lo)
        expr_val = expr_lo + cc_frac * (expr_hi - expr_lo)
        cc_events.append(CCEvent(
            time_s=t, cc=CC_FILTER, value=int(round(max(0, min(127, filt_val)))),
        ))
        cc_events.append(CCEvent(
            time_s=t, cc=CC_EXPRESSION, value=int(round(max(0, min(127, expr_val)))),
        ))
    cc_events.sort(key=lambda e: (e.time_s, e.cc))

    return RampEvent(notes=tuple(notes), cc_events=tuple(cc_events))


def generate_riser(
    analysis: Analysis,
    boundary_s: float,
    *,
    register: tuple[int, int],
    duration_bars: float | None = None,
    degrees: tuple[int, ...] | None = None,
    seed: int,
) -> RampEvent:
    """Rampa ascendente terminando ANTES do downbeat `boundary_s` — nota e
    CC (filtro CC74, expression CC11) so sobem, nunca descem. Ver
    `knowledge/tecnicas/tecnicas_transicoes_midi.md` §2."""
    return _generate_ramp(
        analysis, boundary_s, register=register, direction=1,
        duration_bars=duration_bars, degrees=degrees, seed=seed,
        source="palette.transitions.generate_riser",
    )


def generate_downer(
    analysis: Analysis,
    boundary_s: float,
    *,
    register: tuple[int, int],
    duration_bars: float | None = None,
    degrees: tuple[int, ...] | None = None,
    seed: int,
) -> RampEvent:
    """Mesma mecanica de `generate_riser`, invertida: nota e CC descem em
    direcao ao downbeat `boundary_s`, terminando antes dele. Le os MESMOS
    parametros do manual (`transitions.riser`) — nao ha bloco de tecnica
    separado para nao duplicar numero (issue #23: 'downer e a mesma
    mecanica invertida')."""
    return _generate_ramp(
        analysis, boundary_s, register=register, direction=-1,
        duration_bars=duration_bars, degrees=degrees, seed=seed,
        source="palette.transitions.generate_downer",
    )


# --- impact -----------------------------------------------------------------

def generate_impact(
    analysis: Analysis,
    boundary_s: float,
    *,
    register: tuple[int, int],
    occurrence_index: int = 0,
    seed: int,
) -> ImpactEvent:
    """Hit alinhado no downbeat `boundary_s`, em `layer_count` camadas com
    caudas divergentes. `occurrence_index` cicla deterministicamente por
    `INTENSITY_LEVELS` (soft/medium/hard) — impactos repetidos ao longo da
    musica nao soam identicos, sem sorteio sem origem (AGENTS.md AC-21).
    `analysis` nao e usado hoje (o hit e pontual, sem depender da grade de
    compasso) — mantido na assinatura por simetria com os demais
    geradores de transicao e para uso futuro (ex.: alinhar cauda ao fim do
    compasso)."""
    assert_traceable_seed(seed, source="palette.transitions.generate_impact")
    del analysis
    params = _technique_params("transitions.impact")
    layer_count = max(1, int(params["layer_count"]))
    tails = [float(x) for x in params["tail_durations_s"]]
    bands: dict[str, tuple[float, float]] = {
        "soft": tuple(params["velocity_soft_range"]),
        "medium": tuple(params["velocity_medium_range"]),
        "hard": tuple(params["velocity_hard_range"]),
    }
    intensity = INTENSITY_LEVELS[occurrence_index % len(INTENSITY_LEVELS)]
    vel_lo, vel_hi = bands[intensity]

    rng = random.Random(seed)
    lo, hi = register
    span = hi - lo
    notes: list[RhythmicNote] = []
    for i in range(layer_count):
        tail = tails[i % len(tails)]
        pitch_frac = (i / (layer_count - 1)) if layer_count > 1 else 0.5
        pitch = max(lo, min(hi, int(round(lo + pitch_frac * span))))
        velocity = int(round(max(1, min(127, rng.uniform(vel_lo, vel_hi)))))
        end_s = boundary_s + max(MIN_NOTE_DURATION_S, tail)
        notes.append(RhythmicNote(
            pitch=pitch, velocity=velocity, start_s=boundary_s, end_s=end_s,
        ))
    return ImpactEvent(notes=tuple(notes), intensity=intensity)


# --- reverse / meia-lua -------------------------------------------------

def generate_reverse(
    analysis: Analysis,
    boundary_s: float,
    *,
    register: tuple[int, int],
    duration_bars: float | None = None,
    freeze_pitch: int | None = None,
    freeze_velocity: int | None = None,
    seed: int,
) -> ReverseEvent:
    """Swell/cauda reversa que RESOLVE exatamente no downbeat `boundary_s`
    (nota termina AI, nunca antes nem depois). CC7 (volume) e CC74
    (filtro) sobem ate `resolved_fraction` da janela e entao descem ate
    `resolved_value_ratio` do pico — formato de meia-lua, sobe E desce,
    ao contrario de `generate_riser` (so sobe). `freeze_pitch`/
    `freeze_velocity`: modo `freeze` da issue — congela o ultimo evento
    da secao anterior como fonte em vez do centro do registro declarado.
    """
    assert_traceable_seed(seed, source="palette.transitions.generate_reverse")
    params = _technique_params("transitions.reverse")
    dur_lo, dur_hi = params["duration_bars_range"]
    vol_lo, vol_hi = params["cc_volume_range"]
    filt_lo, filt_hi = params["cc_filter_range"]
    resolved_fraction = float(params["resolved_fraction"])
    resolved_ratio = float(params["resolved_value_ratio"])
    cc_steps = max(3, int(params["cc_steps"]))

    bars_dur = float(duration_bars) if duration_bars is not None else float(dur_hi)
    bars_dur = max(float(dur_lo), min(float(dur_hi), bars_dur))
    bar_s = _bar_duration_s(analysis, boundary_s)
    dur_s = max(MIN_NOTE_DURATION_S, bars_dur * bar_s)
    start_s = boundary_s - dur_s
    if start_s < 0.0:
        # Mesma degradacao de `_generate_ramp`: fronteira perto demais do
        # inicio da grade encolhe a janela em vez de olhar pra tempo
        # negativo.
        start_s = 0.0
        dur_s = max(MIN_NOTE_DURATION_S, boundary_s - start_s)

    if freeze_pitch is not None:
        pitch = max(register[0], min(register[1], int(freeze_pitch)))
    else:
        pitch = (register[0] + register[1]) // 2
    if freeze_velocity is not None:
        base_velocity = int(freeze_velocity)
    else:
        base_velocity = int(round((vol_lo + vol_hi) / 2))
    velocity = max(1, min(127, base_velocity))

    note = RhythmicNote(pitch=pitch, velocity=velocity, start_s=start_s, end_s=boundary_s)

    peak_vol, peak_filt = float(vol_hi), float(filt_hi)
    resolved_vol = vol_lo + resolved_ratio * (vol_hi - vol_lo)
    resolved_filt = filt_lo + resolved_ratio * (filt_hi - filt_lo)

    cc_events: list[CCEvent] = []
    for i in range(cc_steps):
        frac = i / (cc_steps - 1)
        t = boundary_s if i == cc_steps - 1 else start_s + frac * dur_s
        if frac <= resolved_fraction:
            rise_frac = (frac / resolved_fraction) if resolved_fraction > 0 else 1.0
            vol_val = vol_lo + rise_frac * (vol_hi - vol_lo)
            filt_val = filt_lo + rise_frac * (filt_hi - filt_lo)
        else:
            span = 1.0 - resolved_fraction
            fall_frac = (frac - resolved_fraction) / span if span > 0 else 1.0
            vol_val = peak_vol + fall_frac * (resolved_vol - peak_vol)
            filt_val = peak_filt + fall_frac * (resolved_filt - peak_filt)
        cc_events.append(CCEvent(
            time_s=t, cc=CC_VOLUME, value=int(round(max(0, min(127, vol_val)))),
        ))
        cc_events.append(CCEvent(
            time_s=t, cc=CC_FILTER, value=int(round(max(0, min(127, filt_val)))),
        ))
    cc_events.sort(key=lambda e: (e.time_s, e.cc))

    return ReverseEvent(notes=(note,), cc_events=tuple(cc_events))


# --- false_downbeat / subdivision_flip / half_time_magnifier ---------------
# Mecanicas menores da issue #23 (ultima secao). Funcoes puras e testadas,
# sem role/campo de plano proprio nesta rodada — ver docstring do modulo.

def false_downbeat_delay_s(
    analysis: Analysis, boundary_s: float, *, beats: float = 1.0,
) -> float:
    """`false_downbeat`: reverse/impact sugerem a chegada em `boundary_s`,
    mas o riff de verdade so entra `beats` tempos DEPOIS. Devolve o
    instante (segundos) em que o riff deve atacar de fato, usando a
    duracao de beat do compasso que contem (ou, na borda, do ultimo
    compasso da grade que precede) `boundary_s`."""
    bar = find_bar(analysis, boundary_s)
    if bar is None and analysis.bars:
        bar = analysis.bars[-1]
    beat_s = (bar.end - bar.start) / 4.0 if bar is not None else 0.5
    return boundary_s + beats * beat_s


def generate_subdivision_flip(
    analysis: Analysis,
    boundary_s: float,
    *,
    pitch: int,
    velocity: int = 90,
    base_subdivision: str = "eighth",
    flip_subdivision: str = "sixteenth",
    flip_bars_before_boundary: float = 1.0,
    seed: int,
) -> tuple[RhythmicNote, ...]:
    """`subdivision_flip`: a mesma nota muda de taxa de subdivisao
    (colcheia -> tercina/semicolcheia por default) `flip_bars_before_boundary`
    compassos antes do downbeat `boundary_s` (o breakdown). Onsets ANTES do
    flip usam `base_subdivision`; onsets a partir dali (ate, sem incluir,
    `boundary_s`) usam `flip_subdivision`. Monofonico, pitch fixo — so a
    densidade ritmica muda."""
    assert_traceable_seed(seed, source="palette.transitions.generate_subdivision_flip")
    if base_subdivision not in SUBDIVISION_STEPS_PER_BEAT:
        raise TransitionGeneratorError(
            f"base_subdivision must be one of {tuple(SUBDIVISION_STEPS_PER_BEAT)}; "
            f"got {base_subdivision!r}"
        )
    if flip_subdivision not in SUBDIVISION_STEPS_PER_BEAT:
        raise TransitionGeneratorError(
            f"flip_subdivision must be one of {tuple(SUBDIVISION_STEPS_PER_BEAT)}; "
            f"got {flip_subdivision!r}"
        )
    if flip_bars_before_boundary <= 0:
        raise TransitionGeneratorError(
            f"flip_bars_before_boundary must be > 0; got {flip_bars_before_boundary!r}"
        )

    bar_s = _bar_duration_s(analysis, boundary_s)
    beat_s = bar_s / 4.0
    flip_at = boundary_s - flip_bars_before_boundary * bar_s
    lookback_bars = max(2.0, flip_bars_before_boundary * 2)
    start_s = boundary_s - lookback_bars * bar_s

    notes: list[RhythmicNote] = []
    base_step_s = beat_s / SUBDIVISION_STEPS_PER_BEAT[base_subdivision]
    t = start_s
    while t < flip_at - 1e-9:
        notes.append(RhythmicNote(
            pitch=pitch, velocity=velocity, start_s=t, end_s=t + base_step_s * 0.9,
        ))
        t += base_step_s

    flip_step_s = beat_s / SUBDIVISION_STEPS_PER_BEAT[flip_subdivision]
    t = flip_at
    while t < boundary_s - 1e-9:
        notes.append(RhythmicNote(
            pitch=pitch, velocity=velocity, start_s=t, end_s=t + flip_step_s * 0.9,
        ))
        t += flip_step_s

    return tuple(notes)


def half_time_drum_pattern(base_pattern: tuple[int, ...]) -> tuple[int, ...]:
    """`half_time_magnifier`: a bateria cai em half-time enquanto outro
    elemento (ex.: arp) continua no mesmo grid rapido. Mesma convencao de
    `electronic._HAT_HALF_TIME_PATTERN`: mantem so os steps PARES do
    padrao recebido, reduzindo a densidade temporal pela metade sem
    encolher o numero de posicoes do grid — o padrao continua alinhavel
    ao elemento que NAO cai em half-time."""
    return tuple(v if i % 2 == 0 else 0 for i, v in enumerate(base_pattern))


__all__ = [
    "CC_EXPRESSION",
    "CC_FILTER",
    "CC_VOLUME",
    "DOWNER_ROLES",
    "IMPACT_ROLES",
    "INTENSITY_LEVELS",
    "REVERSE_ROLES",
    "RISER_ROLES",
    "SUBDIVISION_STEPS_PER_BEAT",
    "CCEvent",
    "ImpactEvent",
    "RampEvent",
    "ReverseEvent",
    "TransitionGeneratorError",
    "bars_in_section",
    "false_downbeat_delay_s",
    "generate_downer",
    "generate_impact",
    "generate_reverse",
    "generate_riser",
    "generate_subdivision_flip",
    "half_time_drum_pattern",
]
