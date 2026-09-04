"""Testes de `keys.rolled_chord` — espalhamento ACELERADO, topo no tempo.

O achado que da nome ao bloco (`tecnicas_teclas_midi.md` §6.5) e que a taxa de
rolagem NAO e constante: os intervalos entre notas sucessivas diminuem do grave
para o agudo. Espalhar um valor fixo entre cada nota e o erro classico, e e
justamente isso que os testes abaixo proibem.
"""

from __future__ import annotations

import mido

from tests._guitar_keys_fixtures import build_track_midi, midi_bytes, note_events
from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

CANONICAL = "keys.rolled_chord"
TICKS_PER_MS = 480 * 1000 / 500_000  # 0.96 tick/ms a 120 BPM, 480 ppq
BEAT = 960


def _chord() -> mido.MidiFile:
    """Acorde de quatro vozes no tempo 3, com o compasso inteiro livre antes."""

    return build_track_midi(
        [
            (BEAT, 960, 60, 90),
            (BEAT, 960, 64, 90),
            (BEAT, 960, 67, 90),
            (BEAT, 960, 72, 90),
        ],
        name="Keys",
    )


def _apply(mid: mido.MidiFile, **parameters) -> mido.MidiFile:
    payload = {"density": 1.0}
    payload.update(parameters)
    return apply_technique(CANONICAL, mid, seed=13, parameters=payload, tool="generic")


def _onsets(mid: mido.MidiFile) -> list[tuple[int, int]]:
    return [
        (tick, pitch)
        for tick, kind, pitch, _velocity in note_events(mid)
        if kind == "on"
    ]


def test_rolled_chord_is_registered_as_humanize_level():
    assert CANONICAL in SUPPORTED_TECHNIQUES
    assert get_technique(CANONICAL).level == "humanize"


def test_without_density_the_file_comes_out_byte_identical():
    untouched = midi_bytes(_chord())
    for parameters in ({}, {"density": 0.0}):
        result = apply_technique(
            CANONICAL, _chord(), seed=13, parameters=parameters, tool="generic",
        )
        assert midi_bytes(result) == untouched


def test_chord_stops_being_simultaneous_and_rolls_from_the_bottom_up():
    onsets = _onsets(_apply(_chord()))

    assert len({tick for tick, _pitch in onsets}) == 4
    assert onsets == sorted(onsets), "o rolo sobe: grave primeiro, agudo por ultimo"
    assert [pitch for _tick, pitch in onsets] == [60, 64, 67, 72]


def test_the_top_note_lands_on_the_beat():
    onsets = _onsets(_apply(_chord()))
    assert onsets[-1] == (BEAT, 72)


def test_intervals_decrease_and_total_spread_respects_the_manual_window():
    ticks = [tick for tick, _pitch in _onsets(_apply(_chord()))]
    gaps = [second - first for first, second in zip(ticks, ticks[1:], strict=False)]

    assert gaps == sorted(gaps, reverse=True)
    assert len(set(gaps)) > 1, "espalhamento uniforme e o erro que a fonte aponta"

    total_ms = (ticks[-1] - ticks[0]) / TICKS_PER_MS
    assert 30 <= total_ms <= 120


def test_declared_spread_commands_the_total():
    result = _apply(_chord(), espalhamento_total_ms=120)
    ticks = [tick for tick, _pitch in _onsets(result)]
    total_ms = (ticks[-1] - ticks[0]) / TICKS_PER_MS
    assert 115 <= total_ms <= 120


def test_durations_are_preserved_note_by_note():
    result = _apply(_chord())
    starts: dict[int, int] = {}
    durations: dict[int, int] = {}
    for tick, kind, pitch, _velocity in note_events(result):
        if kind == "on":
            starts[pitch] = tick
        else:
            durations[pitch] = tick - starts[pitch]
    assert set(durations.values()) == {960}


def test_chord_without_room_before_the_beat_is_skipped():
    """O rolo comeca ANTES do tempo: sem folga, nao ha rolo honesto."""

    glued = build_track_midi(
        [(0, 960, 60, 90), (0, 960, 64, 90), (0, 960, 67, 90)], name="Keys",
    )
    assert midi_bytes(_apply(glued)) == midi_bytes(
        build_track_midi(
            [(0, 960, 60, 90), (0, 960, 64, 90), (0, 960, 67, 90)], name="Keys",
        )
    )


def test_chord_written_out_of_ascending_order_is_skipped():
    """Rolar exigiria reordenar os note_on — o contrato `humanize` proibe."""

    descending = mido.MidiFile(ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
    track.append(mido.Message("note_on", note=72, velocity=90, time=BEAT))
    track.append(mido.Message("note_on", note=67, velocity=90, time=0))
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=72, velocity=0, time=960))
    track.append(mido.Message("note_off", note=67, velocity=0, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=0))
    descending.tracks.append(track)

    before = midi_bytes(descending)
    assert midi_bytes(_apply(descending)) == before


def test_note_count_and_pitches_are_untouched():
    source = _chord()
    before = sorted(pitch for _t, kind, pitch, _v in note_events(source) if kind == "on")
    result = _apply(_chord())
    after = sorted(pitch for _t, kind, pitch, _v in note_events(result) if kind == "on")
    assert after == before


def test_same_seed_is_deterministic_and_reapplying_is_stable():
    once = _apply(_chord())
    assert midi_bytes(once) == midi_bytes(_apply(_chord()))
    assert midi_bytes(_apply(once)) == midi_bytes(once)
