"""Testes de `guitar.vibrato` — oscilacao real de pitch bend, nunca no ataque.

O fato oficial do bloco (`tecnicas_guitarra_midi.md` §5) e que vibrato NAO
comeca junto com o ataque — a Ample documenta o estagio "Start" existindo
exatamente para impedir que nota rapida seja vibrada. E o mesmo bloco diz que
vibrato por pitch bend de canal em power chord esta errado, porque o
guitarrista vibra UMA corda. As duas coisas viram asserção aqui.
"""

from __future__ import annotations

import mido

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

CANONICAL = "guitar.vibrato"
TICKS_PER_MS = 480 * 1000 / 500_000  # 0.96 tick/ms a 120 BPM, 480 ppq


def _long_note() -> mido.MidiFile:
    return build_track_midi([(480, 1920, 55, 100)], name="Guitar")


def _apply(mid: mido.MidiFile, **parameters) -> mido.MidiFile:
    payload = {"density": 1.0}
    payload.update(parameters)
    return apply_technique(CANONICAL, mid, seed=11, parameters=payload, tool="generic")


def test_vibrato_is_registered_as_technique_level():
    assert CANONICAL in SUPPORTED_TECHNIQUES
    assert get_technique(CANONICAL).level == "technique"


def test_without_density_the_file_comes_out_byte_identical():
    untouched = midi_bytes(_long_note())
    for parameters in ({}, {"density": 0.0}):
        result = apply_technique(
            CANONICAL, _long_note(), seed=11, parameters=parameters, tool="generic",
        )
        assert midi_bytes(result) == untouched


def test_oscillation_only_starts_after_the_manual_delay():
    """O menor atraso do manual sao 100 ms; nada de vibrato colado no ataque."""

    result = _apply(_long_note())
    wheel = pitchwheel_events(result)
    assert wheel, "a tecnica precisa escrever pitch bend"
    assert wheel[0][0] - 480 >= 100 * TICKS_PER_MS


def test_oscillation_goes_above_and_below_the_center():
    values = [value for _tick, _channel, value in pitchwheel_events(_apply(_long_note()))]
    assert max(values) > 0
    assert min(values) < 0


def test_last_event_returns_to_center_before_the_note_off():
    result = _apply(_long_note())
    last_tick, _channel, last_value = pitchwheel_events(result)[-1]
    assert last_value == 0
    assert last_tick < 480 + 1920


def test_declared_extent_commands_the_depth():
    """`extent_cents` do plano manda: 100 cents = 1 semitom = 4096 passos."""

    wide = _apply(_long_note(), extent_cents=100)
    narrow = _apply(_long_note(), extent_cents=20)

    def peak(mid: mido.MidiFile) -> int:
        return max(abs(value) for _tick, _channel, value in pitchwheel_events(mid))

    assert peak(wide) == 4096
    assert peak(narrow) < peak(wide)


def test_declared_rate_commands_the_number_of_cycles():
    slow = _apply(_long_note(), rate_hz=5)
    fast = _apply(_long_note(), rate_hz=7)

    def zero_crossings(mid: mido.MidiFile) -> int:
        return sum(
            1 for _tick, _channel, value in pitchwheel_events(mid) if value == 0
        )

    assert zero_crossings(fast) > zero_crossings(slow)


def test_note_inside_a_chord_is_never_vibrated():
    chord = build_track_midi(
        [(480, 1920, 52, 100), (480, 1920, 59, 100)], name="Guitar",
    )
    assert pitchwheel_events(_apply(chord)) == []


def test_note_too_short_for_delay_plus_one_cycle_is_skipped():
    """Atraso minimo (100 ms) + um ciclo a 7 Hz (143 ms) nao cabem em 200 ms."""

    short = build_track_midi([(480, 192, 55, 100)], name="Guitar")
    assert pitchwheel_events(_apply(short)) == []


def test_structural_notes_are_untouched_and_range_is_declared():
    source = _long_note()
    before = note_events(_long_note())
    result = _apply(source)

    assert note_events(result) == before
    ccs = [(control, value) for _t, _c, control, value in control_events(result)]
    assert ccs[:4] == [(101, 0), (100, 0), (6, 2), (38, 0)]
    assert ccs[-2:] == [(101, 127), (100, 127)]


def test_same_seed_is_deterministic_and_idempotent():
    once = _apply(_long_note())
    assert midi_bytes(once) == midi_bytes(_apply(_long_note()))
    assert midi_bytes(_apply(once)) == midi_bytes(once)
