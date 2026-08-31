"""Testes de `keys.damper_pedal` — CC64 binario, half-damper so com opt-in."""

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


def test_keys_damper_pedal_is_registered_as_supported():
    assert "keys.damper_pedal" in SUPPORTED_TECHNIQUES
    entry = get_technique("keys.damper_pedal")
    assert entry.canonical == "keys.damper_pedal"
    assert entry.level == "technique"


def test_keys_damper_pedal_without_density_is_noop():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique("keys.damper_pedal", line, seed=1)
    assert _collect_cc(out, 64) == []


def test_keys_damper_pedal_zero_density_is_noop():
    line = _make_line([(0, 480, 60, 96)])
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 0.0},
    )
    assert _collect_cc(out, 64) == []


def test_keys_damper_pedal_emits_binary_values_only():
    line = _make_line([(0, 480, 60, 96), (480, 480, 62, 96), (960, 480, 64, 96)])
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 1.0},
    )
    cc64 = _collect_cc(out, 64)
    assert cc64, "CC64 tem que aparecer quando density > 0"
    values = {value for _, _, _, value in cc64}
    assert values <= {0, 127}, "por default so 0 (OFF) e 127 (ON) — binario"


def test_keys_damper_pedal_never_pressed_before_note_on():
    line = _make_line([(0, 480, 60, 96), (480, 480, 62, 96)])
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 1.0},
    )
    cc64 = _collect_cc(out, 64)
    # Notes at tick 0 and 480. Nenhum CC64 pode cair NO tick de um note-on.
    onset_ticks = {0, 480}
    for _, tick, _, _ in cc64:
        assert tick not in onset_ticks, (
            f"CC64 no tick {tick} colide com note-on — deve cair depois"
        )


def test_keys_damper_pedal_releases_at_end_of_last_note():
    line = _make_line([(0, 480, 60, 96), (480, 480, 62, 96)])
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 1.0},
    )
    cc64 = _collect_cc(out, 64)
    assert cc64[-1][3] == 0, "pedal nao pode ficar pendurado no fim da track"
    last_note_end = max(n["end"] for n in _collect_notes(out))
    assert cc64[-1][1] == last_note_end


def test_keys_damper_pedal_pattern_release_then_repress_after_first_press():
    line = _make_line([
        (0, 480, 60, 96),
        (480, 480, 62, 96),
        (960, 480, 64, 96),
    ])
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 1.0},
    )
    values = [value for _, _, _, value in _collect_cc(out, 64)]
    # Sequencia esperada: ON, (OFF, ON)+, OFF (release final).
    assert values[0] == 127
    assert values[-1] == 0
    # Alternancia sem dois iguais consecutivos.
    for a, b in zip(values, values[1:], strict=False):
        assert a != b, f"sequencia com valor repetido: {values}"


def test_keys_damper_pedal_ignores_drum_channel():
    line = _make_line([(0, 480, 38, 96)], channel=9)
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 1.0},
    )
    assert _collect_cc(out, 64) == []


def test_keys_damper_pedal_rejects_partial_press_without_opt_in():
    line = _make_line([(0, 480, 60, 96)])
    with pytest.raises(ValueError, match="half_pedal_supported"):
        apply_technique(
            "keys.damper_pedal",
            line,
            seed=1,
            parameters={"density": 1.0, "press_value": 90},
        )


def test_keys_damper_pedal_accepts_partial_press_with_opt_in():
    line = _make_line([(0, 480, 60, 96), (480, 480, 62, 96)])
    out = apply_technique(
        "keys.damper_pedal",
        line,
        seed=1,
        parameters={
            "density": 1.0,
            "half_pedal_supported": True,
            "press_value": 90,
        },
    )
    values = {value for _, _, _, value in _collect_cc(out, 64)}
    assert values <= {0, 90}


def test_keys_damper_pedal_rejects_press_value_below_limiar_on_min():
    line = _make_line([(0, 480, 60, 96)])
    with pytest.raises(ValueError, match=r"\[64, 127\]"):
        apply_technique(
            "keys.damper_pedal",
            line,
            seed=1,
            parameters={
                "density": 1.0,
                "half_pedal_supported": True,
                "press_value": 40,
            },
        )


def test_keys_damper_pedal_rejects_press_value_above_127():
    line = _make_line([(0, 480, 60, 96)])
    with pytest.raises(ValueError, match=r"\[64, 127\]"):
        apply_technique(
            "keys.damper_pedal",
            line,
            seed=1,
            parameters={
                "density": 1.0,
                "half_pedal_supported": True,
                "press_value": 200,
            },
        )


def test_keys_damper_pedal_does_not_touch_structural_notes():
    events = [(0, 480, 60, 96), (480, 480, 62, 96)]
    line = _make_line(events)
    before = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
              for n in _collect_notes(line)]
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 1.0},
    )
    after = [(n["channel"], n["pitch"], n["start"], n["end"], n["velocity"])
             for n in _collect_notes(out)]
    assert before == after


def test_keys_damper_pedal_is_deterministic_for_same_seed():
    def render() -> list[tuple[int, int, int, int]]:
        line = _make_line([
            (0, 240, 60, 96),
            (240, 240, 62, 96),
            (480, 240, 64, 96),
            (720, 240, 65, 96),
        ])
        out = apply_technique(
            "keys.damper_pedal", line, seed=7,
            parameters={"density": 0.5},
        )
        return _collect_cc(out, 64)

    assert render() == render()


def test_keys_damper_pedal_is_idempotent_on_reapply_same_seed():
    line = _make_line([(0, 480, 60, 96), (480, 480, 62, 96)])
    first = apply_technique(
        "keys.damper_pedal", line, seed=3, parameters={"density": 1.0},
    )
    first_cc64 = _collect_cc(first, 64)

    second = apply_technique(
        "keys.damper_pedal", first, seed=3, parameters={"density": 1.0},
    )
    assert _collect_cc(second, 64) == first_cc64


def test_keys_damper_pedal_end_of_track_stays_last_after_final_release():
    line = _make_line([(0, 480, 60, 96), (480, 480, 62, 96)])
    out = apply_technique(
        "keys.damper_pedal", line, seed=1, parameters={"density": 1.0},
    )
    for track in out.tracks:
        end_meta = [m for m in track if m.is_meta and m.type == "end_of_track"]
        if not end_meta:
            continue
        assert track[-1].type == "end_of_track", (
            "end_of_track precisa ficar por ultimo (sort_and_flush)"
        )
