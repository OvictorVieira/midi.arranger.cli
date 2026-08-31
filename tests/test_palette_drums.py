"""Testes do gerador de bateria do zero (issue #20)."""

from __future__ import annotations

import mido
import pretty_midi
import pytest

from tests.test_palette_bass import _analyze, _build_chord_source  # noqa: E402
from tools.analyze import Analysis, BarAnalysis
from tools.palette.drums import (
    GM_CRASH,
    GM_HAT_CLOSED,
    GM_KICK,
    GM_RIDE,
    GM_TOM_HIGH,
    GM_TOM_LOW,
    GM_TOM_MID,
    generate_drums,
)
from tools.plan import PlanSection
from tools.techniques.physical import validate_physical_plausibility


def _section(*, start_bar=0, end_bar=8, **energy_overrides) -> PlanSection:
    energy = {
        "densidade": 5, "impacto": 5, "largura": 5,
        "altura": 5, "instabilidade": 5,
    }
    energy.update(energy_overrides)
    return PlanSection(
        label="MAIN", kind="verse", start_bar=start_bar, end_bar=end_bar,
        source="marker", energy=energy,
    )


# --- plausibilidade fisica ---------------------------------------------------

def _drums_track_before_after(
    notes,
    pm: pretty_midi.PrettyMIDI,
) -> tuple[mido.MidiFile, mido.MidiFile]:
    """Constroi (before, after) mido.MidiFile — before com a track de
    bateria vazia (so meta track_name), after com todas as notas geradas.
    Reusa DIRETAMENTE `validate_physical_plausibility` de
    tools/techniques/physical.py em vez de reimplementar a checagem de
    maos/pes: como nao ha estado anterior real (a track e nova), toda nota
    gerada conta como 'nova' no diff, exatamente a mesma semantica que a
    funcao ja usa para ornamento de tecnica."""
    before = mido.MidiFile(ticks_per_beat=480, type=1)
    before_track = mido.MidiTrack()
    before_track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    before.tracks.append(before_track)

    after = mido.MidiFile(ticks_per_beat=480, type=1)
    after_track = mido.MidiTrack()
    after_track.append(mido.MetaMessage("track_name", name="Drums", time=0))
    events: list[tuple[int, int, int, int]] = []
    for n in notes:
        start_tick = int(round(pm.time_to_tick(n.start_s)))
        end_tick = int(round(pm.time_to_tick(n.end_s)))
        if end_tick <= start_tick:
            end_tick = start_tick + 1
        events.append((start_tick, 1, int(n.pitch), int(n.velocity)))
        events.append((end_tick, 0, int(n.pitch), 0))
    events.sort()
    prev = 0
    for tick, kind, pitch, vel in events:
        delta = tick - prev
        prev = tick
        if kind == 1:
            after_track.append(mido.Message(
                "note_on", note=pitch, velocity=vel, channel=9, time=delta,
            ))
        else:
            after_track.append(mido.Message(
                "note_off", note=pitch, velocity=0, channel=9, time=delta,
            ))
    after.tracks.append(after_track)
    return before, after


def test_generate_drums_respects_physical_plausibility(tmp_path):
    src = _build_chord_source(tmp_path)
    pm = pretty_midi.PrettyMIDI(str(src))
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=8, densidade=8, impacto=8, instabilidade=8)
    layers = generate_drums(analysis, section, seed=7)
    notes = layers[0].notes
    assert len(notes) > 20

    before, after = _drums_track_before_after(notes, pm)
    # Nao deve levantar TechniquePhysicalError — duas maos, dois pes.
    validate_physical_plausibility("drums.generated", before, after, {})


# --- hat vs ride pela intensidade -------------------------------------------

def test_hat_or_ride_chosen_by_section_intensity(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)

    calm = _section(impacto=2)
    intense = _section(impacto=9)

    calm_notes = generate_drums(analysis, calm, seed=3)[0].notes
    intense_notes = generate_drums(analysis, intense, seed=3)[0].notes

    calm_pitches = {n.pitch for n in calm_notes}
    intense_pitches = {n.pitch for n in intense_notes}

    assert GM_HAT_CLOSED in calm_pitches
    assert GM_RIDE not in calm_pitches
    assert GM_RIDE in intense_pitches
    assert GM_HAT_CLOSED not in intense_pitches


# --- virada nas fronteiras ---------------------------------------------------

def test_last_bar_of_section_carries_a_fill(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    section = _section(start_bar=0, end_bar=4)
    notes = generate_drums(analysis, section, seed=11)[0].notes

    bars = analysis.bars[0:4]
    last_bar = bars[-1]
    last_bar_notes = [n for n in notes if last_bar.start <= n.start_s < last_bar.end]
    tom_pitches = {GM_TOM_HIGH, GM_TOM_MID, GM_TOM_LOW}
    assert any(n.pitch in tom_pitches for n in last_bar_notes), (
        "last bar of the section must carry a tom fill (virada de fronteira)"
    )

    first_bar = bars[0]
    first_bar_notes = [n for n in notes if first_bar.start <= n.start_s < first_bar.end]
    assert any(n.pitch == GM_CRASH for n in first_bar_notes), (
        "first bar of the section must carry a crash accent (entrada de secao)"
    )


# --- dinamica: nao e a mesma levada do inicio ao fim -------------------------

def test_groove_differs_between_low_and_high_energy_sections(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)

    calm = _section(densidade=2, impacto=2, instabilidade=1)
    intense = _section(densidade=9, impacto=9, instabilidade=9)

    calm_notes = generate_drums(analysis, calm, seed=5)[0].notes
    intense_notes = generate_drums(analysis, intense, seed=5)[0].notes

    assert len(intense_notes) > len(calm_notes), (
        "higher energy section must produce a denser groove — "
        "same plain groove start to end is the anti-pattern rejected by the project"
    )


# --- densidade acompanha o eixo energy.densidade -----------------------------

def test_note_count_grows_monotonically_with_density_axis(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)

    low = _section(densidade=1)
    mid = _section(densidade=5)
    high = _section(densidade=10)

    low_count = len(generate_drums(analysis, low, seed=9)[0].notes)
    mid_count = len(generate_drums(analysis, mid, seed=9)[0].notes)
    high_count = len(generate_drums(analysis, high, seed=9)[0].notes)

    assert low_count < mid_count <= high_count


# --- validacoes de entrada ----------------------------------------------------

# --- achados Codex PR #69 ------------------------------------------------------

def test_generate_drums_never_leaks_into_the_bar_before_the_section(tmp_path):
    """Secao que comeca depois do bar 0 nao pode ter golpe do step 0
    (hat/kick/crash) vazando para o bar ANTERIOR quando `_jitter_s`
    sorteia offset negativo — `_emit_hit` clampava so em 0.0 absoluto, nao
    na fronteira do bar atual. Varre varias seeds porque o vazamento so
    aparece quando o sinal do jitter sorteado e negativo."""
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    # secao comeca no bar 4 (nao no bar 0) — o bar anterior (3) existe na
    # analise mas fica FORA da secao declarada do elemento.
    section = _section(start_bar=4, end_bar=8)
    section_start_s = next(b.start for b in analysis.bars if b.index == 4)
    assert section_start_s > 0.0, "fixture must place the section after bar 0"

    for seed in range(30):
        notes = generate_drums(analysis, section, seed=seed)[0].notes
        leaking = [n for n in notes if n.start_s < section_start_s - 1e-9]
        assert leaking == [], (
            f"seed={seed}: notes must never start before the section's own "
            f"bar boundary ({section_start_s}s); leaked into the previous "
            f"bar: {leaking}"
        )


def test_generate_drums_recomputes_step_duration_per_bar():
    """`step_dur_s` tem que ser recalculado por bar (`bar.end - bar.start`),
    nao derivado uma unica vez do primeiro bar da secao. Fixture com bar 1
    de duracao METADE do bar 0 (mudanca de tempo/compasso no meio da
    secao): se `step_dur_s` do bar 0 (2.0s / 16 = 0.125s) for reusado no
    bar 1 (duracao real 1.0s), o kick do step 8 cairia em
    2.0 + 8*0.125 = 3.0s — exatamente na fronteira/fora do bar 1
    (que termina em 3.0s) — em vez do correto 2.0 + 8*0.0625 = 2.5s."""
    bars = [
        BarAnalysis(index=0, start=0.0, end=2.0, chord=None),
        BarAnalysis(index=1, start=2.0, end=3.0, chord=None),
        BarAnalysis(index=2, start=3.0, end=5.0, chord=None),
    ]
    analysis = Analysis(
        key_root=0, bars=bars, kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
    )
    section = _section(start_bar=0, end_bar=3)

    for seed in range(10):
        notes = generate_drums(analysis, section, seed=seed)[0].notes
        bar1_kicks = [
            n for n in notes
            if n.pitch == GM_KICK and bars[1].start <= n.start_s < bars[1].end
        ]
        assert bar1_kicks, f"seed={seed}: bar 1 must carry at least one kick"
        assert all(n.start_s < bars[1].end - 0.01 for n in bar1_kicks), (
            f"seed={seed}: bar 1 kicks must use bar 1's own duration "
            f"(1.0s), not bar 0's (2.0s) — got {[n.start_s for n in bar1_kicks]} "
            f"against bar1 end {bars[1].end}"
        )


def test_generate_drums_rejects_invalid_layers(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    with pytest.raises(ValueError):
        generate_drums(analysis, _section(), layers=0)


def test_generate_drums_rejects_invalid_role(tmp_path):
    src = _build_chord_source(tmp_path)
    analysis = _analyze(src)
    with pytest.raises(ValueError):
        generate_drums(analysis, _section(), role="bass")


def test_generate_drums_empty_section_returns_empty_layers():
    from tools.analyze import Analysis

    empty_analysis = Analysis(
        key_root=0, bars=[], kick_positions=[], snare_positions=[],
        guitar_unison_positions=[], track_names=[],
    )
    layers = generate_drums(empty_analysis, _section(start_bar=0, end_bar=8), seed=1)
    assert len(layers) == 1
    assert layers[0].notes == ()
