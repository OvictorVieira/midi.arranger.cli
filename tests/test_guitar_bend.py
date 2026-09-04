"""Testes de `guitar.bend` — pre-bend com release, so em nota que soa sozinha.

O manual (`knowledge/tecnicas/tecnicas_guitarra_midi.md` §4) chama de "a regra
que quebra a musica se for esquecida" nunca deixar o pitch bend fora do centro
depois do Note Off, e e categorico que bend dentro de acorde num canal so e
impossivel. Os dois viram asserção aqui.
"""

from __future__ import annotations

import mido
import pytest

from tests._guitar_keys_fixtures import (
    build_track_midi,
    control_events,
    midi_bytes,
    note_events,
    pitchwheel_events,
)
from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)

CANONICAL = "guitar.bend"


def _line() -> mido.MidiFile:
    """Duas notas isoladas, longas o bastante para um release de bend."""

    return build_track_midi(
        [(480, 480, 55, 100), (1440, 960, 57, 100)], name="Guitar"
    )


def _apply(mid: mido.MidiFile, **parameters) -> mido.MidiFile:
    payload = {"density": 1.0}
    payload.update(parameters)
    return apply_technique(CANONICAL, mid, seed=7, parameters=payload, tool="generic")


def test_bend_is_registered_as_technique_level():
    assert CANONICAL in SUPPORTED_TECHNIQUES
    assert get_technique(CANONICAL).level == "technique"


def test_without_density_the_file_comes_out_byte_identical():
    source = _line()
    untouched = midi_bytes(_line())

    for parameters in ({}, {"density": 0.0}):
        result = apply_technique(
            CANONICAL, source, seed=7, parameters=parameters, tool="generic",
        )
        assert midi_bytes(result) == untouched


def test_pre_bend_lands_before_the_attack_and_releases_to_center():
    result = _apply(_line())
    wheel = [(tick, value) for tick, _channel, value in pitchwheel_events(result)]

    first_note_wheel = [item for item in wheel if 470 <= item[0] <= 960]
    assert first_note_wheel[0] == (479, 4096), (
        "o pre-bend tem que estar no alvo ANTES do note_on (tick 480)"
    )
    values = [value for _tick, value in first_note_wheel]
    assert values == sorted(values, reverse=True), "a rampa tem que ser monotonica"
    assert values[-1] == 0
    assert first_note_wheel[-1][0] < 960, (
        "o bend tem que voltar ao centro ANTES do note_off"
    )


def test_pitch_bend_never_stays_off_center_at_the_end_of_the_track():
    result = _apply(_line())
    assert pitchwheel_events(result)[-1][2] == 0


def test_rpn_declares_the_manual_range_and_closes_with_rpn_null():
    result = _apply(_line())
    ccs = [(control, value) for _tick, _channel, control, value in control_events(result)]

    assert ccs[:4] == [(101, 0), (100, 0), (6, 2), (38, 0)]
    assert ccs[-2:] == [(101, 127), (100, 127)]


def test_structural_notes_are_untouched():
    source = _line()
    before = note_events(_line())
    result = _apply(source)
    assert note_events(result) == before


def test_note_inside_a_chord_is_never_bent():
    """Bend em canal unico dobraria o acorde inteiro — o manual proibe."""

    chord = build_track_midi(
        [(480, 960, 52, 100), (480, 960, 59, 100), (480, 960, 64, 100)],
        name="Guitar",
    )
    result = _apply(chord)
    assert pitchwheel_events(result) == []


def test_note_shorter_than_the_manual_floor_is_not_bent():
    """`musiclab_tempo_ms` tem piso de 100 ms: 24 ticks nao cabem um release."""

    short = build_track_midi([(480, 24, 55, 100)], name="Guitar")
    assert pitchwheel_events(_apply(short)) == []


@pytest.mark.parametrize(
    ("semitons", "expected_peak"),
    [(1, 4096), (2, 8191)],
)
def test_declared_interval_commands_the_target(semitons, expected_peak):
    """Parametro do plano manda: 2 semitons batem o teto de 16383 (8191 aqui)."""

    result = _apply(_line(), musiclab_intervalo_semitons=semitons)
    assert max(value for _tick, _channel, value in pitchwheel_events(result)) == (
        expected_peak
    )


def test_declared_ramp_time_commands_the_release_length():
    """Tempo declarado no plano manda no comprimento da rampa."""

    long_note = build_track_midi([(480, 1920, 55, 100)], name="Guitar")

    def release_end(tempo_ms: int) -> int:
        result = _apply(
            build_track_midi([(480, 1920, 55, 100)], name="Guitar"),
            musiclab_tempo_ms=tempo_ms,
        )
        return max(
            tick for tick, _channel, value in pitchwheel_events(result)
            if value == 0
        )

    assert note_events(_apply(long_note)) == note_events(long_note)
    assert release_end(100) < release_end(800)


def test_same_seed_is_deterministic_and_idempotent():
    once = _apply(_line())
    again = _apply(_line())
    assert midi_bytes(once) == midi_bytes(again)

    twice = _apply(once)
    assert midi_bytes(twice) == midi_bytes(once)
