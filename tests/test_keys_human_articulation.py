"""Testes de `keys.human_articulation` — nota longa perde o colado de 100%.

Tell numero 2 do manual (`tecnicas_teclas_midi.md` §7.9 e §8.8): nota com 100
por cento da duracao nominal, colada na seguinte. A referencia quantificada e
razao de articulacao 0.75 para toda nota acima de 100 ms (Friberg, Bresin &
Sundberg 2006). A razao e medida contra o intervalo ate o proximo ataque — e o
que faz a tecnica ser idempotente, provado abaixo.
"""

from __future__ import annotations

import mido

from tests._guitar_keys_fixtures import build_track_midi, midi_bytes, note_events
from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

CANONICAL = "keys.human_articulation"


def _legato_line() -> mido.MidiFile:
    """Tres seminimas coladas: cada nota dura exatamente ate a proxima."""

    return build_track_midi(
        [(0, 480, 60, 90), (480, 480, 62, 90), (960, 480, 64, 90)], name="Keys",
    )


def _apply(mid: mido.MidiFile, **parameters) -> mido.MidiFile:
    payload = {"density": 1.0}
    payload.update(parameters)
    return apply_technique(CANONICAL, mid, seed=17, parameters=payload, tool="generic")


def _durations(mid: mido.MidiFile) -> dict[int, int]:
    starts: dict[int, int] = {}
    durations: dict[int, int] = {}
    for tick, kind, pitch, _velocity in note_events(mid):
        if kind == "on":
            starts[pitch] = tick
        else:
            durations[pitch] = tick - starts[pitch]
    return durations


def test_human_articulation_is_registered_as_humanize_level():
    assert CANONICAL in SUPPORTED_TECHNIQUES
    assert get_technique(CANONICAL).level == "humanize"


def test_without_density_the_file_comes_out_byte_identical():
    untouched = midi_bytes(_legato_line())
    for parameters in ({}, {"density": 0.0}):
        result = apply_technique(
            CANONICAL, _legato_line(), seed=17, parameters=parameters, tool="generic",
        )
        assert midi_bytes(result) == untouched


def test_glued_notes_get_the_measured_articulation_ratio():
    durations = _durations(_apply(_legato_line()))

    assert durations[60] == 360, "0.75 do intervalo ate o proximo ataque"
    assert durations[62] == 360


def test_the_last_note_has_no_ioi_and_is_left_alone():
    """Sem proximo ataque no canal nao ha razao de articulacao a medir."""

    assert _durations(_apply(_legato_line()))[64] == 480


def test_onsets_pitches_and_velocities_are_untouched():
    source = _legato_line()
    before = [
        (tick, pitch, velocity)
        for tick, kind, pitch, velocity in note_events(source)
        if kind == "on"
    ]
    after = [
        (tick, pitch, velocity)
        for tick, kind, pitch, velocity in note_events(_apply(_legato_line()))
        if kind == "on"
    ]
    assert after == before


def test_note_shorter_than_the_manual_threshold_is_left_alone():
    """O limiar do manual sao 100 ms; 48 ticks a 120 BPM sao 50 ms."""

    staccato = build_track_midi(
        [(0, 48, 60, 90), (480, 48, 62, 90)], name="Keys",
    )
    assert midi_bytes(_apply(staccato)) == midi_bytes(
        build_track_midi([(0, 48, 60, 90), (480, 48, 62, 90)], name="Keys")
    )


def test_note_already_detached_is_not_stretched():
    """A tecnica so encurta: alongar criaria overlap que a origem nao escreveu."""

    detached = build_track_midi(
        [(0, 240, 60, 90), (480, 480, 62, 90)], name="Keys",
    )
    assert _durations(_apply(detached))[60] == 240


def test_declared_ratio_commands_the_result():
    durations = _durations(_apply(_legato_line(), razao_de_articulacao=0.5))
    assert durations[60] == 240
    assert durations[62] == 240


def test_drum_channel_is_never_articulated():
    kit = build_track_midi(
        [(0, 480, 38, 100), (480, 480, 38, 100)], channel=9, name="Drums",
    )
    assert midi_bytes(_apply(kit)) == midi_bytes(
        build_track_midi(
            [(0, 480, 38, 100), (480, 480, 38, 100)], channel=9, name="Drums",
        )
    )


def test_reapplying_does_not_shorten_again():
    """Medir contra o IOI e o que impede empilhar 0.75 sobre 0.75 (AC-20)."""

    once = _apply(_legato_line())
    assert midi_bytes(_apply(once)) == midi_bytes(once)
    assert _durations(_apply(once))[60] == 360
