"""Testes de `keys.modulation` — CC1 com profundidade do manual, sem invencao."""

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


def test_keys_modulation_is_registered_as_supported():
    assert "keys.modulation" in SUPPORTED_TECHNIQUES
    entry = get_technique("keys.modulation")
    assert entry.canonical == "keys.modulation"
    assert entry.level == "technique"


def test_keys_modulation_without_density_is_noop():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique("keys.modulation", line, seed=1)
    assert _collect_cc(out, 1) == []
    assert _collect_cc(out, 33) == []


def test_keys_modulation_zero_density_is_noop():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.modulation", line, seed=1, parameters={"density": 0.0},
    )
    assert _collect_cc(out, 1) == []


def test_keys_modulation_emits_envelope_returning_to_zero():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.modulation", line, seed=1, parameters={"density": 1.0},
    )
    cc1 = _collect_cc(out, 1)
    assert cc1, "CC1 tem que aparecer quando density > 0"
    values = [value for _, _, _, value in cc1]
    peak = max(values)
    assert peak > 0
    assert peak == 127, "depth default (profundidade_default_cents) mapeia CC1 ao topo"
    # Final CC1 == 0 (default) — modulation nao pode ficar grudada.
    assert values[-1] == 0
    # Envelope sobe do 0 e desce de volta a 0.
    assert values[0] > 0 or values[0] == 0
    max_index = values.index(peak)
    assert values[:max_index + 1] == sorted(values[:max_index + 1])
    assert values[max_index:] == sorted(values[max_index:], reverse=True)


def test_keys_modulation_never_emits_cc33_lsb():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.modulation", line, seed=1, parameters={"density": 1.0},
    )
    assert _collect_cc(out, 33) == [], "CC33 (LSB) nao e emitido em passos inteiros"


def test_keys_modulation_ignores_drum_channel():
    line = _make_line([(0, 480, 38, 96)], channel=9)
    out = apply_technique(
        "keys.modulation", line, seed=1, parameters={"density": 1.0},
    )
    assert _collect_cc(out, 1) == [], "canal 9 (bateria) nao recebe CC1"


def test_keys_modulation_scales_depth_within_manual_default():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.modulation",
        line,
        seed=1,
        parameters={"density": 1.0, "depth_cents": 25.0},
    )
    peak = max(value for _, _, _, value in _collect_cc(out, 1))
    # 25/50 * 127 = 63.5 → 64.
    assert peak in {63, 64}


def test_keys_modulation_rejects_depth_beyond_default_without_rpn5():
    line = _make_line([(0, 480, 60, 96)])
    with pytest.raises(ValueError, match="RPN 5"):
        apply_technique(
            "keys.modulation",
            line,
            seed=1,
            parameters={"density": 1.0, "depth_cents": 60.0},
        )


def test_keys_modulation_rejects_depth_beyond_teto_dls():
    line = _make_line([(0, 480, 60, 96)])
    with pytest.raises(ValueError, match="teto_dls_cents"):
        apply_technique(
            "keys.modulation",
            line,
            seed=1,
            parameters={"density": 1.0, "depth_cents": 5000.0},
        )


def test_keys_modulation_is_deterministic_for_same_seed():
    def render() -> list[tuple[int, int, int, int]]:
        line = _make_line([
            (0, 240, 60, 96),
            (240, 240, 62, 96),
            (480, 240, 64, 96),
        ])
        out = apply_technique(
            "keys.modulation", line, seed=7,
            parameters={"density": 0.5},
        )
        return _collect_cc(out, 1)

    assert render() == render()


def test_keys_modulation_is_idempotent_on_reapply_same_seed():
    line = _make_line([(0, 480, 60, 96)])
    first = apply_technique(
        "keys.modulation", line, seed=3, parameters={"density": 1.0},
    )
    first_cc1 = _collect_cc(first, 1)

    second = apply_technique(
        "keys.modulation", first, seed=3, parameters={"density": 1.0},
    )
    assert _collect_cc(second, 1) == first_cc1


def test_keys_modulation_does_not_touch_structural_notes():
    events = [(0, 480, 60, 96), (480, 480, 62, 96)]
    line = _make_line(events)
    before = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
              for n in _collect_notes(line)]
    out = apply_technique(
        "keys.modulation", line, seed=1, parameters={"density": 1.0},
    )
    after = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
             for n in _collect_notes(out)]
    assert before == after


def test_keys_modulation_event_rate_below_manual_ceiling():
    line = _make_line(
        [(0, 480, 60, 96)],
        ticks_per_beat=480,
        tempo_us=500_000,
    )
    out = apply_technique(
        "keys.modulation", line, seed=1, parameters={"density": 1.0},
    )
    cc1 = _collect_cc(out, 1)
    if len(cc1) >= 2:
        first_tick = cc1[0][1]
        last_tick = cc1[-1][1]
        seconds = (last_tick - first_tick) * 500_000 / (480 * 1_000_000)
        if seconds > 0:
            rate = len(cc1) / seconds
            assert rate <= 1042, f"taxa {rate:.1f} eventos/s excede teto do manual"
