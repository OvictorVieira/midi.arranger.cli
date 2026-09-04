"""Testes dos geradores de transicao (issue #23): riser, downer, impact,
reverse/meia-lua, e as tres mecanicas menores (false_downbeat,
subdivision_flip, half_time_magnifier)."""

from __future__ import annotations

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.palette.transitions import (
    CC_EXPRESSION,
    CC_FILTER,
    CC_VOLUME,
    INTENSITY_LEVELS,
    TransitionGeneratorError,
    false_downbeat_delay_s,
    generate_downer,
    generate_impact,
    generate_reverse,
    generate_riser,
    generate_subdivision_flip,
    half_time_drum_pattern,
)
from tools.techniques.index import build_index

BAR_S = 2.0  # 120bpm, 4/4


def _bar(index: int, chord: Chord | None = None) -> BarAnalysis:
    return BarAnalysis(
        index=index, start=index * BAR_S, end=(index + 1) * BAR_S, chord=chord,
    )


def _analysis(n_bars: int = 20, key_root: int = 0) -> Analysis:
    bars = [_bar(i, Chord(root=key_root, quality="minor")) for i in range(n_bars)]
    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=[],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


# --- manual -------------------------------------------------------------

def test_riser_manual_declares_expected_parameters():
    idx = build_index()
    t = idx.get("transitions.riser")
    assert t is not None
    names = {p.name for p in t.parameters}
    assert names == {
        "duration_bars_range", "gap_before_boundary_ms", "notes_per_bar",
        "velocity_range", "cc_filter_range", "cc_expression_range", "cc_steps",
    }


def test_impact_manual_declares_expected_parameters():
    idx = build_index()
    t = idx.get("transitions.impact")
    assert t is not None
    names = {p.name for p in t.parameters}
    assert names == {
        "layer_count", "velocity_soft_range", "velocity_medium_range",
        "velocity_hard_range", "tail_durations_s",
    }


def test_reverse_manual_declares_expected_parameters():
    idx = build_index()
    t = idx.get("transitions.reverse")
    assert t is not None
    names = {p.name for p in t.parameters}
    assert names == {
        "duration_bars_range", "cc_volume_range", "cc_filter_range",
        "resolved_fraction", "resolved_value_ratio", "cc_steps",
    }


def test_downer_reuses_riser_parameters_no_duplicate_manual_entry():
    """issue #23: 'downer e a mesma mecanica invertida' — nao ha bloco de
    tecnica separado para nao duplicar numero (AGENTS.md)."""
    idx = build_index()
    assert idx.get("transitions.downer") is None
    assert idx.get("transitions.riser") is not None


# --- riser ----------------------------------------------------------------

def test_riser_never_reaches_or_crosses_the_downbeat():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_riser(analysis, boundary, register=(48, 84), seed=1)
    assert event.notes[-1].end_s < boundary
    assert event.cc_events[-1].time_s < boundary
    assert all(n.end_s < boundary for n in event.notes)
    assert all(cc.time_s < boundary for cc in event.cc_events)


def test_riser_cc_curve_is_monotonically_increasing():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_riser(analysis, boundary, register=(48, 84), seed=1)
    filt = [e.value for e in event.cc_events if e.cc == CC_FILTER]
    expr = [e.value for e in event.cc_events if e.cc == CC_EXPRESSION]
    assert len(filt) >= 2 and len(expr) >= 2
    assert filt == sorted(filt)
    assert expr == sorted(expr)
    assert filt[0] < filt[-1]
    assert expr[0] < expr[-1]


def test_riser_pitch_and_velocity_climb_toward_the_boundary():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_riser(analysis, boundary, register=(48, 84), seed=1)
    velocities = [n.velocity for n in event.notes]
    assert velocities == sorted(velocities)
    assert velocities[0] < velocities[-1]


def test_riser_reuses_source_degrees_like_an_arp_pattern():
    """issue #23: 'loop principal virando ruido ascendente' — o riser cicla
    pelos MESMOS graus declarados em `element.degrees`, mesma convencao de
    grau de escala 1-based que arp/sub ja usam."""
    analysis = _analysis(key_root=0)
    boundary = 16 * BAR_S
    event = generate_riser(
        analysis, boundary, register=(48, 72), degrees=(1, 5), seed=1,
    )
    pitch_classes = {n.pitch % 12 for n in event.notes}
    assert pitch_classes <= {0, 7}  # grau 1 (C) e grau 5 (G) em C menor


def test_riser_duration_bars_is_configurable_within_manual_range():
    analysis = _analysis()
    boundary = 16 * BAR_S
    short = generate_riser(analysis, boundary, register=(48, 84), duration_bars=1.0, seed=1)
    long = generate_riser(analysis, boundary, register=(48, 84), duration_bars=2.0, seed=1)
    short_span = short.notes[-1].end_s - short.notes[0].start_s
    long_span = long.notes[-1].end_s - long.notes[0].start_s
    assert long_span > short_span


def test_riser_handles_boundary_at_the_very_start_of_the_grade():
    """Fronteira em t=0 (primeira secao do plano): a rampa nao pode olhar
    pra tempo negativo — degrada a janela em vez de estourar."""
    analysis = _analysis()
    event = generate_riser(analysis, 0.0, register=(48, 84), seed=1)
    assert all(n.start_s >= 0.0 for n in event.notes)
    assert all(cc.time_s >= 0.0 for cc in event.cc_events)


# --- downer -----------------------------------------------------------------

def test_downer_never_reaches_or_crosses_the_downbeat():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_downer(analysis, boundary, register=(48, 84), seed=1)
    assert event.notes[-1].end_s < boundary
    assert event.cc_events[-1].time_s < boundary


def test_downer_cc_curve_is_monotonically_decreasing():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_downer(analysis, boundary, register=(48, 84), seed=1)
    filt = [e.value for e in event.cc_events if e.cc == CC_FILTER]
    assert filt == sorted(filt, reverse=True)
    assert filt[0] > filt[-1]


def test_downer_pitch_and_velocity_fall_toward_the_boundary():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_downer(analysis, boundary, register=(48, 84), seed=1)
    velocities = [n.velocity for n in event.notes]
    assert velocities == sorted(velocities, reverse=True)
    assert velocities[0] > velocities[-1]


# --- riser vs meia-lua: a diferenca que nao pode colapsar (issue #23) -----

def test_riser_only_rises_reverse_rises_and_resolves_side_by_side():
    """AC explicito da issue: riser TERMINA antes do downbeat e a curva SO
    SOBE; meia-lua RESOLVE no downbeat e a curva sobe E desce. Sao coisas
    diferentes — o teste cobre os dois comportamentos lado a lado para que
    uma implementacao futura nao os colapse num so."""
    analysis = _analysis()
    boundary = 16 * BAR_S

    riser = generate_riser(analysis, boundary, register=(48, 84), seed=1)
    reverse = generate_reverse(analysis, boundary, register=(48, 72), seed=1)

    # Fim: riser termina ANTES; reverse termina EXATAMENTE no downbeat.
    assert riser.notes[-1].end_s < boundary
    assert reverse.notes[-1].end_s == boundary

    # Curva: riser e monotonica so-sobe; reverse sobe e desce (nao e
    # monotonica em nenhuma das duas direcoes).
    riser_filter = [e.value for e in riser.cc_events if e.cc == CC_FILTER]
    reverse_filter = [e.value for e in reverse.cc_events if e.cc == CC_FILTER]
    assert riser_filter == sorted(riser_filter)
    assert reverse_filter != sorted(reverse_filter)
    assert reverse_filter != sorted(reverse_filter, reverse=True)
    peak_index = reverse_filter.index(max(reverse_filter))
    assert 0 < peak_index < len(reverse_filter) - 1, (
        "meia-lua precisa ter um pico NO MEIO da curva, nem no inicio nem no fim"
    )
    assert reverse_filter[-1] < max(reverse_filter), "meia-lua precisa RESOLVER (descer) no final"


# --- impact -----------------------------------------------------------------

def test_impact_attacks_are_aligned_but_tails_diverge():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_impact(analysis, boundary, register=(24, 84), occurrence_index=0, seed=1)
    assert len(event.notes) >= 2
    assert {n.start_s for n in event.notes} == {boundary}
    tails = {round(n.end_s - n.start_s, 6) for n in event.notes}
    assert len(tails) == len(event.notes), "cada camada precisa de uma cauda diferente"


def test_impact_repeated_occurrences_cycle_through_distinct_intensities():
    """issue #23: 'Tres intensidades distintas, para o mesmo impacto nao se
    repetir identico ao longo da musica' — o ciclo e deterministico
    (occurrence_index), nunca sorteio sem origem (AGENTS.md AC-21)."""
    analysis = _analysis()
    boundary = 16 * BAR_S
    seen = [
        generate_impact(
            analysis, boundary, register=(24, 84), occurrence_index=i, seed=1,
        ).intensity
        for i in range(6)
    ]
    assert seen == [
        INTENSITY_LEVELS[i % len(INTENSITY_LEVELS)] for i in range(6)
    ]
    assert set(seen) == set(INTENSITY_LEVELS)


def test_impact_intensity_bands_do_not_overlap():
    analysis = _analysis()
    boundary = 16 * BAR_S
    velocities_by_intensity: dict[str, list[int]] = {}
    for i in range(3):
        event = generate_impact(
            analysis, boundary, register=(24, 84), occurrence_index=i, seed=7,
        )
        velocities_by_intensity[event.intensity] = [n.velocity for n in event.notes]
    assert max(velocities_by_intensity["soft"]) < min(velocities_by_intensity["medium"])
    assert max(velocities_by_intensity["medium"]) < min(velocities_by_intensity["hard"])


def test_impact_is_deterministic_given_the_same_seed():
    analysis = _analysis()
    boundary = 16 * BAR_S
    a = generate_impact(analysis, boundary, register=(24, 84), occurrence_index=0, seed=99)
    b = generate_impact(analysis, boundary, register=(24, 84), occurrence_index=0, seed=99)
    assert a.notes == b.notes
    assert a.intensity == b.intensity


# --- reverse / meia-lua -------------------------------------------------

def test_reverse_ends_exactly_at_the_downbeat():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_reverse(analysis, boundary, register=(48, 72), seed=1)
    assert len(event.notes) == 1
    assert event.notes[0].end_s == boundary
    assert event.cc_events[-1].time_s == boundary


def test_reverse_curve_rises_then_resolves():
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_reverse(analysis, boundary, register=(48, 72), seed=1)
    vol = [e.value for e in event.cc_events if e.cc == CC_VOLUME]
    peak_index = vol.index(max(vol))
    assert 0 < peak_index < len(vol) - 1
    assert vol[-1] < max(vol)
    assert vol[0] < max(vol)


def test_reverse_duration_bars_is_configurable_within_manual_range():
    analysis = _analysis()
    boundary = 16 * BAR_S
    short = generate_reverse(analysis, boundary, register=(48, 72), duration_bars=0.5, seed=1)
    long = generate_reverse(analysis, boundary, register=(48, 72), duration_bars=1.0, seed=1)
    assert (short.notes[0].end_s - short.notes[0].start_s) < (
        long.notes[0].end_s - long.notes[0].start_s
    )


def test_reverse_freeze_mode_uses_the_frozen_pitch_and_velocity():
    """Modo `freeze`: congela e reverte o ultimo evento da secao anterior
    como fonte do swell."""
    analysis = _analysis()
    boundary = 16 * BAR_S
    event = generate_reverse(
        analysis, boundary, register=(20, 100),
        freeze_pitch=67, freeze_velocity=101, seed=1,
    )
    assert event.notes[0].pitch == 67
    assert event.notes[0].velocity == 101


def test_reverse_handles_boundary_at_the_very_start_of_the_grade():
    analysis = _analysis()
    event = generate_reverse(analysis, 0.0, register=(48, 72), seed=1)
    assert event.notes[0].start_s >= 0.0
    assert event.notes[0].end_s == 0.0
    assert all(cc.time_s >= 0.0 for cc in event.cc_events)


# --- false_downbeat ---------------------------------------------------------

def test_false_downbeat_delays_the_arrival_by_n_beats():
    analysis = _analysis()
    boundary = 16 * BAR_S
    beat_s = BAR_S / 4.0
    delayed = false_downbeat_delay_s(analysis, boundary, beats=1.0)
    assert delayed == pytest.approx(boundary + beat_s)
    delayed_two = false_downbeat_delay_s(analysis, boundary, beats=2.0)
    assert delayed_two == pytest.approx(boundary + 2 * beat_s)


def test_false_downbeat_zero_beats_is_a_no_op():
    analysis = _analysis()
    boundary = 16 * BAR_S
    assert false_downbeat_delay_s(analysis, boundary, beats=0.0) == pytest.approx(boundary)


# --- subdivision_flip ---------------------------------------------------

def test_subdivision_flip_changes_onset_density_before_the_boundary():
    analysis = _analysis()
    boundary = 16 * BAR_S
    notes = generate_subdivision_flip(
        analysis, boundary, pitch=60, seed=1,
        base_subdivision="eighth", flip_subdivision="sixteenth",
        flip_bars_before_boundary=1.0,
    )
    assert notes
    assert notes[-1].start_s < boundary

    starts = [n.start_s for n in notes]
    flip_at = boundary - 1.0 * BAR_S
    before = [s for s in starts if s < flip_at]
    after = [s for s in starts if s >= flip_at]
    assert len(before) >= 2 and len(after) >= 2

    before_gaps = {round(before[i + 1] - before[i], 6) for i in range(len(before) - 1)}
    after_gaps = {round(after[i + 1] - after[i], 6) for i in range(len(after) - 1)}
    eighth_gap = (BAR_S / 4.0) / 2
    sixteenth_gap = (BAR_S / 4.0) / 4
    assert before_gaps == {round(eighth_gap, 6)}
    assert after_gaps == {round(sixteenth_gap, 6)}
    assert sixteenth_gap < eighth_gap, "flip precisa ser MAIS denso que a base"


def test_subdivision_flip_rejects_unknown_subdivision():
    analysis = _analysis()
    boundary = 16 * BAR_S
    with pytest.raises(TransitionGeneratorError, match="base_subdivision"):
        generate_subdivision_flip(
            analysis, boundary, pitch=60, seed=1, base_subdivision="quarter",
        )
    with pytest.raises(TransitionGeneratorError, match="flip_subdivision"):
        generate_subdivision_flip(
            analysis, boundary, pitch=60, seed=1, flip_subdivision="quarter",
        )


# --- half_time_magnifier -----------------------------------------------

def test_half_time_drum_pattern_halves_density_keeps_grid_length():
    base = (1,) * 16
    halved = half_time_drum_pattern(base)
    assert len(halved) == len(base)
    assert sum(halved) == sum(base) // 2
    assert halved == (1, 0) * 8


def test_half_time_magnifier_arp_stays_fast_while_drums_fall_to_half_time():
    """issue #23: 'arp continua rapido enquanto a bateria cai em
    half-time' — o padrao do arp (nao passado por `half_time_drum_pattern`)
    mantem a densidade original; so a bateria reduz."""
    arp_pattern = (1,) * 16
    drum_pattern = half_time_drum_pattern((1,) * 16)
    assert sum(arp_pattern) == 16
    assert sum(drum_pattern) == 8
    assert sum(arp_pattern) > sum(drum_pattern)
