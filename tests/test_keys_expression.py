"""Testes de `keys.expression` — CC11 (dinamica), NUNCA CC7 (fader)."""

from __future__ import annotations

import mido
import pytest

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)


def _make_line(
    events: list[tuple[int, int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 0,
    tempo_us: int = 500_000,
) -> mido.MidiFile:
    """events: list of (start_tick_absolute, duration_ticks, pitch, velocity)."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Keys", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=tempo_us, time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch, velocity in events:
        absolute.append((
            start, order,
            mido.Message(
                "note_on", channel=channel, note=pitch, velocity=velocity, time=0,
            ),
        ))
        order += 1
        absolute.append((
            start + duration, order,
            mido.Message(
                "note_off", channel=channel, note=pitch, velocity=0, time=0,
            ),
        ))
        order += 1
    prev = 0
    for absolute_tick, _order, msg in sorted(
        absolute, key=lambda item: (item[0], item[1]),
    ):
        track.append(msg.copy(time=absolute_tick - prev))
        prev = absolute_tick
    mid.tracks.append(track)
    return mid


def _collect_cc(
    mid: mido.MidiFile, cc: int
) -> list[tuple[int, int, int, int]]:
    events: list[tuple[int, int, int, int]] = []
    for track_index, track in enumerate(mid.tracks):
        tick = 0
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "control_change" and msg.control == cc:
                events.append((track_index, tick, msg.channel, msg.value))
    return events


def _collect_notes(mid: mido.MidiFile) -> list[dict]:
    notes: list[dict] = []
    for track in mid.tracks:
        tick = 0
        pending: dict[tuple[int, int], list[dict]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                entry = {
                    "channel": msg.channel,
                    "pitch": msg.note,
                    "start": tick,
                    "velocity": msg.velocity,
                    "end": None,
                }
                pending.setdefault((msg.channel, msg.note), []).append(entry)
                notes.append(entry)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get((msg.channel, msg.note))
                if stack:
                    stack.pop(0)["end"] = tick
    return [n for n in notes if n["end"] is not None]


def test_keys_expression_is_registered_as_supported():
    assert "keys.expression" in SUPPORTED_TECHNIQUES
    entry = get_technique("keys.expression")
    assert entry.canonical == "keys.expression"
    assert entry.level == "technique"


def test_keys_expression_without_density_is_noop():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique("keys.expression", line, seed=1)
    assert _collect_cc(out, 11) == []
    assert _collect_cc(out, 7) == []


def test_keys_expression_zero_density_is_noop():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 0.0},
    )
    assert _collect_cc(out, 11) == []


def test_keys_expression_never_emits_cc7_volume():
    line = _make_line([(0, 480, 60, 96), (480, 480, 62, 96)])
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 1.0},
    )
    assert _collect_cc(out, 7) == [], "CC7 e o fader — jamais emitido por keys.expression"


def test_keys_expression_never_emits_cc11_lsb():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 1.0},
    )
    assert _collect_cc(out, 43) == [], "CC43 (LSB de CC11) nao e emitido em passos inteiros"


def test_keys_expression_starts_and_ends_at_default_127():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 1.0},
    )
    cc11 = _collect_cc(out, 11)
    assert cc11, "CC11 tem que aparecer quando density > 0"
    values = [value for _, _, _, value in cc11]
    # A curva SO se afasta do default e RETORNA a 127.
    assert values[-1] == 127, "envelope precisa retornar ao default (127)"
    # Envelope: desce para valley e sobe de volta a 127.
    valley_value = min(values)
    assert valley_value < 127, "envelope precisa se afastar do default"
    valley_index = values.index(valley_value)
    assert values[:valley_index + 1] == sorted(values[:valley_index + 1], reverse=True)
    assert values[valley_index:] == sorted(values[valley_index:])


def test_keys_expression_default_valley_matches_sourced_minus_11_9_db():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 1.0},
    )
    cc11 = _collect_cc(out, 11)
    valley = min(value for _, _, _, value in cc11)
    # CONVENCAO: dip default=63 → valley em CC11=64 (db_em_cc_64 sourced).
    assert valley == 64


def test_keys_expression_custom_depth_shifts_valley():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.expression",
        line,
        seed=1,
        parameters={"density": 1.0, "depth": 31},
    )
    valley = min(value for _, _, _, value in _collect_cc(out, 11))
    assert valley == 96, "127-31=96 (db_em_cc_96 sourced ~ -4.9 dB)"


def test_keys_expression_ignores_drum_channel():
    line = _make_line([(0, 480, 38, 96)], channel=9)
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 1.0},
    )
    assert _collect_cc(out, 11) == [], "canal 9 (bateria) nao recebe CC11 por keys.expression"


def test_keys_expression_rejects_depth_beyond_default_cc11():
    line = _make_line([(0, 480, 60, 96)])
    with pytest.raises(ValueError, match="default_cc11"):
        apply_technique(
            "keys.expression",
            line,
            seed=1,
            parameters={"density": 1.0, "depth": 128},
        )


def test_keys_expression_is_deterministic_for_same_seed():
    def render() -> list[tuple[int, int, int, int]]:
        line = _make_line([
            (0, 240, 60, 96),
            (240, 240, 62, 96),
            (480, 240, 64, 96),
        ])
        out = apply_technique(
            "keys.expression", line, seed=7,
            parameters={"density": 0.5},
        )
        return _collect_cc(out, 11)

    assert render() == render()


def test_keys_expression_is_idempotent_on_reapply_same_seed():
    line = _make_line([(0, 480, 60, 96)])
    first = apply_technique(
        "keys.expression", line, seed=3, parameters={"density": 1.0},
    )
    first_cc11 = _collect_cc(first, 11)

    second = apply_technique(
        "keys.expression", first, seed=3, parameters={"density": 1.0},
    )
    assert _collect_cc(second, 11) == first_cc11


def test_keys_expression_does_not_touch_structural_notes():
    events = [(0, 480, 60, 96), (480, 480, 62, 96)]
    line = _make_line(events)
    before = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
              for n in _collect_notes(line)]
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 1.0},
    )
    after = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
             for n in _collect_notes(out)]
    assert before == after


def test_keys_expression_event_rate_below_manual_ceiling():
    line = _make_line(
        [(0, 480, 60, 96)],
        ticks_per_beat=480,
        tempo_us=500_000,
    )
    out = apply_technique(
        "keys.expression", line, seed=1, parameters={"density": 1.0},
    )
    cc11 = _collect_cc(out, 11)
    if len(cc11) >= 2:
        first_tick = cc11[0][1]
        last_tick = cc11[-1][1]
        seconds = (last_tick - first_tick) * 500_000 / (480 * 1_000_000)
        if seconds > 0:
            rate = len(cc11) / seconds
            assert rate <= 1042, f"taxa {rate:.1f} eventos/s excede teto do manual"
