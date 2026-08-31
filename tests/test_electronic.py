"""Testes dos geradores eletronicos (issue #22): hat_elec, sub, sub_drop."""

from __future__ import annotations

import statistics

import pytest

from tools.analyze import Analysis, BarAnalysis, Chord
from tools.palette.electronic import (
    HAT_PATTERN_MODES,
    SUB_FOLLOW_MODES,
    ElectronicGeneratorError,
    bars_in_section,
    generate_hat_elec,
    generate_sub,
    generate_sub_drop,
)
from tools.plan import PlanSection
from tools.techniques.index import build_index

REFERENCE_BAR_S = 60.0 / 174.0 * 4  # bar a 174bpm, mesmo BPM do manual


def _bar(index: int, bar_s: float, chord: Chord | None = None) -> BarAnalysis:
    return BarAnalysis(
        index=index, start=index * bar_s, end=(index + 1) * bar_s, chord=chord,
    )


def _analysis(
    n_bars: int = 16,
    bar_s: float = REFERENCE_BAR_S,
    key_root: int = 0,
    kick_positions: list[float] | None = None,
) -> Analysis:
    bars = [_bar(i, bar_s, Chord(root=key_root, quality="minor")) for i in range(n_bars)]
    return Analysis(
        key_root=key_root,
        bars=bars,
        kick_positions=kick_positions or [],
        snare_positions=[],
        guitar_unison_positions=[],
        track_names=[],
    )


def _section(start_bar: int = 0, end_bar: int = 16) -> PlanSection:
    return PlanSection(
        label="MAIN", kind="chorus", start_bar=start_bar, end_bar=end_bar,
        source="marker", protagonist="rhythm_machine",
        energy={"densidade": 5, "impacto": 5, "largura": 5, "altura": 5, "instabilidade": 3},
    )


def _all_notes(layers):
    out = []
    for layer in layers:
        out.extend(layer.notes)
    return sorted(out, key=lambda n: n.start_s)


def _assert_monophonic(notes) -> None:
    for i in range(len(notes) - 1):
        assert notes[i].end_s <= notes[i + 1].start_s + 1e-9, (
            f"overlap between note {i} (end={notes[i].end_s}) and "
            f"note {i + 1} (start={notes[i + 1].start_s})"
        )


# --- hat_elec -----------------------------------------------------------------

def test_hat_elec_manual_declares_expected_parameters():
    idx = build_index()
    t = idx.get("drums.hat_elec")
    assert t is not None
    names = {p.name for p in t.parameters}
    assert names == {
        "pitch", "reference_bpm", "gate_ms_at_reference_bpm",
        "velocity_range", "velocity_mean", "velocity_stdev",
        "offset_range_ms", "offset_bias_ms", "offset_stdev_ms",
    }


def test_hat_elec_pitch_never_varies():
    analysis = _analysis(n_bars=16)
    layers = generate_hat_elec(analysis, _section(), seed=1)
    notes = _all_notes(layers)
    assert len(notes) == 16 * 16  # 'sixteenth' continuous, 16 bars
    assert {n.pitch for n in notes} == {70}


def test_hat_elec_is_strictly_monophonic():
    analysis = _analysis(n_bars=16)
    notes = _all_notes(generate_hat_elec(analysis, _section(), seed=7))
    _assert_monophonic(notes)


def test_hat_elec_statistics_match_manual_over_many_notes():
    """AC (issue #22): 'Teste gera 255 notas e verifica cada estatistica
    contra a faixa esperada'. Aqui: 16 bars * 16 steps + parte de um 17o
    bar com pattern com lacunas para fechar em 255 amostras."""
    analysis = _analysis(n_bars=17)
    section = _section(start_bar=0, end_bar=17)
    notes_16 = _all_notes(generate_hat_elec(analysis, section, pattern_mode="sixteenth", seed=3))
    # 17 bars completos de 16 = 272; corta pros ultimos 255 pra bater com o
    # tamanho de amostra citado na issue, sem perder generalidade estatistica.
    notes = notes_16[:255]
    assert len(notes) == 255

    velocities = [n.velocity for n in notes]
    assert all(79 <= v <= 113 for v in velocities)
    mean_v = statistics.mean(velocities)
    stdev_v = statistics.pstdev(velocities)
    assert 90 <= mean_v <= 100, f"mean velocity {mean_v} not close to manual's 95"
    assert 3 <= stdev_v <= 13, f"velocity stdev {stdev_v} not close to manual's 8"

    # Gate a 174bpm (== reference_bpm do manual): a faixa medida e 83-86ms;
    # apos o clamp de monofonia (offset pode encostar duracoes) a mediana
    # ainda deve ficar perto do topo dessa faixa.
    durations_ms = sorted((n.end_s - n.start_s) * 1000 for n in notes)
    median_ms = statistics.median(durations_ms)
    assert 78 <= median_ms <= 86.5, f"median gate {median_ms}ms not close to manual's 83-86ms"
    assert max(durations_ms) <= 86.3

    _assert_monophonic(sorted(notes, key=lambda n: n.start_s))


def test_hat_elec_offset_has_early_bias():
    """AC: 'Offset +-20ms com vies de -4ms (levemente adiantado)'."""
    analysis = _analysis(n_bars=17)
    section = _section(start_bar=0, end_bar=17)
    notes = _all_notes(generate_hat_elec(analysis, section, pattern_mode="sixteenth", seed=11))
    step_s = REFERENCE_BAR_S / 16
    offsets_ms = []
    for i, n in enumerate(notes):
        bar_idx, step_idx = divmod(i, 16)
        grid_onset = bar_idx * REFERENCE_BAR_S + step_idx * step_s
        offsets_ms.append((n.start_s - grid_onset) * 1000)
    assert all(-20.5 <= o <= 20.5 for o in offsets_ms)
    mean_offset = statistics.mean(offsets_ms)
    assert mean_offset < 0, "offset should be biased early (negative)"
    assert -10 <= mean_offset <= -1, f"mean offset {mean_offset} not close to manual's -4ms"


def test_hat_elec_gate_scales_with_real_bpm():
    """Gate deve escalar com o BPM real do arquivo, nao ficar fixo em ms."""
    fast = _analysis(n_bars=4, bar_s=REFERENCE_BAR_S)          # 174bpm
    slow = _analysis(n_bars=4, bar_s=REFERENCE_BAR_S * 2)      # 87bpm

    fast_notes = _all_notes(generate_hat_elec(fast, _section(end_bar=4), seed=5))
    slow_notes = _all_notes(generate_hat_elec(slow, _section(end_bar=4), seed=5))

    fast_median = statistics.median((n.end_s - n.start_s) for n in fast_notes)
    slow_median = statistics.median((n.end_s - n.start_s) for n in slow_notes)
    assert slow_median > fast_median * 1.5, (
        "halving the bpm should roughly double the gate duration"
    )


@pytest.mark.parametrize("mode", HAT_PATTERN_MODES)
def test_hat_elec_pattern_modes_are_all_generatable(mode):
    analysis = _analysis(n_bars=4)
    notes = _all_notes(generate_hat_elec(analysis, _section(end_bar=4), pattern_mode=mode, seed=2))
    assert notes


def test_hat_elec_gaps_and_half_time_are_less_dense_than_continuous():
    analysis = _analysis(n_bars=4)
    counts = {
        mode: len(_all_notes(generate_hat_elec(
            analysis, _section(end_bar=4), pattern_mode=mode, seed=2,
        )))
        for mode in HAT_PATTERN_MODES
    }
    assert counts["sixteenth"] == 64
    assert counts["gaps"] < counts["sixteenth"]
    assert counts["half_time"] < counts["gaps"]


def test_hat_elec_rejects_unknown_pattern_mode():
    analysis = _analysis(n_bars=1)
    with pytest.raises(ElectronicGeneratorError, match="pattern_mode"):
        generate_hat_elec(analysis, _section(end_bar=1), pattern_mode="triplet", seed=1)


# --- sub ------------------------------------------------------------------

def test_sub_manual_declares_expected_parameters():
    idx = build_index()
    t = idx.get("bass.sub")
    assert t is not None
    names = {p.name for p in t.parameters}
    assert names == {"first_impact_velocity_boost", "repeat_velocity_jitter"}


@pytest.mark.parametrize("follow", SUB_FOLLOW_MODES)
def test_sub_is_always_monophonic_never_a_chord(follow):
    analysis = _analysis(n_bars=8, kick_positions=[
        i * REFERENCE_BAR_S / 4 for i in range(32)
    ])
    layers = generate_sub(
        analysis, _section(end_bar=8), register=(24, 40),
        follow=follow, degrees=(0, 3, 7), seed=9,
    )
    assert len(layers) == 1, "sub must always be a single layer"
    notes = layers[0].notes
    assert notes
    _assert_monophonic(sorted(notes, key=lambda n: n.start_s))


def test_sub_first_impact_is_louder_than_repeats():
    analysis = _analysis(n_bars=8)
    layers = generate_sub(
        analysis, _section(end_bar=8), register=(24, 40), follow="tonic", seed=4,
    )
    notes = layers[0].notes
    assert len(notes) >= 2
    first_vel = notes[0].velocity
    later = [n.velocity for n in notes[1:]]
    assert first_vel > max(later)


def test_sub_kick_mode_follows_kick_positions():
    kicks = [0.0, 0.5, 1.0, 1.5]
    analysis = _analysis(n_bars=1, bar_s=2.0, kick_positions=kicks)
    layers = generate_sub(
        analysis, _section(end_bar=1), register=(24, 40), follow="kick", seed=1,
    )
    notes = layers[0].notes
    assert [round(n.start_s, 6) for n in notes] == kicks


def test_sub_kick_mode_deduplicates_layered_kick_onsets():
    """Regressao do achado do Codex na review pos-merge da PR #68: bateria
    em camadas (duas tracks de kick soando no mesmo instante) preserva as
    duas ocorrencias em `analysis.kick_positions`. `_enforce_monophony` nao
    resolve starts iguais (mantem duracao minima positiva), entao sem
    deduplicar `follow=kick` virava polifonico apesar da garantia de
    monofonia estrita do gerador."""
    kicks = [0.0, 0.0, 0.5, 1.0, 1.0, 1.5]  # kick duplo em 0.0 e 1.0
    analysis = _analysis(n_bars=1, bar_s=2.0, kick_positions=kicks)
    layers = generate_sub(
        analysis, _section(end_bar=1), register=(24, 40), follow="kick", seed=1,
    )
    notes = layers[0].notes
    assert [round(n.start_s, 6) for n in notes] == [0.0, 0.5, 1.0, 1.5]
    _assert_monophonic(sorted(notes, key=lambda n: n.start_s))


def test_sub_rejects_unknown_follow_mode():
    analysis = _analysis(n_bars=1)
    with pytest.raises(ElectronicGeneratorError, match="follow"):
        generate_sub(
            analysis, _section(end_bar=1), register=(24, 40), follow="groove", seed=1,
        )


def test_sub_riff_mode_interprets_degrees_as_scale_degrees():
    """Regressao do achado do Codex na review pos-merge da PR #68:
    `follow=riff` somava `degrees` como semitom direto sobre a raiz, mas a
    convencao do plano inteiro (docs/arquitetura.md,
    `tools.validators.harmony.degrees_pcs`) e grau de escala 1-based sobre
    a escala do tom. Em C menor natural (`key_root=0`), grau 1 == tonica
    (pitch class 0 == C) e grau 5 == quinta (pitch class 7 == G) — nao C#
    e F, que e o que a soma direta de semitom produzia."""
    analysis = _analysis(n_bars=1, key_root=0)
    layers = generate_sub(
        analysis, _section(end_bar=1), register=(24, 40),
        follow="riff", degrees=(1, 5), seed=1,
    )
    notes = layers[0].notes
    pitch_classes = [n.pitch % 12 for n in notes]
    # Batidas alternam grau 1 (C, pc 0) e grau 5 (G, pc 7) — nunca C#
    # (pc 1) ou F (pc 5), que era o resultado da soma direta de semitom.
    assert pitch_classes == [0, 7, 0, 7]


# --- sub_drop ---------------------------------------------------------------

def test_sub_drop_manual_declares_expected_parameters():
    idx = build_index()
    t = idx.get("bass.sub_drop")
    assert t is not None
    names = {p.name for p in t.parameters}
    assert names == {"duration_beats", "pitch_bend_curve_ms", "pitch_bend_curve_steps"}


def test_sub_drop_is_always_a_single_note():
    analysis = _analysis(n_bars=4)
    event = generate_sub_drop(analysis, boundary_s=2.0, register=(24, 40), seed=1)
    assert event.note.pitch == 24
    assert event.note.start_s == 2.0


def test_sub_drop_pitch_bend_is_monotonic_descending():
    analysis = _analysis(n_bars=4)
    event = generate_sub_drop(analysis, boundary_s=0.0, register=(24, 40), seed=1)
    # O ultimo evento e o reset de canal (0), acrescentado depois da curva
    # de descida — ver test_sub_drop_resets_pitch_wheel_after_drop. A curva
    # em si (tudo antes do reset) continua monotonica descendente.
    descent = [pb.value for pb in event.pitch_bend[:-1]]
    assert len(descent) >= 2
    assert descent == sorted(descent, reverse=True)
    assert descent[0] == 0
    assert descent[-1] == -8192
    assert all(-8192 <= v <= 8191 for v in descent)


def test_sub_drop_resets_pitch_wheel_after_drop():
    """Pitch bend e estado persistente de CANAL: sem reset, a curva termina
    em -8192 e toda nota seguinte no mesmo canal (SUB_DROP_CHANNEL, canal 0,
    compartilhado com outros roles gerados) continua desafinada ao maximo
    em qualquer player SMF-compliant."""
    analysis = _analysis(n_bars=4)
    event = generate_sub_drop(analysis, boundary_s=0.0, register=(24, 40), seed=1)
    last = event.pitch_bend[-1]
    assert last.value == 0
    # O reset nunca soa antes do drop terminar de descer, nem antes da nota
    # de sub-drop terminar.
    assert last.time_s >= event.pitch_bend[-2].time_s
    assert last.time_s >= event.note.end_s


def test_bars_in_section_shared_helper():
    analysis = _analysis(n_bars=8)
    bars = bars_in_section(_section(start_bar=2, end_bar=5), analysis)
    assert [b.index for b in bars] == [2, 3, 4]
