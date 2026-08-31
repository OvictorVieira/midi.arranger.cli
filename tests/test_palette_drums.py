"""Testes do gerador de bateria do zero (issue #20)."""

from __future__ import annotations

import mido
import pretty_midi
import pytest

from tests.test_palette_bass import _analyze, _build_chord_source  # noqa: E402
from tools.palette.drums import (
    GM_CRASH,
    GM_HAT_CLOSED,
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
