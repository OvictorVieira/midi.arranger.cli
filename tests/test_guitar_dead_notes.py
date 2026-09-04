"""Testes de `guitar.dead_notes` — transiente da palheta entre chugs."""

from __future__ import annotations

import mido

from tools.techniques.engine import (
    SUPPORTED_TECHNIQUES,
    apply_technique,
    get_technique,
)


def _make_guitar_line(
    events: list[tuple[int, int, int]],
    *,
    ticks_per_beat: int = 480,
    channel: int = 1,
) -> mido.MidiFile:
    """events: list of (start_tick_absolute, duration_ticks, pitch)."""

    mid = mido.MidiFile(ticks_per_beat=ticks_per_beat)
    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Guitar", time=0))
    absolute: list[tuple[int, int, mido.Message]] = []
    order = 0
    for start, duration, pitch in events:
        absolute.append((
            start, order,
            mido.Message("note_on", channel=channel, note=pitch, velocity=96, time=0),
        ))
        order += 1
        absolute.append((
            start + duration, order,
            mido.Message("note_off", channel=channel, note=pitch, velocity=0, time=0),
        ))
        order += 1
    prev = 0
    for absolute_tick, _order, msg in sorted(absolute, key=lambda item: (item[0], item[1])):
        track.append(msg.copy(time=absolute_tick - prev))
        prev = absolute_tick
    mid.tracks.append(track)
    return mid


def _structural_signature(mid: mido.MidiFile) -> list[tuple[int, int, int, int]]:
    out: list[tuple[int, int, int, int]] = []
    for track in mid.tracks:
        tick = 0
        pending: dict[tuple[int, int], list[int]] = {}
        for msg in track:
            tick += msg.time
            if msg.is_meta:
                continue
            if msg.type == "note_on" and msg.velocity > 0:
                pending.setdefault((msg.channel, msg.note), []).append(tick)
            elif msg.type == "note_off" or (
                msg.type == "note_on" and msg.velocity == 0
            ):
                stack = pending.get((msg.channel, msg.note))
                if stack:
                    start = stack.pop(0)
                    out.append((msg.channel, msg.note, start, tick))
    return out


def _riff_events(bars: int = 4, ticks_per_beat: int = 480) -> list[tuple[int, int, int]]:
    """Chug em backbeat de 1 e 3 (dois golpes por compasso), 4/4."""
    events = []
    for bar in range(bars):
        base = bar * ticks_per_beat * 4
        events.append((base, ticks_per_beat // 4, 40))
        events.append((base + 2 * ticks_per_beat, ticks_per_beat // 4, 40))
    return events


def test_guitar_dead_notes_is_registered_as_supported():
    assert "guitar.dead_notes" in SUPPORTED_TECHNIQUES
    entry = get_technique("guitar.dead_notes")
    assert entry.canonical == "guitar.dead_notes"
    assert entry.level == "technique"


def test_guitar_dead_notes_density_zero_is_no_op():
    source = _make_guitar_line(_riff_events())
    before = _structural_signature(source)

    out = apply_technique(
        "guitar.dead_notes", source, seed=1, tool="generic",
        parameters={"density": 0.0},
    )

    assert _structural_signature(out) == before


def test_guitar_dead_notes_adds_ornament_notes_between_structural_notes():
    source = _make_guitar_line(_riff_events())
    before_count = len(_structural_signature(source))

    out = apply_technique(
        "guitar.dead_notes", source, seed=2, tool="generic",
        parameters={"density": 1.0},
    )
    after = _structural_signature(out)

    assert len(after) > before_count


def test_guitar_dead_notes_never_removes_structural_notes():
    source = _make_guitar_line(_riff_events())
    before = set(_structural_signature(source))

    out = apply_technique(
        "guitar.dead_notes", source, seed=3, tool="generic",
        parameters={"density": 1.0},
    )
    after = set(_structural_signature(out))

    assert before <= after


def test_guitar_dead_notes_inherits_pitch_from_preceding_note():
    # Pitch do dead note = pitch da nota estrutural anterior (mesma corda).
    source = _make_guitar_line(_riff_events())
    original_pitches = {p for _c, p, _s, _e in _structural_signature(source)}

    out = apply_technique(
        "guitar.dead_notes", source, seed=4, tool="generic",
        parameters={"density": 1.0},
    )
    after_pitches = {p for _c, p, _s, _e in _structural_signature(out)}

    assert after_pitches == original_pitches


def test_guitar_dead_notes_uses_low_velocity_from_manual_range():
    source = _make_guitar_line(_riff_events())
    out = apply_technique(
        "guitar.dead_notes", source, seed=5, tool="generic",
        parameters={"density": 1.0},
    )

    velocities = [
        msg.velocity
        for track in out.tracks
        for msg in track
        if not msg.is_meta and msg.type == "note_on" and msg.velocity > 0
    ]
    # velocity CONVENCAO do manual e [15, 35]; note-on estruturais sao 96.
    ornament_velocities = [v for v in velocities if v != 96]
    assert ornament_velocities
    assert all(15 <= v <= 35 for v in ornament_velocities)


def test_guitar_dead_notes_is_deterministic_for_same_seed():
    src_a = _make_guitar_line(_riff_events())
    src_b = _make_guitar_line(_riff_events())
    out_a = apply_technique(
        "guitar.dead_notes", src_a, seed=9, tool="generic",
        parameters={"density": 0.5},
    )
    out_b = apply_technique(
        "guitar.dead_notes", src_b, seed=9, tool="generic",
        parameters={"density": 0.5},
    )

    assert _structural_signature(out_a) == _structural_signature(out_b)


def test_guitar_dead_notes_is_idempotent():
    source = _make_guitar_line(_riff_events())
    once = apply_technique(
        "guitar.dead_notes", source, seed=13, tool="generic",
        parameters={"density": 1.0},
    )
    twice = apply_technique(
        "guitar.dead_notes", once, seed=13, tool="generic",
        parameters={"density": 1.0},
    )

    assert _structural_signature(once) == _structural_signature(twice)
